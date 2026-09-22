# Wave 1 / PR 1 implementation notes

The V3 shell uses Flask filesystem templates and static assets. Existing page handlers and URLs remain intact. No schema, scoring, draft, sync, or Correspondent logic changes are included.

## Navigation mapping

- Matchweek: `/tab/current` (also the initial `/` page).
- Fixtures: `/tab/open`, preserving open-week access until the Fixtures redesign.
- Table & Results: `/tab/season`.
- Training Ground: `/tab/stats`, preserving existing statistics.
- Around the League: `/tab/league`, a small placeholder for the later coverage redesign. Existing recaps remain with matchweek scores.

Admin remains a utility link outside primary navigation. Existing season selectors stay inside legacy pages; navigation preserves an explicit `season` query parameter. Account/join controls sit in the header.

One semantic navigation element serves desktop and mobile: CSS places it at the bottom on small screens. Real anchor URLs work without HTMX; enhanced requests retain the existing main-content swap and out-of-band active navigation update. History restoration receives a full document.

## Presentation migration

`templates/v3/components/ui.html` provides autoescaped, caller-based component macros for future page work. The league placeholder demonstrates page header, card, and empty state. Legacy page styles and gameplay JavaScript were extracted into `static/v3/legacy.*` without behavior changes. `v3.css` supplies tunable tokens and shell overrides. Legacy page, join, and admin templates remain inline to keep this PR bounded.

The root body is trusted server-rendered template output, as before. User-facing macro values remain autoescaped; the extracted JavaScript reads image URLs from a Jinja `tojson` data block.

## Validation

Run `.venv/Scripts/python.exe -m unittest discover -s tests` (or equivalent environment Python). Regression coverage includes all five destinations for full-page, HTMX and history restoration requests, active state, season-preserving links, join/session flow, admin access, static assets, and escaping. The existing suite exercises security, gameplay, score sync and Correspondent behavior.
