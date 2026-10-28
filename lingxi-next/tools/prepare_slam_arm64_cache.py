"""Download signed Ubuntu Jammy cross-build dependencies, never install them."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess

from build_slam_cpu_trial import package_inventory


KEYRING = Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg")
PACKAGES = {
    "amd64": ["g++-11-aarch64-linux-gnu"],
    "arm64": [
        "libc6-dev", "libeigen3-dev", "libboost-serialization-dev",
        "libboost-system-dev", "libboost-filesystem-dev", "libboost-thread-dev",
        "libboost-date-time-dev", "libboost-timer-dev", "libboost-chrono-dev",
        "libboost-program-options-dev", "libboost-regex-dev", "libboost-graph-dev",
        "libspdlog-dev", "libmetis-dev",
    ],
}


def apt_commands(directory, architecture):
    if architecture not in PACKAGES:
        raise ValueError("unsupported_cache_architecture")
    root = Path(directory)
    options = [
        "-o", "Dir::Etc::main=-", "-o", "Dir::Etc::parts=-",
        "-o", "Dir::Etc::sourcelist=" + str(root / "sources.list"),
        "-o", "Dir::Etc::sourceparts=-",
        "-o", "Dir::State::status=" + str(root / "status"),
        "-o", "Dir::State::lists=" + str(root / "lists"),
        "-o", "Dir::Cache::archives=" + str(root / "archives"),
        "-o", "Dir::Log=" + str(root / "logs"),
        "-o", "APT::Architecture=" + architecture,
        "-o", "APT::Architectures::=" + architecture,
        "-o", "Acquire::Languages=none", "-o", "Acquire::Retries=2",
        "-o", "Acquire::http::Timeout=30", "-o", "Acquire::https::Timeout=30",
        "-o", "Debug::NoLocking=1",
    ]
    return [
        ["apt-get"] + options + ["update"],
        ["apt-get"] + options + [
            "--download-only", "--yes", "--no-install-recommends", "install",
        ] + PACKAGES[architecture],
    ]


def sources(architecture):
    if architecture not in PACKAGES:
        raise ValueError("unsupported_cache_architecture")
    origin = (
        "https://archive.ubuntu.com/ubuntu"
        if architecture == "amd64" else "https://ports.ubuntu.com/ubuntu-ports"
    )
    return "".join(
        "deb [arch=" + architecture + " signed-by=" + str(KEYRING) + "] "
        + origin + " " + suite + " main universe\n"
        for suite in ("jammy", "jammy-updates", "jammy-security")
    )


def run(output):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("linux_x86_64_download_host_required")
    if os.geteuid() == 0:
        raise ValueError("ordinary_user_required")
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("existing_cache_is_preserved_use_new_directory")
    if not KEYRING.is_file():
        raise ValueError("official_ubuntu_archive_keyring_required")
    output.mkdir(mode=0o700, parents=True)
    report = {
        "purpose": "ubuntu_jammy_arm64_cross_build_download_only",
        "systemPackagesInstalled": False, "result": "running", "caches": {},
    }
    env = os.environ.copy()
    # Bootstrap before APT reads the host's apt.conf.d, including local hooks.
    bootstrap = output / "apt-bootstrap.conf"
    bootstrap.write_text('Dir::Etc::main "-";\nDir::Etc::parts "-";\n', encoding="ascii")
    env["APT_CONFIG"] = str(bootstrap)
    try:
        for architecture in PACKAGES:
            root = output / architecture
            for name in ("lists/partial", "archives/partial", "logs"):
                (root / name).mkdir(parents=True)
            (root / "status").touch()
            (root / "sources.list").write_text(sources(architecture), encoding="utf-8")
            for index, command in enumerate(apt_commands(root, architecture)):
                print("Downloading Jammy " + architecture + " stage " + str(index), flush=True)
                with (root / ("apt-" + str(index) + ".log")).open("w") as log:
                    subprocess.run(
                        command, check=True, env=env, stdout=log, stderr=subprocess.STDOUT,
                    )
            packages = package_inventory(root / "archives")
            if any(item["architecture"] not in (architecture, "all") for item in packages):
                raise ValueError("unexpected_download_architecture")
            report["caches"][architecture] = packages
        report["result"] = "downloaded_not_installed"
    except BaseException:
        report["result"] = "failed_or_interrupted"
        raise
    finally:
        (output / "download-report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8",
        )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.output)
    print(json.dumps({
        "result": report["result"],
        "packageCounts": {name: len(items) for name, items in report["caches"].items()},
    }))


if __name__ == "__main__":
    main()
