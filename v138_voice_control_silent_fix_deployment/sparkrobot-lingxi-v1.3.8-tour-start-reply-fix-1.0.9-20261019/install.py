#!/usr/bin/env python3
"""Use the tested cumulative transaction with an isolated rollback directory."""
from pathlib import Path
import arrival_runtime as runtime
from arrival_runtime import *

HERE = Path(__file__).resolve().parent


class Installer(runtime.Installer):
    def __init__(self, root=Path("/"), package=HERE):
        super().__init__(root, package)
        self.backups = self.path("/var/lib/sparkrobot/lingxi-voice-control-silent-fix/backups")

    def validate(self):
        base.Installer.validate(self)
        for relative, allowed in self.meta.get("python_accepted_sha256", {}).items():
            target = self.safe_target(relative)
            actual = digest(regular(target)) if target.exists() else None
            if actual not in allowed and actual != self.meta["payload_sha256"][relative]:
                raise RuntimeError("Python 文件不是已验证版本：" + relative)
            self.meta["python_baselines"][relative] = actual
        return super().validate()


if __name__ == "__main__":
    try:
        runtime.Installer = Installer
        runtime.main()
    except Exception as error:
        print("失败：" + str(error), file=__import__("sys").stderr)
        raise SystemExit(1)
