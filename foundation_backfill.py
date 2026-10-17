"""Read-only Stage 2 inspection and evidence-manifest validation.

No apply/backfill command exists. Output contains fixed codes, IDs, counts and
hashes, never data rows, recipient addresses, SQL definitions or exception text.
A valid evidence manifest is not a complete plan or permission to execute.
"""
import argparse
import json

from foundation_backfill_manifest import ManifestError, load_manifest
from foundation_backfill_validation import InspectionError, inspect_database


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default diagnostics can echo unknown arguments or paths.
        print(json.dumps({"status": "refused", "code": "INVALID_ARGUMENTS"}))
        raise SystemExit(2)


def main(argv=None):
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_subparsers(dest="command", required=True, parser_class=SafeParser)
    inspector = modes.add_parser("inspect", allow_abbrev=False)
    inspector.add_argument("--database", required=True)
    validator = modes.add_parser("validate-manifest", allow_abbrev=False)
    validator.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)
    try:
        report = inspect_database(args.database) if args.command == "inspect" else load_manifest(args.manifest)
    except (InspectionError, ManifestError) as exc:
        print(json.dumps({"status": "refused", "code": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 0 if report["status"] in {"valid", "inspected"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
