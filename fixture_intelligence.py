"""Deterministic fixture facts from Footy's official current-season ledger.

Pure input/output: no ORM, clock, network, writes, or Pick Insight dependency.
Inputs are attribute-bearing Fixture, Result and Week records (ORM or snapshots).
"""
from dataclasses import asdict, dataclass
from datetime import timezone
from hashlib import sha256
import json

VERSION = "fixture-intelligence-v1"
MIN_SCORE = 60


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Candidate:
    id: str
    fixture: dict
    signal_type: str
    family: str
    subject_team: str
    claim: str
    evidence: dict
    sample: dict
    scope: str
    strength: int
    rarity: dict
    relevance: int
    confidence: dict
    provenance: dict
    recomputability: dict
    editorial_score: int
    score_breakdown: dict
    components: tuple = ()


PREDICATES = {
    "WINNING": lambda r: r["outcome"] == "W",
    "UNBEATEN": lambda r: r["outcome"] in ("W", "D"),
    "LOSING": lambda r: r["outcome"] == "L",
    "WINLESS": lambda r: r["outcome"] in ("L", "D"),
    "CLEAN_SHEET": lambda r: r["ga"] == 0,
    "NO_CLEAN_SHEET": lambda r: r["ga"] > 0,
    "SCORING": lambda r: r["gf"] > 0,
    "NO_SCORING": lambda r: r["gf"] == 0,
}
LABELS = {"WINNING": "wins", "UNBEATEN": "unbeaten matches", "LOSING": "defeats",
          "WINLESS": "winless matches", "CLEAN_SHEET": "clean sheets",
          "NO_CLEAN_SHEET": "matches without a clean sheet", "SCORING": "matches scoring",
          "NO_SCORING": "matches without scoring"}
GOAL_TYPES = set(list(PREDICATES)[4:])


def build_fixture_intelligence(*, fixture, fixtures, results, weeks, season_id, as_of):
    """Build a JSON-ready packet. Caller supplies the entire season schedule.

    Both fixture kickoff and result.updated_at must be strictly before the cutoff
    min(as_of, target kickoff). Unknown dates cannot establish temporal safety.
    Results has no revision history: later corrections are excluded, not rewound.
    Missing results remain gaps; detectors never bridge them to invent runs.
    """
    week_ids = {w.id for w in weeks if w.season_id == season_id}
    if fixture.week_id not in week_ids:
        raise ValueError("Target fixture is not in the selected season")
    if fixture.kickoff_utc is None:
        raise ValueError("Target fixture needs a known kickoff")
    cutoff = min(utc(as_of), utc(fixture.kickoff_utc))
    schedule = sorted((f for f in fixtures if f.week_id in week_ids), key=lambda f: f.id)
    if len({f.id for f in schedule}) != len(schedule):
        raise ValueError("Duplicate fixture IDs")
    if not any(f.id == fixture.id and (f.home, f.away, f.kickoff_utc) ==
               (fixture.home, fixture.away, fixture.kickoff_utc) for f in schedule):
        raise ValueError("Target must be present in the supplied season schedule")
    result_map = {}
    for result in results:
        if result.fixture_id in result_map:
            raise ValueError("Duplicate official result for a fixture")
        result_map[result.fixture_id] = result
    reference = dict(id=fixture.id, season_id=season_id, home=fixture.home, away=fixture.away,
                     kickoff_utc=utc(fixture.kickoff_utc).isoformat())
    histories = {}
    unknown_dates = set()
    exclusions = []
    for f in schedule:
        for team in (f.home, f.away):
            histories.setdefault(team, [])
        if f.id == fixture.id:
            continue
        if f.kickoff_utc is None:
            unknown_dates.update((f.home, f.away))
            exclusions.append(dict(fixture_id=f.id, reason="unknown_kickoff"))
            continue
        if utc(f.kickoff_utc) >= cutoff:
            continue
        r = result_map.get(f.id)
        reason = None
        if r is None:
            reason = "missing_official_result"
        elif r.updated_at is None or utc(r.updated_at) >= cutoff:
            reason = "result_not_available_before_cutoff"
        elif r.outcome not in ("Home", "Away", "Draw"):
            reason = "invalid_outcome"
        valid = reason is None
        scored = valid and all(type(s) is int and s >= 0 for s in (r.home_score, r.away_score))
        if scored:
            expected = "Home" if r.home_score > r.away_score else "Away" if r.away_score > r.home_score else "Draw"
            if expected != r.outcome:
                scored = False
                exclusions.append(dict(fixture_id=f.id, reason="score_outcome_conflict"))
        if reason:
            exclusions.append(dict(fixture_id=f.id, reason=reason))
        for team, home in ((f.home, True), (f.away, False)):
            histories[team].append(dict(
                fixture_id=f.id, kickoff=utc(f.kickoff_utc).isoformat(), home=home,
                outcome=("D" if r.outcome == "Draw" else "W" if r.outcome == ("Home" if home else "Away") else "L") if valid else None,
                gf=(r.home_score if home else r.away_score) if scored else None,
                ga=(r.away_score if home else r.home_score) if scored else None,
                source=getattr(r, "source", None) if valid else None,
                updated_at=utc(r.updated_at).isoformat() if valid else None))
    for rows in histories.values():
        rows.sort(key=lambda r: (r["kickoff"], r["fixture_id"]))
    # Hash only eligible ledger evidence/gaps; future results cannot change it.
    fingerprint = sha256(json.dumps(histories, sort_keys=True).encode()).hexdigest()
    raw = []

    def add(team, kind, family, scope, rows, claim, evidence, strength, components=()):
        relevant = scope in ("overall", "split") or scope == ("home" if team == fixture.home else "away")
        relevance = 10 if relevant else 3
        confidence = 10 if len(rows) >= 5 else 5
        breakdown = dict(strength=min(strength, 80), relevance=relevance, sample_confidence=confidence)
        identifier = f"{fixture.id}:{team}:{scope}:{kind}"
        raw.append(Candidate(
            id=identifier, fixture=reference, signal_type=kind, family=family, subject_team=team,
            claim=claim, evidence=evidence,
            sample=dict(size=len(rows), fixture_ids=[r["fixture_id"] for r in rows],
                        start=rows[0]["kickoff"] if rows else None,
                        end=rows[-1]["kickoff"] if rows else None, window="current_season_before_cutoff"),
            scope=scope, strength=breakdown["strength"],
            rarity=dict(basis="v1_editorial_heuristic", historical_percentile=None),
            relevance=relevance, confidence=dict(level="high" if confidence == 10 else "limited_sample",
                                                  score=confidence, basis="official_ledger"),
            provenance=dict(source="Footy official results", rows=rows),
            recomputability=dict(engine_version=VERSION, season_id=season_id, cutoff=cutoff.isoformat(),
                                 ledger_sha256=fingerprint, requires="same fixture schedule and result snapshot"),
            editorial_score=sum(breakdown.values()), score_breakdown=breakdown, components=tuple(components)))
        return raw[-1]

    def run_length(rows, kind):
        n = 0
        for r in reversed(rows):
            if r["outcome"] is None or (kind in GOAL_TYPES and r["gf"] is None) or not PREDICATES[kind](r):
                break
            n += 1
        return n

    def summary(rows):
        return dict(played=len(rows), wins=sum(r["outcome"] == "W" for r in rows),
                    draws=sum(r["outcome"] == "D" for r in rows),
                    losses=sum(r["outcome"] == "L" for r in rows),
                    points=sum(3 if r["outcome"] == "W" else 1 if r["outcome"] == "D" else 0 for r in rows))

    for team in (fixture.home, fixture.away):
        history = histories[team]
        # An undated game could interrupt any chronological window.
        if team in unknown_dates:
            continue
        runs = {}
        scopes = {"overall": history, "home": [r for r in history if r["home"]],
                  "away": [r for r in history if not r["home"]]}
        if 3 <= len(history) <= 10 and all(r["outcome"] for r in history):
            record = summary(history)
            ppg = record["points"] / len(history)
            add(team, "SEASON_START_RECORD", "SEASON_START", "overall", history,
                f"{team} opened the recorded season with {record['wins']} wins, {record['draws']} draws and {record['losses']} defeats in {len(history)} matches.",
                dict(**record, comparison="this season only"), int(20 + abs(ppg - 1.4) * 35))
        for scope, rows in scopes.items():
            for kind in PREDICATES:
                n = run_length(rows, kind)
                if not n:
                    continue
                strength = min(80, 15 + n * 10)
                c = add(team, kind + "_RUN", "RUN", scope, rows[-n:],
                        f"{team}: {n} consecutive {LABELS[kind]} ({scope}) in the observed current-season sequence.",
                        dict(metric=kind, count=n, lower_bound=n == len(rows) or
                             rows[-n-1]["outcome"] is None or (kind in GOAL_TYPES and rows[-n-1]["gf"] is None)), strength)
                runs[(scope, kind)] = c
                if n >= 3:
                    add(team, kind + "_CONTINUATION", "CONTINUATION", scope, rows[-n:],
                        f"Another qualifying match would extend {team}'s {scope} {LABELS[kind]} run to {'at least ' if c.evidence['lower_bound'] else ''}{n+1}.",
                        dict(metric=kind, current=n, would_be=n+1, conditional=True,
                             lower_bound=c.evidence["lower_bound"]), strength - 12, (c.id,))
                if scope == "overall" and n == len(rows) and n >= 3:
                    add(team, kind + "_SEASON_START", "SEASON_START", scope, rows,
                        f"{team} opened the recorded current-season schedule with {n} consecutive {LABELS[kind]}.",
                        dict(metric=kind, count=n, comparison="this season only"), strength, (c.id,))
            recent = rows[-5:]
            if len(recent) == 5 and all(r["outcome"] is not None for r in recent):
                record = summary(recent)
                add(team, "ROLLING_FORM", "FORM", scope, recent,
                    f"{team}: {record['wins']} wins, {record['draws']} draws, {record['losses']} defeats in the last 5 ({scope}).",
                    record, 20 + abs(record["points"] - 7) * 6)
                for kind in ("WINNING", "CLEAN_SHEET", "SCORING", "NO_SCORING"):
                    if kind in GOAL_TYPES and any(r["gf"] is None for r in recent):
                        continue
                    count = sum(PREDICATES[kind](r) for r in recent)
                    add(team, kind + "_LAST_5", "FREQUENCY", scope, recent,
                        f"{team}: {count} {LABELS[kind]} in the last 5 ({scope}).",
                        dict(metric=kind, count=count, denominator=5), 10 + count * 13)
                if all(r["gf"] is not None for r in recent):
                    gf, ga = sum(r["gf"] for r in recent), sum(r["ga"] for r in recent)
                    add(team, "RECENT_GOALS", "GOALS", scope, recent,
                        f"{team}: {gf} goals scored and {ga} conceded in the last 5 ({scope}).",
                        dict(goals_for=gf, goals_against=ga, goals_for_per_game=gf/5, goals_against_per_game=ga/5),
                        25 + min(45, max(gf, ga) * 3))
        venue = "home" if team == fixture.home else "away"
        recent, venue_recent = history[-5:], scopes[venue][-5:]
        if len(recent) == len(venue_recent) == 5 and all(r["outcome"] for r in recent + venue_recent):
            overall_record, venue_record = summary(recent), summary(venue_recent)
            gap = abs(overall_record["points"] - venue_record["points"]) / 5
            ids = {r["fixture_id"] for r in recent + venue_recent}
            add(team, "OVERALL_VENUE_CONTRAST", "CONTRAST", "split", [r for r in history if r["fixture_id"] in ids],
                f"{team}: {overall_record['points']} points in the last 5 overall; {venue_record['points']} in the last 5 at {venue}.",
                dict(overall=overall_record, venue_record=venue_record, venue=venue, points_per_game_gap=gap), int(20 + gap * 25))
        overall = runs.get(("overall", "WINLESS"))
        unbeaten = runs.get((venue, "UNBEATEN"))
        if overall and unbeaten and overall.evidence["count"] >= 3 and unbeaten.evidence["count"] >= 4:
            ids = set(overall.sample["fixture_ids"] + unbeaten.sample["fixture_ids"])
            add(team, "FORM_CONTRAST", "CONTRAST", "split", [r for r in history if r["fixture_id"] in ids],
                f"{team} are winless in {overall.evidence['count']} overall but unbeaten in {unbeaten.evidence['count']} at {venue}.",
                dict(overall_winless=overall.evidence["count"], venue_unbeaten=unbeaten.evidence["count"], venue=venue),
                80, (overall.id, unbeaten.id))
        home, away = scopes["home"][-5:], scopes["away"][-5:]
        if min(len(home), len(away)) >= 3 and all(r["outcome"] for r in home + away):
            h, a = summary(home), summary(away)
            gap = abs(h["points"]/len(home) - a["points"]/len(away))
            add(team, "HOME_AWAY_SPLIT", "CONTRAST", "split", sorted(home + away, key=lambda r: (r["kickoff"], r["fixture_id"])),
                f"{team}: {h['points']} points in {len(home)} home matches; {a['points']} in {len(away)} away matches.",
                dict(home=h, away=a, points_per_game_gap=round(gap, 3)), int(20 + gap * 25))

    # League-wide claims require a full 20-team schedule, no gaps, scored samples,
    # at least five games each and comparable games played. Totals, not all-time records.
    league_ready = len(histories) == 20 and not unknown_dates and all(
        len(rows) >= 5 and all(r["gf"] is not None for r in rows) for rows in histories.values())
    league_ready = league_ready and max(map(len, histories.values())) - min(map(len, histories.values())) <= 1
    if league_ready:
        totals = {team: sum(r["ga"] for r in rows) for team, rows in histories.items()}
        if min(totals.values()) != max(totals.values()):
            for team in (fixture.home, fixture.away):
                for kind, extreme in (("BEST_DEFENCE", min(totals.values())), ("WORST_DEFENCE", max(totals.values()))):
                    peers = sorted(t for t, value in totals.items() if value == extreme)
                    if team in peers and len(peers) <= 3:
                        add(team, kind, "LEAGUE_EXTREME", "overall", histories[team],
                            f"{team}: {'joint ' if len(peers)>1 else ''}{'fewest' if kind == 'BEST_DEFENCE' else 'most'} goals conceded in the current league season ({extreme}).",
                            dict(goals_against=extreme, tied_teams=peers, comparison="current season totals",
                                 league={t: dict(played=len(histories[t]), goals_against=totals[t], rows=histories[t]) for t in sorted(histories)}), 80)

    ranked, decisions = rank_candidates(raw, fixture)
    return dict(engine_version=VERSION, fixture=reference, cutoff=cutoff.isoformat(),
                candidates=[asdict(c) for c in sorted(raw, key=lambda c: c.id)],
                ranked_candidates=[asdict(c) for c in ranked], ranking_decisions=decisions,
                diagnostics=dict(exclusions=exclusions, league_comparison_eligible=bool(league_ready),
                                 unknown_date_teams=sorted(unknown_dates)),
                limitations=["Current season only; no historical or H2H comparisons.",
                             "Result updated_at is availability evidence, not a revision archive.",
                             "Season-start claims assume the supplied schedule covers the season from its start."])


def rank_candidates(candidates, fixture):
    """Keep raw facts intact; disclose threshold and semantic suppression reasons."""
    ordered = sorted(candidates, key=lambda c: (-c.editorial_score, -len(c.components), c.scope != "overall", c.id))
    kept, decisions = [], []
    for c in ordered:
        reason, winner = None, None
        if c.editorial_score < MIN_SCORE:
            reason = "below_editorial_threshold"
        if c.family == "CONTINUATION" and c.scope not in ("overall", "home" if c.subject_team == fixture.home else "away"):
            reason = "continuation_not_applicable_at_fixture_venue"
        if reason is None:
            for k in kept:
                if c.subject_team != k.subject_team:
                    continue
                same_rows = c.sample["fixture_ids"] == k.sample["fixture_ids"]
                same_metric = c.evidence.get("metric") is not None and c.evidence.get("metric") == k.evidence.get("metric")
                equivalent_run = c.family == k.family == "RUN" and {c.evidence.get("metric"), k.evidence.get("metric")} in ({"WINNING", "UNBEATEN"}, {"LOSING", "WINLESS"})
                form_types = {"ROLLING_FORM", "SEASON_START_RECORD", "WINNING_LAST_5"}
                equivalent_form = c.signal_type in form_types and k.signal_type in form_types
                # A perfect W/D/L record is already conveyed by its pure run.
                for record, run in ((c, k), (k, c)):
                    if record.signal_type in ("ROLLING_FORM", "SEASON_START_RECORD") and run.family in ("RUN", "SEASON_START"):
                        metric = run.evidence.get("metric")
                        key = "wins" if metric in ("WINNING", "UNBEATEN") else "losses" if metric in ("LOSING", "WINLESS") else None
                        if key and record.evidence.get(key) == record.sample["size"]:
                            equivalent_form = True
                if c.id in k.components or k.id in c.components or (same_rows and (same_metric or equivalent_run)):
                    reason, winner = "redundant_with_stronger_fact", k.id
                    break
                if same_rows and equivalent_form:
                    reason, winner = "redundant_form_window", k.id
                    break
        decisions.append(dict(candidate_id=c.id, selected=reason is None, reason=reason or "selected",
                              suppressed_by=winner, score=c.editorial_score))
        if reason is None:
            kept.append(c)
    return kept, decisions
