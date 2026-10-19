#!/usr/bin/env python3
"""Cumulative silent-control patch using the verified arrival/TTS transaction."""
from pathlib import Path
import arrival_runtime as runtime
from arrival_runtime import *

HERE = Path(__file__).resolve().parent


class Installer(runtime.Installer):
    def __init__(self, root=Path("/"), package=HERE):
        super().__init__(root, package)
        self.backups = self.path("/var/lib/sparkrobot/lingxi-silent-control-fix/backups")

    def validate(self):
        # Bind the transaction to either the original Python baseline or 1.0.7.
        # Unknown field changes are still rejected before any service is stopped.
        base.Installer.validate(self)
        for relative, allowed in self.meta.get("python_accepted_sha256", {}).items():
            target = self.safe_target(relative)
            actual = digest(regular(target)) if target.exists() else None
            if actual not in allowed and actual != self.meta["payload_sha256"][relative]:
                raise RuntimeError("Python 文件不是已验证版本：" + relative)
            self.meta["python_baselines"][relative] = actual
        return super().validate()


def main():
    # The inherited CLI uses these names from its own module globals.
    runtime.Installer = Installer
    runtime.main()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("失败：" + str(error), file=__import__("sys").stderr)
        raise SystemExit(1)
