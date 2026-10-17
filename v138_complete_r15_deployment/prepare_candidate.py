"""Extract the immutable delivered r14 sources for the next product audit."""
import hashlib
import io
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "v138_complete_r14_deployment/dist/lingxi-v1.3.8-complete-20261017-r14-deployment.tar.gz"
BASE_SHA = "ac7f93f20d1ed2155e394b4bf216d8717476925d571582632d84b4081d05f0b0"


def main():
    raw = BASE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == BASE_SHA
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
            elif relative.startswith(("payload/java/", "payload/console/dist/")):
                target = HERE / "reference" / relative.removeprefix("payload/")
            else:
                continue
            assert not target.exists(), f"Refusing to overwrite {target}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(nav.extractfile(member).read())
            count += 1
    print(f"Extracted {count} immutable r14 baseline files")


if __name__ == "__main__":
    main()
