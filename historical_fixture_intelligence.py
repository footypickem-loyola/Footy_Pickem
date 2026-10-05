"""Deterministic historical detectors using PR19's Candidate and ranking contract."""
from collections import defaultdict
from hashlib import sha256
import json
import re

from fixture_intelligence import Candidate
from fixture_history import utc

VERSION = "historical-fixture-intelligence-v2"
LATE_MINUTE = 85
GOALS = {14, 15, 16}
NORMAL_GOALS = {14, 16}


def sequence(history, venue=False):
    rows = history["venue_h2h" if venue else "recent_h2h"]
    gaps = history.get("gaps", [])
    if any(g["kickoff"] is None for g in gaps):
        return []
    latest_gap = max((g["kickoff"] for g in gaps), default="")
    return [r for r in rows if r["kickoff"] > latest_gap]


def outcome(row, team_id):
    gf, ga = (row["home_score"], row["away_score"]) if team_id == row["home_team_id"] else (row["away_score"], row["home_score"])
    return "W" if gf > ga else "L" if gf < ga else "D"


def provenance(row):
    return {k: v for k, v in row.items() if k != "events"}


class Context:
    def __init__(self, fixture, history):
        self.fixture, self.history = fixture, history
        self.team_names = {history["home_team_id"]: fixture["home"], history["away_team_id"]: fixture["away"]}

    def candidate(self, kind, team_id, rows, evidence, strength, *, venue=False, events=(), rarity=0):
        ordered = sorted(rows, key=lambda r: (r["kickoff"], r["external_fixture_id"]))
        exact = self.history["exact_prior_season_fixture"]
        exact_relevant = exact is not None and any(r["external_fixture_id"] == exact["external_fixture_id"] for r in rows)
        age_days = max(0, (utc(self.history["cutoff"]) - utc(ordered[-1]["kickoff"])).days)
        breakdown = dict(strength=strength, exact_fixture=8 if exact_relevant else 0,
                         opponent_specificity=8, venue_specificity=4 if venue else 0,
                         recency=8 if age_days <= 365 else 4 if age_days <= 730 else 0,
                         rarity=rarity, historical_significance=5 if kind == "EXACT_PRIOR_SEASON_FIXTURE" else 0)
        if kind == "EXACT_PRIOR_SEASON_FIXTURE":
            home, away = evidence['home_score'], evidence['away_score']
            notable = abs(home-away) >= 3 or home+away >= 5
            breakdown['scoreline_interest'] = 0 if notable else -32 if home+away == 0 else -24
        if kind == "PLAYER_VS_OPPONENT":
            meetings = evidence['scoring_meetings']
            # Recurrence is distinct scored meetings, never inferred appearances.
            breakdown['distinct_scoring_meetings'] = 22 + 4*(meetings-3) if meetings >= 3 else 0
        identity = sha256(json.dumps(dict(kind=kind, team=team_id, fixtures=[r["external_fixture_id"] for r in ordered],
                                         events=[e["external_event_id"] for e in events], evidence=evidence), sort_keys=True).encode()).hexdigest()[:16]
        source = dict(source="Sportmonks reference", fixtures=[provenance(r) for r in ordered],
                      reference_event_ids=[e["id"] for e in events], external_event_ids=[e["external_event_id"] for e in events],
                      events=list(events))
        return Candidate(id=f"{self.fixture['id']}:history:{kind}:{identity}", fixture=self.fixture,
            signal_type=kind, family="HISTORY", subject_team=self.team_names[team_id],
            claim=evidence["fact"], evidence=evidence,
            sample=dict(size=len(rows), fixture_ids=[r["reference_fixture_id"] for r in ordered],
                        start=ordered[0]["kickoff"], end=ordered[-1]["kickoff"], window="stored_PL_history"),
            scope="home" if venue else "overall", strength=strength,
            rarity=dict(basis="versioned editorial heuristic, not all-time rarity", score=rarity),
            relevance=breakdown["exact_fixture"]+breakdown["opponent_specificity"]+breakdown["venue_specificity"],
            confidence=dict(level="reference_snapshot", basis="stored completed PL fixtures and active provider events"),
            provenance=source, recomputability=dict(engine_version=VERSION, cutoff=self.history["cutoff"],
                snapshot_sha256=sha256(json.dumps(self.history, sort_keys=True).encode()).hexdigest(),
                availability_semantics=self.history["availability_semantics"]),
            editorial_score=sum(breakdown.values()), score_breakdown=breakdown)


def detect_exact_prior_season_fixture(ctx):
    row = ctx.history["exact_prior_season_fixture"]
    if row is None:
        return []
    return [ctx.candidate("EXACT_PRIOR_SEASON_FIXTURE", ctx.history["home_team_id"], [row],
        dict(fact=f"{row['home_team']} {row['home_score']}-{row['away_score']} {row['away_team']} ({row['season']}, same venue orientation).",
             home_score=row["home_score"], away_score=row["away_score"], season=row["season"],
             nearest_available_prior_season=True,
             immediately_previous_season=row["season_start"] == ctx.history["target_season_start"]-1), 52, venue=True)]


def _streaks(ctx, venue=False):
    rows = sequence(ctx.history, venue)
    candidates = []
    for team_id in ([ctx.history["home_team_id"]] if venue else ctx.team_names):
        for metric, accepted in (("WINNING", {"W"}), ("UNBEATEN", {"W", "D"}),
                                 ("WINLESS", {"L", "D"}), ("LOSING", {"L"})):
            n = 0
            for row in rows:
                if outcome(row, team_id) not in accepted:
                    break
                n += 1
            if n < (2 if venue else 3):
                continue
            lower = n == len(rows)
            candidates.append(ctx.candidate(("VENUE_H2H_" if venue else "H2H_") + metric + "_RUN", team_id, rows[:n],
                dict(fact=f"{ctx.team_names[team_id]}: {metric.lower()} across the last {n} stored {'home ' if venue else ''}PL meetings.",
                     metric=metric, count=n, lower_bound=lower,
                     boundary="available history/window or evidence gap" if lower else "preceding contrary result"),
                min(64, 22 + n*9), venue=venue))
    return candidates


def detect_h2h_streak(ctx):
    return _streaks(ctx)


def detect_venue_h2h(ctx):
    return _streaks(ctx, venue=True)


def detect_exact_goals_sequence(ctx):
    """Positive exact totals in the uninterrupted newest stored meeting sequence."""
    rows = sequence(ctx.history)
    candidates = []
    for team_id in ctx.team_names:
        matching, goals = [], None
        for row in rows:
            total = row['home_score'] if row['home_team_id'] == team_id else row['away_score']
            if goals is None:
                goals = total
            if total != goals:
                break
            matching.append(row)
        if len(matching) < 3 or not goals:
            continue
        lower = len(matching) == len(rows)
        candidates.append(ctx.candidate('H2H_EXACT_GOALS_SEQUENCE', team_id, matching,
            dict(fact=f"{ctx.team_names[team_id]} scored exactly {goals} {'goal' if goals == 1 else 'goals'} in each of the last {len(matching)} stored PL meetings between these clubs.",
                 goals_each=goals, count=len(matching), lower_bound=lower,
                 boundary='available history/window or evidence gap' if lower else 'preceding different total'),
            min(64, 26 + 6*(len(matching)-3) + 8*min(goals, 4)), rarity=8 if goals >= 3 else 0))
    return candidates


def complete_scorer_evidence(row):
    goals = [e for e in row["events"] if e["type_id"] in GOALS]
    if len(goals) != row["home_score"] + row["away_score"]:
        return False
    if any(e["type_id"] in NORMAL_GOALS and (not e["player_provider_id"] or not e["player_name"]
           or not e["player_name"].strip() or e["team_provider_id"] not in (row["home_team_id"], row["away_team_id"]))
           for e in goals):
        return False
    return all(sum(e["type_id"] in NORMAL_GOALS and e["team_provider_id"] == row[side + "_team_id"] for e in goals)
               <= row[side + "_score"] for side in ("home", "away"))


def scorer_groups(row):
    groups = defaultdict(list)
    if not complete_scorer_evidence(row):
        return groups
    for event in row["events"]:
        if (event["type_id"] in NORMAL_GOALS and event["player_provider_id"] is not None
                and event["player_name"] and event["team_provider_id"] in (row["home_team_id"], row["away_team_id"])):
            groups[(event["team_provider_id"], event["player_provider_id"])].append(event)
    return groups


def relevant_meetings(ctx):
    rows = {r["external_fixture_id"]: r for r in ctx.history["recent_h2h"]}
    exact = ctx.history["exact_prior_season_fixture"]
    if exact:
        rows[exact["external_fixture_id"]] = exact
    return sorted(rows.values(), key=lambda r: (r["kickoff"], r["external_fixture_id"]), reverse=True)


def detect_hat_trick_or_brace(ctx):
    candidates = []
    for row in relevant_meetings(ctx):
        for (team_id, player_id), events in scorer_groups(row).items():
            if len(events) < 2:
                continue
            # Incomplete/corrupt event ledgers must not claim more than the team's score.
            score = row["home_score"] if team_id == row["home_team_id"] else row["away_score"]
            if len(events) > score:
                continue
            hat_trick = len(events) >= 3
            player = events[-1]["player_name"]
            candidates.append(ctx.candidate("HAT_TRICK" if hat_trick else "BRACE", team_id, [row],
                dict(fact=f"{player}: {len(events)} goals for {ctx.team_names[team_id]} in the stored {row['season']} meeting.",
                     player_id=player_id, player_name=player, goals=len(events), own_goals_excluded=True,
                     roster_verified=False), 66 if hat_trick else 35,
                events=events, rarity=18 if hat_trick else 4))
    return candidates


def goal_timeline(row):
    """Fail closed unless the entire active goal sequence reconciles to final score."""
    goals = [e for e in row["events"] if e["type_id"] in GOALS]
    orders = [e["sort_order"] for e in goals]
    if not goals or any(type(o) is not int for o in orders) or len(set(orders)) != len(orders):
        return []
    goals.sort(key=lambda e: e["sort_order"])
    previous, timeline = (0, 0), []
    for event in goals:
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", event["running_score"] or "")
        if not match or event["minute"] is None:
            return []
        after = tuple(map(int, match.groups()))
        delta = (after[0]-previous[0], after[1]-previous[1])
        if delta not in ((1, 0), (0, 1)):
            return []
        side = 0 if delta[0] else 1
        scoring_team = row["home_team_id"] if side == 0 else row["away_team_id"]
        if event["type_id"] != 15 and event["team_provider_id"] != scoring_team:
            return []
        timeline.append(dict(event=event, before=previous, after=after, scoring_team=scoring_team))
        previous = after
    return timeline if previous == (row["home_score"], row["away_score"]) else []


def detect_late_decisive_goal(ctx):
    candidates = []
    sign = lambda score: (score[0] > score[1]) - (score[0] < score[1])
    for row in relevant_meetings(ctx):
        timeline = goal_timeline(row)
        for i, goal in enumerate(timeline):
            event = goal["event"]
            if event["minute"] < LATE_MINUTE:
                continue
            before, after = sign(goal["before"]), sign(goal["after"])
            if not ((before == 0 and after != 0) or (before != 0 and after == 0)):
                continue
            if any(sign(later["after"]) != after for later in timeline[i+1:]):
                continue
            kind = "winner" if after else "equalizer"
            minute = str(event["minute"]) + (f"+{event['extra_minute']}" if event["extra_minute"] else "")
            candidates.append(ctx.candidate("LATE_DECISIVE_GOAL", goal["scoring_team"], [row],
                dict(fact=f"{ctx.team_names[goal['scoring_team']]}: late {kind} at {minute} in the stored {row['season']} meeting.",
                     kind=kind, minute=event["minute"], extra_minute=event["extra_minute"],
                     threshold_minute=LATE_MINUTE, score_before=goal["before"], score_after=goal["after"],
                     final_score=[row["home_score"], row["away_score"]],
                     player_id=event["player_provider_id"], own_goal=event["type_id"] == 15),
                62 if after else 55, events=[g["event"] for g in timeline], rarity=8))
    return candidates


def detect_player_vs_opponent(ctx):
    rows = sequence(ctx.history)
    if not all(complete_scorer_evidence(row) for row in rows):
        return []  # An omitted scorer could change an exact cross-match tally.
    grouped = defaultdict(list)
    for row in rows:
        for key, events in scorer_groups(row).items():
            score = row["home_score"] if key[0] == row["home_team_id"] else row["away_score"]
            if len(events) <= score:
                grouped[key].extend(events)
    candidates = []
    for (team_id, player_id), events in sorted(grouped.items()):
        name = events[0]["player_name"]
        candidates.append(ctx.candidate("PLAYER_VS_OPPONENT", team_id, rows,
            dict(fact=f"{name}: {len(events)} goals for {ctx.team_names[team_id]} across the last {len(rows)} stored PL meetings between these clubs.",
                 player_id=player_id, player_name=name, goals=len(events), meetings=len(rows),
                 scoring_meetings=len({e['reference_fixture_id'] for e in events}),
                 appearance_count=None, roster_verified=False, own_goals_excluded=True),
            min(60, 5 + len(events)*9), events=events))
    return candidates


def detect_previous_red_cards(ctx):
    row = ctx.history["most_recent_meeting"]
    if row is None:
        return []
    cards = [e for e in row["events"] if e["type_id"] in (20, 21)]
    return [ctx.candidate("PREVIOUS_MEETING_RED_CARD", team, [row],
        dict(fact=f"{ctx.team_names[team]} had a red-card event in the most recent stored PL meeting.",
             event_types=sorted({e["type_id"] for e in cards if e["team_provider_id"] == team})),
        38, events=[e for e in cards if e["team_provider_id"] == team])
        for team in sorted({e["team_provider_id"] for e in cards if e["team_provider_id"] in ctx.team_names})]


def build_historical_candidates(fixture, history):
    if history["status"] != "available":
        return []
    ctx = Context(fixture, history)
    return [c for detector in (detect_exact_prior_season_fixture, detect_h2h_streak, detect_venue_h2h, detect_exact_goals_sequence,
                               detect_hat_trick_or_brace, detect_late_decisive_goal,
                               detect_player_vs_opponent, detect_previous_red_cards) for c in detector(ctx)]
