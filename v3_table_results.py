"""Adapt official standings and Matchweek models without recalculating rules."""

from v3_fixtures import schedule_dates


def build_table_results(*, standings, details, completed_matchweeks, viewer_id, fixtures_by_week=None):
    rows = []
    for official in standings:
        record = details.get(official["player_id"], {})
        rows.append({
            "id": official["player_id"], "name": official["player"],
            "rank": official["rank"], "for": official["points_for"],
            "against": official["points_against"], "net": official["net_points"],
            "is_you": official["player_id"] == viewer_id,
            **{key: record.get(key, 0) for key in (
                "correct", "incorrect", "draws", "against_correct",
                "against_incorrect", "against_draws")},
        })
    weeks = []
    for mw in sorted(completed_matchweeks, key=lambda item: item["week"], reverse=True):
        matchups = ([mw["primary"]] if mw["primary"] else []) + mw["others"]
        matchups.sort(key=lambda item: item["id"])
        weeks.append({"number": mw["week"], "matchups": matchups,
                      "dates": schedule_dates((fixtures_by_week or {}).get(mw["week"], []))})
    return {"rows": rows, "weeks": weeks}
