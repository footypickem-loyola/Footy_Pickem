"""Inspect a local committed slate; generate only with explicit --generate."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pre_match_brief import build_pre_match_context, serialize, CorrespondentError
from correspondent.pre_match_writer import build_pre_match_request, generate_pre_match_brief


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path, help='Explicit local/disposable SQLite snapshot')
    parser.add_argument('--season', required=True, type=int, help='Season ID')
    parser.add_argument('--week', required=True, type=int, help='Matchweek number')
    parser.add_argument('--player-id', required=True, type=int)
    parser.add_argument('--as-of', required=True, help='Explicit ISO time; naive means UTC')
    parser.add_argument('--candidate-limit', type=int, default=5, choices=range(1, 6))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--context-only', action='store_true', help='Authoritative JSON; never calls OpenAI')
    mode.add_argument('--request-only', action='store_true', help='Exact request including prompt/schema; never calls OpenAI')
    mode.add_argument('--generate', action='store_true', help='Explicitly call OpenAI using locally supplied credentials')
    parser.add_argument('--model', help='Override existing OPENAI_MODEL/default convention')
    parser.add_argument('--output', type=Path, help='Optional local .json output; never saved automatically')
    args = parser.parse_args(argv)
    if args.output and (args.output.suffix.lower() != '.json' or args.output.resolve() == args.db.resolve() or
                        (args.output.exists() and args.db.exists() and args.output.samefile(args.db))):
        parser.error('Output must be a .json file distinct from the source database')
    try:
        context = build_pre_match_context(args.db, season=args.season, week=args.week,
            player_id=args.player_id, as_of=args.as_of, candidate_limit=args.candidate_limit)
        if args.context_only:
            result = context
        elif args.request_only:
            result = build_pre_match_request(context, model=args.model)
        else:
            result = generate_pre_match_brief(context, model=args.model).to_dict()
        content = serialize(result)
        if args.output:
            args.output.write_text(content, encoding='utf-8')
        else:
            print(content, end='')
        return result
    except (CorrespondentError, OSError) as exc:
        parser.exit(2, f'Pre-match brief: {exc}\n')


if __name__ == '__main__':
    main()
