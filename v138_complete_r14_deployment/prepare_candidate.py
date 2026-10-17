"""Extract only the reviewed r13 product sources and runtime control scripts."""
import hashlib
import io
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "v138_complete_r13_deployment/dist/lingxi-v1.3.8-complete-20261017-r13-deployment.tar.gz"


def main():
    raw = BASE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "c283bb8ca867e533e098443281aba93c6d5e8a8bac45b7c67081cf3d8cc5e7ed"
    with tarfile.open(fileobj=io.BytesIO(raw)) as outer:
        name = next(n for n in outer.getnames() if "/components/sparkrobot-lingxi-nav-internal-" in n)
        component = outer.extractfile(name).read()
    count = 0
    with tarfile.open(fileobj=io.BytesIO(component)) as nav:
        for member in nav:
            if not member.isfile():
                continue
            relative = member.name.split("/", 1)[1]
            if relative.startswith(("source/", "scripts/", "systemd/")) or relative in ("install.sh", "control.sh", "configure.sh"):
                target = HERE / "candidate" / relative
            elif relative.startswith("payload/java/"):
                target = HERE / "reference" / relative.removeprefix("payload/")
            elif relative.startswith("payload/console/dist/"):
                target = HERE / "reference" / relative.removeprefix("payload/")
            else:
                continue
            assert not target.exists(), f"Refusing to overwrite {target}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(nav.extractfile(member).read())
            count += 1
    print(f"Extracted {count} baseline files")


if __name__ == "__main__":
    main()
