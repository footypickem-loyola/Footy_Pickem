"""Staging-only Admin harness for exercising Correspondent V2 classification.

This module wraps the existing Flask application without changing production
routes.  Footy_Pickem_V2_Staging can point gunicorn at
``staging_classifier_admin:app`` while the classifier integration is evaluated.
"""

from flask import abort, flash, redirect, request, url_for

import pickem_flask_htmx_tabs as core


_CLASSIFY_FORM = """
_CLASSIFY_FORM += '''
              <form method="post" action="{{ url_for('admin_classify_sources') }}">
                <input type="hidden" name="week" value="{{ week.number }}">
                <button class="btn" type="submit"
                        {% if not openai_configured or not correspondent_sources %}disabled{% endif %}>
                  Classify Sources
                </button>
              </form>
'''

_V2_FORM_MARKER = '''            {% if correspondent_v2_enabled %}
              <form method="post" action="{{ url_for('admin_generate_recap') }}">
'''

if core.ADMIN_HTML.count(_V2_FORM_MARKER) != 1:
    raise RuntimeError("Unable to locate unique V2 Admin generation form")

core.ADMIN_HTML = core.ADMIN_HTML.replace(
    _V2_FORM_MARKER,
    "            {% if correspondent_v2_enabled %}\n" + _CLASSIFY_FORM
    + '''              <form method="post" action="{{ url_for('admin_generate_recap') }}">
''',
    1,
)


@core.app.post("/admin/classify-sources")
def admin_classify_sources():
    """Run only the approved two-pass source classifier for one staging week."""
    if not core.is_admin_session():
        abort(403, "Admin locked")

    db = core.SessionLocal()
    season = core.active_season(db)
    if season is None:
        abort(404, "No active season")

    week_number = request.form.get("week", type=int)
    week = None if week_number is None else core.season_week(db, season, week_number)
    if week is None:
        abort(404, "Week not found")

    try:
        summary = core.classify_and_store_week_sources(db, week)
        routes = ", ".join(
            f"{route}={count}"
            for route, count in sorted(summary["effective_route_counts"].items())
        ) or "none"
        flash(
            f"Week {week.number} sources classified: "
            f"{summary['stored_sources']} stored, "
            f"{summary['article_candidates']} article candidates, "
            f"{summary['signal_only_sources']} signal-only, "
            f"{summary['first_pass_classifications']} first-pass, "
            f"{summary['automated_reviews']} reviewed, "
            f"{summary['second_pass_classifications']} second-pass; "
            f"routes: {routes}.",
            "success",
        )
    except (ValueError, core.CorrespondentError) as exc:
        flash(str(exc), "error")
    finally:
        db.close()

    return redirect(url_for("admin", week=week.number))


app = core.app
