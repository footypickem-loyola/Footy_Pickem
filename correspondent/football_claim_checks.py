"""Narrow deterministic entailment checks, not a general English fact checker.

Only the cited frozen sample is used. Ambiguous arithmetic bindings fail closed.
"""
import re
from collections import Counter

from pre_match_brief import require

# Explicit editorial aliases, never fuzzy/substring club inference.
CLUB_ALIASES = {
    'Crystal Palace': {'Palace'},
    'Man City': {'Manchester City'},
    'Man United': {'Manchester United'},
}
RANKING_SIGNALS = {'BEST_DEFENCE', 'WORST_DEFENCE', 'BEST_ATTACK', 'WORST_ATTACK'}
RANKING = re.compile(r'\b(fewest|most|best|worst|highest|lowest|joint|rank(?:ed|ing)?)\b', re.I)
MONTH = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?'
# A ranking may be stated at the frozen comparison scope, never attached to a
# sample's historical endpoint. Conservatively split dated stats from rankings.
HISTORICAL_BOUND = re.compile(
    rf'\b(?:\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH}|{MONTH}\s+\d{{1,2}}|\d{{4}}-\d{{2}}-\d{{2}})\b'
    rf'|\b(?:by|before|after|since|between|from|during|in|on|as of)\s+(?:the\s+)?{MONTH}\b'
    r'|\b(?:by|after|through|before)\s+(?:their\s+|the\s+)?(?:first|opening|matchweek|week|then)\b'
    r'|\b(?:at that (?:time|point)|yesterday|back then)\b', re.I)
NUMBERS = dict(zip(('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty').split(), range(21)))
NUMBERS.update(no=0, once=1, twice=2, a=1)
for tens, base in (('twenty',20),('thirty',30)):
    NUMBERS[tens]=base
    for unit,value in list(NUMBERS.items()):
        if unit in ('one','two','three','four','five','six','seven','eight','nine'):
            NUMBERS[tens+'-'+unit]=base+value
NUMBER = r'(?:\d+|'+'|'.join(NUMBERS)+r')'
COUNTS = re.compile(rf'(?<![\w+–-])(?P<n>{NUMBER})\s+(?P<noun>wins?|victor(?:y|ies)|draws?|defeats?|losses)\b'
                    rf'|\b(?P<verb>won|winning|drew|drawing|lost|losing)\s+(?:all\s+)?(?P<v>{NUMBER})\b(?!\s*[–-]\s*\d)', re.I)


def aliases(team):
    for canonical, others in CLUB_ALIASES.items():
        if team == canonical or team in others:
            return {canonical} | others
    return {team} if team else set()


def mentions(text, teams):
    return sorted((m.start(), team) for team in teams for name in aliases(team)
                  for m in re.finditer(r'(?<!\w)'+re.escape(name)+r'(?!\w)', text, re.I))


def team_pronoun(facts, previous_text):
    """Only a team aggregate with one cited subject and one prior antecedent."""
    subjects = {f.get('subject_team') for f in facts} - {None, ''}
    if len(subjects) != 1 or any(f['writing']['scorers'] for f in facts):
        return False
    return bool(previous_text) and next(iter(subjects)) in {t for _, t in mentions(previous_text, subjects)}


def validate_derived_claims(text, facts, previous_text='', fixture_teams=()):
    ranking_facts = [f for f in facts if f.get('signal_type') in RANKING_SIGNALS]
    if ranking_facts and RANKING.search(text):
        require(not HISTORICAL_BOUND.search(text), 'League ranking cannot inherit a historical sample date')

    counts = list(COUNTS.finditer(text))
    if not counts:
        return
    samples = {}
    for f in facts:
        rows = f.get('provenance', {}).get('rows', [])
        team = f.get('subject_team')
        if team and rows and all(r.get('outcome') in ('W', 'D', 'L') for r in rows):
            key = tuple((r.get('fixture_id'), r['outcome']) for r in rows)
            samples.setdefault(team, {})[key] = Counter(r['outcome'] for r in rows)
        elif team and not rows:
            record=f.get('evidence', {})
            keys=('wins','draws','losses')
            if all(type(record.get(k)) is int and record[k]>=0 for k in keys) and sum(record[k] for k in keys)==record.get('played'):
                key=('summary', tuple(f.get('sample', {}).get('fixture_ids', [])), *(record[k] for k in keys))
                samples.setdefault(team, {})[key]=dict(zip(('W','D','L'),(record[k] for k in keys)))
    # Do not bind to a conveniently matching opponent or a different sample.
    subjects = {f.get('subject_team') for f in facts} - {None, ''}
    known_teams = subjects | set(fixture_teams)
    for match in counts:
        if re.search(r'\d\s*[–-]\s*$', text[:match.start()]):
            continue  # The second half of a score, not a count of draws/wins.
        clause = re.split(r';|\b(?:while|whereas|but)\b', text[:match.start()], flags=re.I)[-1]
        earlier = {t for _, t in mentions(clause, known_teams)}
        if earlier:
            require(len(earlier) == 1, 'Ambiguous W-D-L subject')
            team = next(iter(earlier))
        else:
            prior = {t for _, t in mentions(previous_text, known_teams)}
            require(len(subjects) == 1 and (not previous_text or prior == subjects), 'Ambiguous W-D-L subject')
            team = next(iter(subjects))
        choices = samples.get(team, {})
        require(len(choices) == 1, 'W-D-L claim needs one cited outcome sample')
        token = (match['n'] or match['v']).lower()
        value = int(token) if token.isdigit() else NUMBERS[token]
        if token == 'a' and re.search(r'\bwithout\s*$', text[:match.start()], re.I):
            value = 0
        label = (match['noun'] or match['verb']).lower()
        outcome = 'W' if label.startswith(('win', 'won', 'victor')) else 'D' if label.startswith(('draw', 'drew')) else 'L'
        require(next(iter(choices.values()))[outcome] == value, 'W-D-L count contradicts cited outcomes')
