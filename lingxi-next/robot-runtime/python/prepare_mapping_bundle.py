"""Prepare, verify or extract the dedicated managed-mapping archive offline."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_bundle
from prepare_live_bundle import main


if __name__ == "__main__":
    os.umask(0o077)
    try:
        raise SystemExit(main(profile=live_bundle.MAPPING))
    except Exception:
        print("Lingxi Next managed mapping bundle operation failed; inspect private reports.", file=sys.stderr)
        raise SystemExit(1)
