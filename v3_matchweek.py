"""Matchweek presentation models. No database access or gameplay rules live here."""

from datetime import timezone


def kickoff_order(fixture):
    """Known kickoffs first, with stable fixture order for ties and missing times."""
    kickoff = fixture.kickoff_utc
    if kickoff is not None:
        kickoff = kickoff.replace(tzinfo=timezone.utc) if kickoff.tzinfo is None else kickoff
    return (kickoff is None, kickoff.timestamp() if kickoff else 0, fixture.match_number)


def build_player_context(standings, weekly_scores):
    """Present official prior-week standings and finalized H2H records."""
    context = {}
    for row in standings:
        record = {"wins": 0, "losses": 0, "ties": 0}
        for scores in weekly_scores:
            score = scores.get(row["player_id"])
            if score is not None:
                result = "wins" if score["for"] > score["against"] else "losses" if score["for"] < score["against"] else "ties"
                record[result] += 1
        rank = row["rank"]
        suffix = "th" if 10 <= rank % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")
        context[row["player_id"]] = dict(record, rank=rank, place=f"{rank}{suffix}",
                                          net_points=row["net_points"])
    return context


def build_matchweek(*, week, season, you, matchups, fixtures, picks, results,
                    turns, points, payouts, outcome_for_pick, contribution_for_pick, draft_turn_at, player_context=None):
    """Shape domain objects and official helper outputs for all Matchweek states.

    A complete draft is not live. Partial official results may annotate owned
    fixtures, but only a finalized week receives a final H2H summary/payout.
    """
    result_map = {result.fixture_id: result for result in results}
    fixture_map = {fixture.id: fixture for fixture in fixtures}
    viewer_id = you.id if you else None
    views = []
    for matchup in matchups:
        history = []
        matchup_picks = sorted(
            (pick for pick in picks if pick.matchup_id == matchup.id),
            key=lambda pick: (pick.created_at, pick.id),
        )
        complete = len(matchup_picks) == 10
        final = week.status == "finalized"
        state = "final" if final else "ready" if complete else "draft"
        for sequence, pick in enumerate(matchup_picks, 1):
            fixture = fixture_map[pick.fixture_id]
            result = result_map.get(fixture.id)
            outcome = result.outcome if result else None
            history.append({
                "id": pick.id, "sequence": sequence, "player_id": pick.player_id,
                "player": pick.player.name, "is_yours": pick.player_id == viewer_id,
                "fixture_id": fixture.id,
                "home": fixture.home, "away": fixture.away, "team": pick.team,
                "opposition": fixture.away if pick.team == fixture.home else fixture.home,
                "kickoff_utc": fixture.kickoff_utc,
                "outcome": outcome_for_pick(pick, outcome),
                "contribution": contribution_for_pick(pick, outcome) if result else None,
                "score": (f"{result.home_score}–{result.away_score}"
                          if result and result.home_score is not None
                          and result.away_score is not None else None),
            })
        players = []
        for player, opponent in ((matchup.player_a, matchup.player_b),
                                 (matchup.player_b, matchup.player_a)):
            owned = [row for row in history if row["player_id"] == player.id]
            owned.sort(key=lambda row: kickoff_order(fixture_map[row["fixture_id"]]))
            players.append({
                "id": player.id, "name": player.name, "is_you": player.id == viewer_id,
                "owned": owned, "for": points.get(player.id, 0),
                "pick_record": {outcome: sum(row["outcome"] == outcome for row in owned)
                                for outcome in ("correct", "incorrect", "draw")},
                "season_record": (player_context or {}).get(player.id),
                "against": points.get(opponent.id, 0),
                "net": points.get(player.id, 0) - points.get(opponent.id, 0),
            })
        player_ids = {player["id"] for player in players}
        picked_ids = {row["fixture_id"] for row in history}
        turn_id = turns[matchup.id] if state == "draft" else None
        turn = next((player for player in players if player["id"] == turn_id), None)
        payout = next((row for row in payouts
                       if {row["from"], row["to"]} == {player["name"] for player in players}),
                      {"from": "-", "to": "-", "points": 0, "payout": 0})
        first_id = matchup.first_picker_id
        second_id = next(player["id"] for player in players if player["id"] != first_id)
        upcoming = [next(player for player in players
                         if player["id"] == draft_turn_at(first_id, second_id, index))
                    for index in range(len(history), min(10, len(history) + 6))]
        views.append({
            "id": matchup.id, "week": week.number, "state": state,
            "state_label": {"draft": "Draft", "ready": "Matchup set", "final": "Final"}[state],
            "players": players, "is_yours": viewer_id in player_ids,
            "first_picker": matchup.first_picker.name, "turn": turn,
            "upcoming_turns": upcoming if state == "draft" else [],
            "draft_slots": history + [{"sequence": n, "pending": True}
                                      for n in range(len(history) + 1, 11)],
            "your_turn": bool(turn and turn["is_you"] and not season.is_archived),
            "can_pick": bool(turn and turn["is_you"] and not season.is_archived),
            "total_picks": len(history), "draft_complete": complete, "history": history,
            "available": sorted((fixture for fixture in fixtures if fixture.id not in picked_ids),
                                key=kickoff_order) if state == "draft" else [],
            "winner": payout["to"] if final and payout["points"] else None,
            "payout": payout if final else None,
        })
    primary = next((view for view in views if view["is_yours"]), None)
    has_matchup = primary is not None
    if primary is None and views:
        primary = views[0]
    return {
        "week": week.number, "season": season.name, "season_code": season.code,
        "archived": bool(season.is_archived), "viewer": you.name if you else None,
        "first_kickoff": next((f.kickoff_utc for f in sorted(fixtures, key=kickoff_order)
                               if f.kickoff_utc is not None), None),
        "has_matchup": has_matchup, "primary": primary,
        "others": [view for view in views if view is not primary],
    }
