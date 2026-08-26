from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path
from typing import Callable


PRODUCT_ID = "sparkrobot-lingxi-nav"
IDENTITY_PATHS = (
    "/sys/firmware/devicetree/base/serial-number",
    "/proc/device-tree/serial-number",
    "/sys/class/dmi/id/product_uuid",
    "/etc/machine-id",
)


def _read_identity(path: str, reader: Callable[[str], bytes] | None = None) -> bytes:
    try:
        value = reader(path) if reader else Path(path).read_bytes()
    except OSError:
        return b""
    return value.strip(b"\x00\r\n \t")


def identity_hashes(reader: Callable[[str], bytes] | None = None) -> dict[str, str]:
    result: dict[str, str] = {}
    seen: set[bytes] = set()
    for path in IDENTITY_PATHS:
        value = _read_identity(path, reader)
        if not value or value in seen:
            continue
        seen.add(value)
        result[path] = hashlib.sha256(value).hexdigest()
    return result


def device_fingerprint(
    *,
    product_id: str = PRODUCT_ID,
    reader: Callable[[str], bytes] | None = None,
    architecture: str | None = None,
) -> str:
    hashes = identity_hashes(reader)
    if not hashes:
        raise RuntimeError("未找到可用于设备绑定的稳定硬件标识")
    selected_path = next(path for path in IDENTITY_PATHS if path in hashes)
    payload = {
        "architecture": architecture or platform.machine().lower(),
        "identity": hashes[selected_path],
        "product_id": product_id,
    }
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def device_claim(*, product_id: str = PRODUCT_ID) -> dict[str, object]:
    hashes = identity_hashes()
    return {
        "format": "sparkrobot-device-claim-v1",
        "product_id": product_id,
        "device_fingerprint": device_fingerprint(product_id=product_id),
        "architecture": platform.machine().lower(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "identity_hashes": hashes,
    }
