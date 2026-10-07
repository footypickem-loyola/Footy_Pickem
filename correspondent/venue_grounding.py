"""Closed cited-name vocabulary for named grounds, not a stadium blacklist."""
import re

from pre_match_brief import require
from .football_claim_checks import reference_aliases as aliases, MONTH

VENUE_KEYS={'venue_name','stadium_name','ground_name'}
VENUE_OBJECTS={'venue','stadium','ground'}
NAME=r"[A-ZÀ-Þ][\w’'.-]*(?:\s+(?:(?:of|the|and)\s+)?[A-ZÀ-Þ][\w’'.-]*)*"


def cited_venues(value):
    names=set()
    if isinstance(value,dict):
        for key,item in value.items():
            if key in VENUE_KEYS and isinstance(item,str) and item.strip():names.add(item.strip())
            if key in VENUE_OBJECTS:
                if isinstance(item,str) and item.strip():names.add(item.strip())
                elif isinstance(item,dict) and isinstance(item.get('name'),str):names.add(item['name'].strip())
            if isinstance(item,(dict,list)):names.update(cited_venues(item))
    elif isinstance(value,list):
        for item in value:names.update(cited_venues(item))
    return names


def validate_venues(text,facts):
    venues=set();homes=set();clubs=set();home_away=set()
    for f in facts:
        # Claims/instructions are not authoritative venue-name fields.
        for section in ('evidence','provenance'):venues.update(cited_venues(f.get(section,{})))
        subject=f.get('subject_team')
        for row in f.get('provenance',{}).get('fixtures',[]):
            homes.update(aliases(row.get('home_team')))
            clubs.update(aliases(row.get('home_team'))|aliases(row.get('away_team')))
            if subject in aliases(row.get('home_team')):home_away.add('home')
            if subject in aliases(row.get('away_team')):home_away.add('away')
        for row in f.get('provenance',{}).get('rows',[]):
            if row.get('home') is True:
                homes.update(aliases(subject));home_away.add('home')
            if row.get('home') is False:home_away.add('away')
        clubs.update(aliases(subject))

    def check(name,ordinary_clubs=()):
        name=name.strip().rstrip('.')
        # Possessive club locatives ("in Fulham's ...") are club references.
        club=re.sub(r"[’']s$",'',name)
        require(club in venues or club in ordinary_clubs,'Venue name absent from cited structured facts')

    # Any name in a locative construction requires a cited venue or supported
    # club location. Dates/minutes and temporal idioms are not ground names.
    for m in re.finditer(r'\b(at|inside|outside)\s+(?:the\s+)?',text,re.I):
        tail=text[m.end():]
        if re.match(r'\d{1,3}(?:\+\d{1,2})?\b|(?:kickoff|half.time|full.time|the cutoff|cutoff|this stage|that stage|the start|start|the end|end)\b',tail,re.I):continue
        basic=re.match(r'(home|away)\b',tail,re.I)
        if basic:
            require(basic[1].lower() in home_away,'Home/away location lacks cited support');continue
        name=re.match(NAME,tail)
        if name:check(name[0],homes)
        else:
            # Lowercase names must not bypass the guard. No inference that a
            # lowercase unknown location is a harmless temporal expression.
            require(False,'Venue name absent from cited structured facts')
    # Named locations after "in", or a ground used as the subject of an event.
    for m in re.finditer(r'(?i:\bin\s+(?:the\s+)?)('+NAME+r')',text):
        name=m[1].rstrip('.')
        if re.fullmatch(MONTH,name,re.I) or name in {'Premier League','PL'}:continue
        check(name,clubs)
    for m in re.finditer(r'\b('+NAME+r')\s+(?:hosted|staged|witnessed|welcomed)\b',text):
        check(m[1])
    # Explicit named stadium/ground constructions, even without a preposition.
    for m in re.finditer(r'\b('+NAME+r'\s+(?:Stadium|Ground|stadium|ground))\b',text):
        check(m[1])
    for m in re.finditer(r'\b('+NAME+r')\s+(?:win|draw|defeat|meeting|clash|encounter|crowd|pitch|stands)\b',text):
        check(m[1],clubs)
