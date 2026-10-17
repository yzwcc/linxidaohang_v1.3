#!/usr/bin/env python3
"""Read Java inputs as the service user without starting the SDK or robot."""
from pathlib import Path
import sys
import zipfile


MAIN_CLASS = "com/agibot/aiem/nav/LingxiNavAgentApp.class"


def check_access(release: Path, config: Path) -> None:
    # Opening the files, rather than checking as root, also catches ACL issues.
    with config.open("rb") as source:
        source.read(1)
    for name in ("robot-local-navigation-java-all.jar", "linksoul-agentsdk-1.4.0.jar"):
        with zipfile.ZipFile(release / "java" / name) as archive:
            if name.startswith("robot-local"):
                archive.read(MAIN_CLASS)
            elif not archive.namelist():
                raise ValueError("Empty SDK JAR")


def main() -> int:
    try:
        check_access(Path(__file__).resolve().parents[1],
                     Path("/etc/sparkrobot/lingxi-nav/lingxi-nav.properties"))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"Java input check failed (service user): {error}. "
              "Run: sudo sparkrobot-lingxi-complete repair-permissions", file=sys.stderr)
        return 1
    print("JAVA_RUNTIME_ACCESS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
