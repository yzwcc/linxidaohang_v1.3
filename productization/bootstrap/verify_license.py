#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_DIR = SCRIPT_DIR.parent / "common"
if (SCRIPT_DIR / "device_identity.py").exists():
    sys.path.insert(0, str(SCRIPT_DIR))
elif COMMON_DIR.exists():
    sys.path.insert(0, str(COMMON_DIR))
else:
    sys.path.insert(0, "/usr/lib/sparkrobot-license")

from device_identity import PRODUCT_ID, device_fingerprint  # noqa: E402


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def verify_signature(license_path: Path, signature_path: Path, public_key: Path) -> None:
    result = subprocess.run(
        [
            os.environ.get("OPENSSL_BIN", "openssl"),
            "dgst",
            "-sha256",
            "-verify",
            str(public_key),
            "-signature",
            str(signature_path),
            str(license_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or "Verified OK" not in result.stdout:
        raise RuntimeError("许可证签名无效或许可证已被修改")


def verify_license(
    license_path: Path,
    signature_path: Path,
    public_key: Path,
    *,
    product_id: str,
    required_features: list[str],
) -> dict[str, object]:
    verify_signature(license_path, signature_path, public_key)
    data = json.loads(license_path.read_text(encoding="utf-8"))
    if data.get("format") != "sparkrobot-license-v1":
        raise RuntimeError("许可证格式不受支持")
    if data.get("product_id") != product_id:
        raise RuntimeError("许可证不属于当前产品")
    expected = device_fingerprint(product_id=product_id)
    if data.get("device_fingerprint") != expected:
        raise RuntimeError("许可证与当前机器人不匹配")
    if data.get("architecture") not in {"aarch64", "arm64"}:
        raise RuntimeError("许可证不是 ARM64 机器人许可证")

    now = datetime.now(timezone.utc)
    not_before = parse_time(str(data.get("not_before") or ""))
    expires_at = parse_time(str(data.get("expires_at") or ""))
    if not_before and now < not_before:
        raise RuntimeError("许可证尚未生效")
    if expires_at and now >= expires_at:
        raise RuntimeError("许可证已过期")

    features = set(str(item) for item in data.get("features", []))
    missing = sorted(set(required_features) - features)
    if missing:
        raise RuntimeError(f"许可证缺少功能授权：{', '.join(missing)}")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="验证灵犀导航设备许可证")
    parser.add_argument("--license", required=True)
    parser.add_argument("--signature", required=True)
    parser.add_argument("--public-key", required=True)
    parser.add_argument("--product-id", default=PRODUCT_ID)
    parser.add_argument("--require-feature", action="append", default=[])
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    try:
        license_data = verify_license(
            Path(args.license),
            Path(args.signature),
            Path(args.public_key),
            product_id=args.product_id,
            required_features=args.require_feature,
        )
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"许可证校验失败：{exc}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(
            "许可证有效："
            f"license_id={license_data.get('license_id')}，"
            f"customer={license_data.get('customer')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
