# PR27 final narrow corrections and offline replay

Continues after `3b896fc`. **Recommend proceeding to merge review without a third
paid twenty-brief evaluation.** Keep PR27 draft for review. Production activation
is **not approved**; storage/start-command/environment setup, heartbeat verification,
monitoring and deployment remain separate operational steps.

No OpenAI calls, production reads/writes, Railway/n8n changes, merge or deployment
were performed in this pass. No preserved output was edited, repaired, regenerated
or repersisted.

## Three corrections

### Cited venue names only

The writer now has exactly one additional prompt rule:

> Never name a stadium or ground unless its exact name appears in the cited facts.

`correspondent/venue_grounding.py` uses a closed vocabulary from the **cited**
structured evidence/provenance: venue/stadium/ground name fields or named objects.
It checks named locatives, explicit stadium/ground constructions and venue-like
event/modifier constructions. It is not an Anfield/Old Trafford/Emirates blacklist;
tests also use arbitrary invented ground names. A name merely in claim prose or
an uncited fact does not grant permission. Lowercase unknown `at` locations cannot
bypass the check. Date/minute expressions remain distinct from named grounds.

Explicit home-team provenance permits ordinary “at Liverpool” / “at Tottenham”
references; supported home/away flags permit ordinary home/away wording. They do
not imply a stadium name. Reference-provider club spellings are mapped explicitly
for these checks, without changing the existing scorer actor alias contract.

The exact Liverpool sentence from run two is now rejected for “at Anfield”. The
same sentence passes a regression when the cited fixture explicitly supplies
`venue_name: Anfield`; its scorer/minute handling is unchanged.

### W-D-L subject continuity

The arithmetic resolver now carries an established subject through `but`, `and`
and `while` when the following clause is a clear verb/pronoun continuation. The
exact Bournemouth sentence in Chelsea's brief therefore retains Bournemouth and
validates zero wins, two defeats and three draws.

An explicitly named new club changes the subject; that club must have its own
cited sample and matching values. Unknown subjects, competing subjects and vague
new noun subjects such as “the visitors” fail closed instead of borrowing the
preceding team's counts. Semicolons reset the subject. “And” inside a month/date
range is not treated as a clause switch. Arithmetic values are still calculated
from the cited complete sample; no W-D-L checks were removed.

### Bounded H2H fixture-score arithmetic

For H2H/venue-H2H facts, the checker derives W/D/L from **every** cited fixture's
home/away team and integer, nonnegative final scores, from the fact's subject
club's perspective. Explicit reference aliases resolve Nottingham/Nottingham
Forest and Tottenham/Tottenham Hotspur; there is no fuzzy matching.

The fixture IDs must be unique and exactly match the declared bounded sample and
size. Missing scores, booleans, negative values, absent/ambiguous subject teams,
mixed opponents, duplicate rows and missing/extra sample rows fail closed. The
checker never queries for additional fixtures or infers outcomes outside the
sample. Reference and current-form sample identities remain distinct.

An opponent in a clear “meetings with Crystal Palace” construction is treated as
the object, not a competing subject, only when the cited H2H score rows establish
that opponent. An explicit subsequent subject switch still binds to the new club;
ambiguous relative clauses fail closed. Score/result phrases such as “a 1-0 win”
also need a matching result for the subject within the same cited sample.

The exact Nottingham sentence now validates its four supplied meetings: one
Nottingham win and three draws. Reversing the subject to Palace yields zero wins,
three draws and one defeat. Regressions also move the decisive win to an away
fixture to ensure the checker does not mistake the home side for the subject.

## Exact second-run replay

The twenty original `.output.txt` and `.context.json` files in
`local_data/pr27_live_20261007T183230Z` were replayed without mutation. SHA-256 checks
across the existing artifact files, including source/content databases, confirm
they stayed unchanged. No store was opened for writes and no failed content was
repersisted. Result files are separate local-only replay artifacts.

**Whole outputs: 19 accepted, 1 rejected.**

- Accepted: Sunderland, Brighton Hove, Arsenal, Leeds United, Everton, Hull City,
  Chelsea, Bournemouth, Crystal Palace, Nottingham, Coventry City, Newcastle,
  Man City, Fulham, Ipswich Town, Man United, Tottenham, Aston Villa and Brentford.
- Rejected: Liverpool, solely for the unsupported “at Anfield” clause in sentence 3.
- Chelsea and Nottingham change from false rejections to acceptance.
- Every other supported brief remains accepted.

**Sentences: 42 supported sentences accepted; 1 unsupported sentence rejected,
43 total. No supported sentence is newly rejected.** The rejected Liverpool
sentence still has supported scorer/date/minute/result clauses, but its venue
addition cannot pass the strict cited-fact contract.

As an additional regression check, run one's unchanged outputs retain the expected
result: **18 whole outputs accepted; Hull/Villa rejected; 45 supported sentences
accepted and both known false sentences rejected**. This did not make provider
calls or update either evaluation's content store.

Local replay artifacts: `local_data/pr27-final-offline-replay.json`,
`local_data/pr27-final-offline-replay.log`, and
`local_data/pr27-final-first-run-replay.log`. Raw outputs remain local and are not
committed. The replay result does not convert either historical store into a
fresh successful twenty-brief batch.

## Full verification and unchanged boundaries

**490 Python tests passed** (51.142 seconds), including ten new focused tests with
adversarial subcases. **10 JavaScript tests passed**. Commands:

```text
.venv/Scripts/python.exe -m unittest discover -s tests
node --test tests/bulk_picks.test.cjs tests/pick_confirm.test.cjs
```

The existing de Ligt synthetic test fixture now explicitly supplies its Tottenham
home venue, matching real provenance; its scorer/minute assertions were not relaxed.
New coverage includes exact Liverpool/Chelsea/Nottingham text, cited versus uncited
or missing venue names, ordinary supported club/home/away wording, conjunction
continuity and real subject switches, H2H perspective inversion, incomplete samples
and prohibition on deriving extra results.

No changes to:

- PR24 intelligence, candidate generation, ranking or thresholds;
- cutoff rules or source observation times;
- scorer identity/actor or strict minute validation;
- shared persistence, retry limits, leases or fencing;
- the 30h eligibility / 24h readiness / 18h hard-stop scheduler;
- game results, scores, picks or payouts.

Only writer-side deterministic checks and the single requested venue prompt rule
changed, with tests and documentation. Unrelated `.gitignore`, local files and
`.env` are preserved. No production operational state was inspected or modified.

## Recommendation

**Ready to proceed to merge review; a third paid twenty-brief evaluation is not
necessary for these corrections.** Two live runs have already provided concrete
prose. Both exact preserved batches now give the intended deterministic outcomes,
all supported prose passes, the unsupported venue is blocked, and adversarial tests
cover the added subject/score-row/venue rules. Another random generation would
sample editorial variation rather than prove these deterministic cases more directly.

This is not a promise that arbitrary future prose is semantically correct. The
guards implement a bounded writing contract, not a general English entailment
system; unusually complex phrasing may still fail closed and needs normal review.
No broader editorial or generative behavior was introduced. Retain review of future
content and existing safe failure/retry behavior instead of weakening validation.

Keep PR27 draft until review is complete. **Not ready for production activation**
without the separately approved operational work. Use a new content version for
future authorized generation because the prompt hash changed; do not rewrite old
contexts/hashes or migrate old failed outputs into success. This recommendation
authorizes neither another provider call nor merge/deployment.
