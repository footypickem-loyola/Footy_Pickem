"""Read-only local reference dataset report."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from football_reference import inspect_reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(inspect_reference(args.db), indent=2, sort_keys=True))
    except Exception:
        raise SystemExit("Reference inspection failed; check the explicit local database and schema") from None


if __name__ == "__main__":
    main()
