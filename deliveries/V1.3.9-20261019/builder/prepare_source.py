"""Capture the audited V1.3.9 baseline without editing its working tree."""
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASELINE = ROOT / "deliveries/V1.3.9-20261015"
BASE_INNER = BASELINE / ".build/sparkrobot-lingxi-nav-internal-1.0.21-20261015"
WORKTREE = ROOT.with_name(ROOT.name + "-v1.3.9")


def prepare():
    destination = HERE.parent / "source"
    if destination.exists():
        raise RuntimeError("Source snapshot already exists; refusing to overwrite")
    shutil.copytree(BASE_INNER / "source", destination)
    shutil.copytree(
        WORKTREE / "server/test", destination / "server/test",
        ignore=shutil.ignore_patterns("artifacts"),
    )
    shutil.copytree(BASE_INNER / "scripts", HERE / "scripts")
    for name in ("install.sh", "configure.sh", "control.sh", "quick_install.sh"):
        shutil.copy2(BASE_INNER / name, HERE / name)
    for name in ("systemd", "templates"):
        shutil.copytree(BASE_INNER / name, HERE / name)
    outer = BASELINE / "lingxi-v1.3.9-complete-20261015-r3-deployment"
    shutil.copy2(outer / "install_all.sh", HERE / "install_all.sh")
    shutil.copy2(outer / "control_all.sh", HERE / "control_all.sh")
    shutil.copytree(
        outer / "docs", HERE / "docs",
    )
    for name in (
        "00-install-environment.sh", "01-replace-aimdk.sh",
        "03-verify-after-web-start.sh", "04-check-localization.sh",
    ):
        shutil.copy2(outer / "scripts" / name, HERE / "scripts" / name)
    shutil.copy2(BASELINE / "builder/test_delivery.py", HERE / "test_delivery.py")
    print("Captured V1.3.9 sources, tests and installers:", destination)


if __name__ == "__main__":
    prepare()
