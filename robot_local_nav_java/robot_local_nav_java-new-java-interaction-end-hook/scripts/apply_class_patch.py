#!/usr/bin/env python3
import os
import sys
import tempfile
import zipfile


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: apply_class_patch.py TARGET_JAR PATCH_JAR")

    target_path, patch_path = map(os.path.abspath, sys.argv[1:])
    with zipfile.ZipFile(patch_path, "r") as patch_zip:
        replacements = {
            info.filename: (info, patch_zip.read(info.filename))
            for info in patch_zip.infolist()
            if not info.is_dir()
        }

    target_dir = os.path.dirname(target_path)
    fd, temporary_path = tempfile.mkstemp(prefix=".java-agent-", suffix=".jar", dir=target_dir)
    os.close(fd)
    try:
        with zipfile.ZipFile(target_path, "r") as source_zip, zipfile.ZipFile(
            temporary_path, "w", allowZip64=True
        ) as output_zip:
            for info in source_zip.infolist():
                if info.filename not in replacements:
                    output_zip.writestr(info, source_zip.read(info.filename))
            for info, data in replacements.values():
                output_zip.writestr(info, data)

        with zipfile.ZipFile(temporary_path, "r") as result_zip:
            missing = set(replacements) - set(result_zip.namelist())
            if missing:
                raise RuntimeError(f"patched JAR is missing entries: {sorted(missing)}")
            bad_entry = result_zip.testzip()
            if bad_entry:
                raise RuntimeError(f"patched JAR failed CRC check: {bad_entry}")
        os.replace(temporary_path, target_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


if __name__ == "__main__":
    main()
