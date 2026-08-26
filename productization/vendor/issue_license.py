#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_FEATURES = ["console", "navigation", "voice", "tour", "map-editor"]
PRODUCT_ID = "sparkrobot-lingxi-nav"


def resolve_openssl(explicit: str | None) -> str:
    for candidate in (explicit, shutil.which("openssl"), r"C:\Program Files\Git\usr\bin\openssl.exe"):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise SystemExit("找不到 OpenSSL，请使用 --openssl 指定路径")


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(description="为单台机器人签发灵犀导航许可证")
    parser.add_argument("--claim", required=True)
    parser.add_argument("--private-key", required=True)
    parser.add_argument("--public-key")
    parser.add_argument("--customer", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--days", type=int, default=0, help="0 表示永久设备许可证")
    parser.add_argument("--feature", action="append", dest="features")
    parser.add_argument("--openssl")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    openssl = resolve_openssl(args.openssl)
    claim = json.loads(Path(args.claim).read_text(encoding="utf-8"))
    if claim.get("format") != "sparkrobot-device-claim-v1":
        raise SystemExit("设备申请文件格式不正确")
    if claim.get("product_id") != PRODUCT_ID:
        raise SystemExit("设备申请文件不属于灵犀导航产品")
    if claim.get("architecture") not in {"aarch64", "arm64"}:
        raise SystemExit("目标设备不是 ARM64 机器人")

    if args.days < 0:
        raise SystemExit("许可证有效天数不能为负数")
    customer = args.customer.strip()
    if not customer:
        raise SystemExit("客户名称不能为空")
    now = datetime.now(timezone.utc)
    license_data = {
        "format": "sparkrobot-license-v1",
        "product_id": claim["product_id"],
        "license_id": str(uuid.uuid4()),
        "customer": customer,
        "device_fingerprint": claim["device_fingerprint"],
        "architecture": "aarch64",
        "features": sorted(set(args.features or DEFAULT_FEATURES)),
        "issued_at": isoformat(now),
        "not_before": isoformat(now - timedelta(minutes=5)),
        "expires_at": isoformat(now + timedelta(days=args.days)) if args.days > 0 else None,
    }
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    license_path = output / "license.json"
    signature_path = output / "license.sig"
    if (license_path.exists() or signature_path.exists()) and not args.force:
        raise SystemExit(f"许可证输出已存在，拒绝覆盖：{output}")
    license_path.write_text(
        json.dumps(license_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [openssl, "dgst", "-sha256", "-sign", args.private_key, "-out", str(signature_path), str(license_path)],
        check=True,
    )
    if args.public_key:
        subprocess.run(
            [openssl, "dgst", "-sha256", "-verify", args.public_key, "-signature", str(signature_path), str(license_path)],
            check=True,
        )
    print(f"许可证已签发：{license_path}")
    print(f"许可证签名：{signature_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
