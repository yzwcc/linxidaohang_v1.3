from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def atomic_write_text(path: str | Path, text: str, *, default_mode: int = 0o660) -> None:
    """Replace a UTF-8 text file without exposing a partial JSON document."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.stat() if target.exists() else None
    mode = stat.S_IMODE(existing.st_mode) if existing else default_mode
    shared_gid = existing.st_gid if existing else target.parent.stat().st_gid
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        if hasattr(os, "fchown"):
            # Replacing the inode must not discard access for sibling services.
            owner = existing.st_uid if existing and os.geteuid() == 0 else -1
            os.fchown(fd, owner, shared_gid)
        if hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
