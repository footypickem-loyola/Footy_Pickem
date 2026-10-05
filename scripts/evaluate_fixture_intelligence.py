"""Repeatable read-only retrospective JSON evaluation, without Flask imports."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_intelligence_eval import evaluate, serialize, utc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--season', required=True, type=int, help='Official database season ID')
    parser.add_argument('--week', action='append', type=int)
    parser.add_argument('--fixture-id', action='append', type=int)
    parser.add_argument('--start', type=utc, help='Inclusive ISO date/time')
    parser.add_argument('--end', type=utc, help='Exclusive ISO date/time')
    parser.add_argument('--as-of', type=utc)
    parser.add_argument('--gold', type=Path)
    parser.add_argument('--supplemental-gold', type=Path, help='Separate secondary seed; never pooled with primary metrics')
    parser.add_argument('--reviews', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    inputs = [Path(args.db), *(p for p in (args.gold, args.reviews, args.supplemental_gold) if p)]
    if args.output and any(args.output.resolve() == p.resolve() or
                           (args.output.exists() and p.exists() and args.output.samefile(p)) for p in inputs):
        parser.error('Output must not overwrite a database or review input')
    load = lambda path: json.loads(path.read_text(encoding='utf-8')) if path else None
    result = evaluate(args.db, season=args.season, weeks=args.week, fixture_ids=args.fixture_id,
                      start=args.start, end=args.end, as_of=args.as_of,
                      gold=load(args.gold), reviews=load(args.reviews), supplemental_gold=load(args.supplemental_gold))
    content = serialize(result)
    if args.output:
        args.output.write_text(content, encoding='utf-8')
    else:
        print(content, end='')
    return result


if __name__ == '__main__':
    main()
