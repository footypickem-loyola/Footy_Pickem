# PR26 Coventry–Hull coverage investigation

## Root cause and available facts

Read-only source: `local_data/pickem_with_reference_snapshot.db`, supplied October 5 snapshot.
SHA-256: `73c37c2cceff71f360dbf36f55ecadc1a9ad05143ed0cbf93c369794df35bef4` (unchanged).
Strict Week 2 replay cutoff: **2026-08-27 19:00 UTC**; Coventry–Hull kickoff: August 29, 14:00 UTC.

Both canonical mappings resolve correctly: Coventry City = Sportmonks club 117;
Hull City = club 22. Their shared official fixture is football-data 560561.
This is not a team-name, provider-ID or fallback eligibility bug.

- **Coventry:** the official ledger contains Arsenal 3–0 Coventry, August 21,
  19:00 UTC (football-data 560542). Its result was updated October 5 at
  02:35:24.552268 UTC, after the replay cutoff. It is therefore ineligible for
  strict Week 2 replay. There are no match-event rows for this fixture.
- **Hull:** the official ledger contains Hull 2–0 Man United, August 22,
  11:30 UTC (football-data 560543). Its result was updated October 5 at
  02:35:24.554228 UTC, also ineligible. There are no match-event rows for it.
- Neither club occurs in the stored 2024/25 or 2025/26 Premier League reference
  fixture history. Consequently there is no eligible reference H2H, recent
  reference result or reference scorer ledger for either club. This says nothing
  about their all-time history or Championship results.
- No 2026/27 reference season is present. Club scoring leaders cannot be established.
  A known team-level score would not require scorer names, but its official result
  must first satisfy the timestamp cutoff.

The results are **present but unavailable at replay**, rather than missing entirely.
Incomplete scorer information is not what blocks the team-level score anchors.
No timestamp was changed, no history reconstructed and no cutoff weakened.

## Narrow code change

The shared context builder now opts into an unranked `RECENT_FORM_CONTEXT` fallback
for up to five eligible official league results. PR25's original five-pick builder
keeps its existing fallback behavior. Both use the unchanged PR24 ranking engine.

The fallback contains exact sample size, chronological W/D/L sequence, W–D–L counts,
goals for/against, date bounds and individual result provenance. One match remains
a dated team-result anchor, not five-match form. Missing or late-updated results
cannot establish a consecutive or full-season run: the new fallback uses bounded
sample statements, not streak claims. It does not inspect ineligible result scores.

A matching authoritative reference goal ledger can add named scorers from the
newest eligible official match, with the scorer-match date and club identity.
A missing/anonymous/incomplete ledger leaves the team score usable but supplies no
specific goal story. Existing scorer, citation, minute and recency guards remain.
Current-season club leader facts retain joint leaders and now also cross-check
known schedule coverage: complete events for an incomplete fixture set are not
enough to establish club season leaders. No new event source/importer was added.

The existing two-fact fallback budget stays in place. Retained editorial facts
remain primary. Substantial sample overlap suppresses the form fallback; candidate
provenance and form samples also suppress duplicate result anchors. Intelligence
candidates and rankings are not modified. Use a **new content version** when preparing
contexts with this enhancement; successful persisted content is never refreshed.

Training Ground's heading is now **Pre-Match Brief**, preserving the five bullets
and existing styling.

## Before/after and production implications

Strict Week 2 is **18/20 before and 18/20 after**. Coventry and Hull remain blocked.
The supplied snapshot cannot establish either club's pre-cutoff official state.
An authoritative earlier snapshot/result-version archive proving those Week 1
results were available before August 27 would support the dated result fallback.
Verified pre-cutoff scorer data would enrich it but is not required for team scores.

The next scheduled matchweek in this snapshot is **Week 6**, starting October 10,
11:30 UTC. A separate current-cutoff check at **2026-10-07 03:00 UTC** (October 6,
11:00 p.m. America/New_York) produces **20/20 supported contexts before and after**.
This is not a reconstructed September replay: the October 5 updates legitimately
precede that current cutoff. Repeated complete round builds are deterministic.

At that cutoff Coventry has five official results: losses 0–3 at Arsenal, 0–1 to
Hull, 0–1 at Man City, 0–5 to Brighton Hove, then a 1–0 win at Nottingham. That is
1 win, 0 draws, 4 losses; 1 goal for and 10 against. Hull has a 2–0 win over Man
United, 1–0 win at Coventry, 0–0 draw with Aston Villa, 2–2 draw at Chelsea and
1–2 loss at Newcastle: 2 wins, 2 draws, 1 loss; 6 goals for and 4 against.
Week 6 already has retained editorial candidates; its contexts do not need the
new fallback to reach twenty. These counts establish snapshot context coverage,
not successful generation or live prose quality.

**Live production was not accessed.** The supplied snapshot is sufficient to
isolate the historical failure and demonstrate upcoming-context support. Current
production freshness, latest fixture changes, live result timestamps/event
completeness and shared-store configuration remain unverified. Before activation,
run the documented read-only context preparation against a fresh authoritative
snapshot. A production source missing results could still block a new/promoted
club, but Week 2's 18/20 alone is not evidence of a production defect.

No OpenAI calls, production writes, Railway/n8n changes, merge or deployment.

## Verification

Focused tests cover one-match and several-match promoted-club samples, exact
W–D–L/GF/GA, five-match cap and order independence, future/late-update exclusion
before score inspection, missing-history gaps, named and anonymous scorer ledgers,
tied club leaders, missing schedule coverage, overlap suppression, unchanged
editorial candidates and deterministic twenty-team contexts without player/league
identity. Existing strict cutoff, shared persistence/concurrency and PR24/PR25
regression suites remain in the full run.

Full results: **446 Python tests passed** (`python -m unittest discover -s tests`),
**10 JavaScript tests passed** (`node --test tests/bulk_picks.test.cjs tests/pick_confirm.test.cjs`).
No live provider evaluation was run. Source snapshot hash remains unchanged.
