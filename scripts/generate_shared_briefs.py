"""Explicit CLI: initialize separate store, prepare twenty slots, or generate."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--init-store', action='store_true')
    modes.add_argument('--prepare', action='store_true')
    modes.add_argument('--generate', action='store_true')
    parser.add_argument('--source')
    parser.add_argument('--season-year', type=int, default=2026)
    parser.add_argument('--matchweek', type=int)
    parser.add_argument('--content-version')
    parser.add_argument('--model')
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--retry-uncertain', action='store_true', help='Explicitly risk repeating a lost provider response; inspect first')
    args = parser.parse_args(argv)
    if args.init_store:
        from shared_brief_store import initialize
        initialize(args.store)
        return {'initialized': True}
    if not args.source or not args.matchweek or not args.content_version:
        parser.error('source, matchweek and content-version are required')
    from shared_brief_workflow import run_batch
    kwargs = dict(model=args.model) if args.model else {}
    return run_batch(args.source, args.store, season_year=args.season_year, matchweek=args.matchweek,
        content_version=args.content_version, generate=args.generate, retry_failed=args.retry_failed,
        retry_uncertain=args.retry_uncertain, **kwargs)


if __name__ == '__main__':
    try:
        result = main()
        print(json.dumps(result, sort_keys=True))
        if '--generate' in sys.argv and result != {'succeeded': 20}:
            sys.exit(2)
    except Exception as exc:
        print(f'Shared brief workflow failed: {type(exc).__name__}', file=sys.stderr)
        sys.exit(1)
