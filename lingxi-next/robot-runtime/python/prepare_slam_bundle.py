"""Prepare, verify or extract the source-bound shared SLAM ARM64 archive."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_bundle
from prepare_live_bundle import main


if __name__ == "__main__":
    os.umask(0o077)
    try:
        raise SystemExit(main(profile=live_bundle.SLAM))
    except Exception:
        print("Lingxi Next shared SLAM bundle operation failed; inspect private reports.", file=sys.stderr)
        raise SystemExit(1)
