"""Explicit batch orchestration only; never imported by Training Ground."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from correspondent.shared_team_writer import generate_team_brief, load_team_prompt
from correspondent.pre_match_writer import validate_fixture_output
from correspondent.writer import DEFAULT_MODEL, CorrespondentError
from pre_match_brief import require, utc
from shared_brief_context import build_round, validate_team_context, check_current_fixture, CONTEXT_VERSION, PROMPT_VERSION
from shared_brief_store import BriefStore, digest


def clock():
    return datetime.now(timezone.utc)


def run_batch(source, store_path, *, season_year, matchweek, content_version, model=DEFAULT_MODEL,
              generate=False, retry_failed=False, retry_uncertain=False, now=clock, writer=generate_team_brief):
    require(Path(source).resolve() != Path(store_path).resolve(), 'Content store must be separate from game/reference source')
    require(isinstance(model, str) and model.strip() == model and bool(model), 'Explicit nonempty model required')
    store = BriefStore(store_path)
    try:
        batch = store.batch(season_year, matchweek, content_version)
        cutoff = batch['as_of'] if batch else utc(now()).isoformat()
        prompt_sha = sha256(load_team_prompt().encode()).hexdigest()
        slots = build_round(source, season_year=season_year, matchweek=matchweek, as_of=cutoff)
        store.prepare(slots, season_year=season_year, matchweek=matchweek, version=content_version,
                      as_of=cutoff, model=model, prompt_sha=prompt_sha)
        if generate:
            for slot in slots:
                claim = store.claim(slot['identity'], content_version, now(),
                                    retry_failed=retry_failed, retry_uncertain=retry_uncertain)
                if claim is None:
                    continue
                try:
                    context = validate_team_context(claim['context'])
                    check_current_fixture(source, slot)
                    if utc(now()) >= utc(slot['kickoff']):
                        store.finish(slot['identity'], content_version, claim['token'], now(), error='kickoff_passed')
                        continue
                    result = writer(context, model=model)
                    validate_fixture_output(result['output'], context['fixture'])
                    require(result['identity'] == context['identity'] and result['context_sha256'] == digest(context)
                            and result['model'] == model and result['prompt_sha256'] == prompt_sha
                            and result['context_version'] == CONTEXT_VERSION and result['prompt_version'] == PROMPT_VERSION
                            and isinstance(result['provider_response_id'], str) and result['provider_response_id'],
                            'Generated metadata does not match claimed context/version')
                    require(result['body'] == ' '.join(s['text'] for s in result['output']['sentences']), 'Body differs from cited sentences')
                    check_current_fixture(source, slot)
                    store.finish(slot['identity'], content_version, claim['token'], now(), generated=result)
                except (CorrespondentError, ValueError, KeyError, TypeError) as exc:
                    store.finish(slot['identity'], content_version, claim['token'], now(), error=type(exc).__name__)
                except Exception as exc:
                    # No exception messages/credentials or automatic transport retry.
                    store.finish(slot['identity'], content_version, claim['token'], now(),
                                 error=type(exc).__name__, uncertain=True)
        return store.summary(season_year, matchweek, content_version)
    finally:
        store.close()
