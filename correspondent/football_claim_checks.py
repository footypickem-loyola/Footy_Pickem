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
# Reference-provider spellings for score-row/venue identity only. The existing
# scorer actor alias contract is deliberately unchanged.
REFERENCE_ALIASES={'Nottingham':{'Nottingham Forest'},'Tottenham':{'Tottenham Hotspur'}}
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


def reference_aliases(team):
    for canonical,others in REFERENCE_ALIASES.items():
        if team==canonical or team in others:return {canonical}|others
    return aliases(team)


def team_pronoun(facts, previous_text):
    """Only a team aggregate with one cited subject and one prior antecedent."""
    subjects = {f.get('subject_team') for f in facts} - {None, ''}
    if len(subjects) != 1 or any(f['writing']['scorers'] for f in facts):
        return False
    return bool(previous_text) and next(iter(subjects)) in {t for _, t in mentions(previous_text, subjects)}


def h2h_sample(fact):
    """All bounded reference fixtures, from the fact subject's perspective."""
    if not fact.get('signal_type', '').startswith(('H2H_', 'VENUE_H2H_')):
        return None
    rows=fact.get('provenance', {}).get('fixtures', [])
    sample=fact.get('sample', {})
    ids=[r.get('reference_fixture_id') for r in rows]
    require(rows and sample.get('size')==len(rows) and all(type(i) is int and i>0 for i in ids)
            and len(set(ids))==len(ids) and len(sample.get('fixture_ids', []))==len(ids)
            and all(type(i) is int for i in sample.get('fixture_ids', []))
            and set(ids)==set(sample.get('fixture_ids', [])),
            'Incomplete bounded H2H sample')
    names=reference_aliases(fact.get('subject_team')); derived=[]; opponents=set()
    for r in rows:
        home,away=r.get('home_team'),r.get('away_team')
        hs,aws=r.get('home_score'),r.get('away_score')
        require(isinstance(home,str) and isinstance(away,str) and home and away and home!=away
                and type(hs) is int and type(aws) is int and hs>=0 and aws>=0,
                'Incomplete H2H score row')
        require((home in names)!=(away in names), 'H2H subject absent or ambiguous')
        gf,ga=(hs,aws) if home in names else (aws,hs)
        opponent=away if home in names else home
        opponents.add(next((k for k in CLUB_ALIASES|REFERENCE_ALIASES if opponent in reference_aliases(k)),opponent))
        derived.append((r['reference_fixture_id'],'W' if gf>ga else 'L' if gf<ga else 'D',gf,ga))
    require(len(opponents)==1, 'H2H sample must describe one opponent')
    return derived,opponents


def arithmetic_subject(prefix, facts, known_teams, previous_text, subjects):
    # Only explicit, score-verified H2H opponents may be excluded as objects.
    for f in facts:
        h2h=h2h_sample(f)
        if h2h is None:continue
        for opponent in h2h[1]:
            for name in reference_aliases(opponent):
                pattern=r'\b(?:meetings|matches|games|encounters|clashes)\s+(?:with|against|versus|vs\.?)\s+'+re.escape(name)+r'(?!\w)'
                for m in list(re.finditer(pattern,prefix,re.I)):
                    require(not re.match(r'\s*,?\s*(?:who|whose|which)\b',prefix[m.end():],re.I),
                            'Ambiguous H2H relative-clause subject')
                prefix=re.sub(pattern,lambda m:' ' * len(m.group()),prefix,flags=re.I)
    active=None
    # "between 21 October and 1 February" is a date range, not a new clause.
    prefix=re.sub(r'\band\b(?=\s+(?:\d{1,2}\s+)?'+MONTH+r'\b)','   ',prefix,flags=re.I)
    parts=re.split(r'(;|\b(?:and|while|whereas|but)\b)',prefix,flags=re.I)
    for i,part in enumerate(parts):
        if i%2:
            if part==';':active=None
            continue
        named={t for _,t in mentions(part,known_teams)}
        require(len(named)<=1, 'Ambiguous W-D-L subject')
        if named:
            active=next(iter(named))
        elif i and part.strip():
            # Omitted subject is allowed only for a verb/pronoun continuation;
            # "but the visitors..." or an unknown named club cannot borrow it.
            require(active is not None and re.match(
                r'\s*(?:(?:a|an|with)\s*$|(?:they|have|had|has|are|were|remain(?:ed)?|record(?:ed|ing)?|won|win(?:ning)?|drew|draw(?:ing)?|lost|losing|scor(?:ed|ing)|conced(?:ed|ing)|fail(?:ed|ing)|kept)\b)',part,re.I),
                'Ambiguous W-D-L continuation subject')
    if active is None:
        prior={t for _,t in mentions(previous_text,known_teams)}
        require(';' not in prefix and len(subjects)==1 and (not previous_text or prior==subjects),
                'Ambiguous W-D-L subject')
        active=next(iter(subjects))
    return active


def validate_derived_claims(text, facts, previous_text='', fixture_teams=()):
    ranking_facts = [f for f in facts if f.get('signal_type') in RANKING_SIGNALS]
    if ranking_facts and RANKING.search(text):
        require(not HISTORICAL_BOUND.search(text), 'League ranking cannot inherit a historical sample date')

    counts = list(COUNTS.finditer(text))
    score_results=list(re.finditer(r'\b(\d+)\s*[–-]\s*(\d+)\s+(win|victory|draw|defeat|loss)\b',text,re.I))
    h2h_facts=[f for f in facts if f.get('signal_type','').startswith(('H2H_','VENUE_H2H_'))]
    if not counts and not (score_results and h2h_facts):
        return
    samples = {}
    for f in facts:
        rows = f.get('provenance', {}).get('rows', [])
        team = f.get('subject_team')
        h2h=h2h_sample(f)
        if h2h is not None:
            derived,_=h2h
            key=('reference',tuple((r[0],r[1]) for r in derived))
            samples.setdefault(team,{})[key]=Counter(r[1] for r in derived)
        elif team and rows and all(r.get('outcome') in ('W', 'D', 'L') for r in rows):
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
        team=arithmetic_subject(text[:match.start()],facts,known_teams,previous_text,subjects)
        choices = samples.get(team, {})
        require(len(choices) == 1, 'W-D-L claim needs one cited outcome sample')
        token = (match['n'] or match['v']).lower()
        value = int(token) if token.isdigit() else NUMBERS[token]
        if token == 'a' and re.search(r'\bwithout\s*$', text[:match.start()], re.I):
            value = 0
        label = (match['noun'] or match['verb']).lower()
        outcome = 'W' if label.startswith(('win', 'won', 'victor')) else 'D' if label.startswith(('draw', 'drew')) else 'L'
        require(next(iter(choices.values()))[outcome] == value, 'W-D-L count contradicts cited outcomes')
    for match in score_results if h2h_facts else []:
        team=arithmetic_subject(text[:match.start()],facts,known_teams,previous_text,subjects)
        kind=match[3].lower();outcome='W' if kind in ('win','victory') else 'D' if kind=='draw' else 'L'
        score=sorted((int(match[1]),int(match[2])))
        require(any(r[1]==outcome and sorted(r[2:])==score for f in h2h_facts
                    if f['subject_team']==team for r in h2h_sample(f)[0]),
                'H2H score/result contradicts subject perspective')
