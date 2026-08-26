#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "common"
sys.path.insert(0, str(COMMON_DIR))

from device_identity import PRODUCT_ID, device_claim  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="采集灵犀导航产品设备申请信息")
    parser.add_argument("--output", default="device-claim.json")
    parser.add_argument("--product-id", default=PRODUCT_ID)
    args = parser.parse_args()

    claim = device_claim(product_id=args.product_id)
    output = Path(args.output).resolve()
    output.write_text(
        json.dumps(claim, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    output.chmod(0o600)
    print(f"设备申请文件已生成：{output}")
    print(f"设备指纹：{claim['device_fingerprint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

