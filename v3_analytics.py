"""Deterministic presentation built from official finalized-week helper outputs."""

COLORS = ('#245fc5', '#167047', '#a45116', '#7d44a4', '#b3343e', '#247986')


def position_chart(players, snapshots):
    weeks = sorted(snapshots)
    width = max(320, len(weeks) * 36 + 80)
    def x(week):
        if len(weeks) == 1:
            return width / 2
        return 48 + (week - weeks[0]) / max(1, weeks[-1] - weeks[0]) * (width - 140)
    def y(rank):
        return 28 + (rank - 1) / max(1, len(players) - 1) * 220
    series = []
    for index, player in enumerate(players):
        points = []
        for week in weeks:
            row = next((r for r in snapshots[week] if r['player_id'] == player.id), None)
            if row is not None:
                points.append({'week': week, 'rank': row['rank'], 'x': x(week), 'y': y(row['rank'])})
        series.append({'id': player.id, 'name': player.name, 'color': COLORS[index % len(COLORS)],
                       'points': points, 'path': ' '.join(f"{p['x']},{p['y']}" for p in points)})
    label_counts = {}
    for item in series:
        if item['points']:
            last = item['points'][-1]
            offset = label_counts.get(last['rank'], 0)
            item['label_x'] = last['x'] + 12 + offset * 16
            item['label_y'] = last['y'] + 4
            item['initial'] = item['name'][:1].upper()
            label_counts[last['rank']] = offset + 1
    return {'width': width, 'weeks': [{'number': w, 'x': x(w)} for w in weeks],
            'ranks': [{'number': rank, 'y': y(rank)} for rank in range(1, len(players) + 1)],
            'series': series}


def performance_chart(snapshots, player_id):
    """Plot cumulative official net, rather than summing matchup margins."""
    values = [(week, row['net_points']) for week, rows in sorted(snapshots.items())
              for row in rows if row['player_id'] == player_id]
    low = min([0] + [v for _, v in values])
    high = max([1] + [v for _, v in values])
    width = max(320, len(values) * 36 + 70)
    points = [{'week': week, 'net': value,
               'x': width / 2 if len(values) == 1 else 40 + index * (width - 60) / max(1, len(values) - 1),
               'y': 22 + (high - value) * 150 / (high - low)}
              for index, (week, value) in enumerate(values)]
    ticks = sorted({low, 0, high, round((low + high) / 2)})
    return dict(width=width, points=points, path=' '.join(f"{p['x']},{p['y']}" for p in points),
                ticks=[dict(value=v, y=22 + (high - v) * 150 / (high - low)) for v in ticks])


def matchup_matrix(players, meetings):
    rows = []
    for player in players:
        cells = []
        for opponent in players:
            games = [g for g in meetings if {g['a'], g['b']} == {player.id, opponent.id}]
            nets = [g['net'] if g['a'] == player.id else -g['net'] for g in games]
            wins, losses, draws = (sum(n > 0 for n in nets), sum(n < 0 for n in nets), sum(n == 0 for n in nets))
            cells.append(dict(opponent=opponent.name, played=len(games), wins=wins, losses=losses, draws=draws,
                              self=player.id == opponent.id, tone='win' if wins > losses else 'loss' if losses > wins else 'draw'))
        rows.append(dict(name=player.name, cells=cells))
    return rows


def recent_form(players, meetings):
    rows = []
    for player in players:
        history = []
        for game in sorted(meetings, key=lambda g: g['week']):
            if player.id not in (game['a'], game['b']):
                continue
            net = game['net'] if player.id == game['a'] else -game['net']
            history.append({'week': game['week'], 'net': net,
                            'outcome': 'W' if net > 0 else 'L' if net < 0 else 'D'})
        rows.append({'id': player.id, 'name': player.name, 'history': history,
                     'recent': history[-5:], 'recent_net': sum(h['net'] for h in history[-5:]),
                     'wins': sum(h['outcome'] == 'W' for h in history),
                     'losses': sum(h['outcome'] == 'L' for h in history),
                     'ties': sum(h['outcome'] == 'D' for h in history)})
    return rows


def club_records(records_by_player, player_id=None, club='', minimum=1, sort='best'):
    """Aggregate established per-player club records; never evaluate picks again."""
    grouped = {}
    for pid, records in records_by_player.items():
        if player_id is not None and pid != player_id:
            continue
        for record in records:
            row = grouped.setdefault(record['club'], dict(club=record['club'], picks=0,
                correct=0, incorrect=0, draws=0, net=0))
            for key in ('picks', 'correct', 'incorrect', 'draws', 'net'):
                row[key] += record[key]
    names = sorted(grouped, key=str.lower)
    rows = []
    for row in grouped.values():
        decisions = row['correct'] + row['incorrect']
        row['accuracy'] = row['correct'] / decisions if decisions else 0
        row['accuracy_display'] = f"{row['accuracy'] * 100:.1f}%" if decisions else '—'
        if row['picks'] >= minimum and (not club or row['club'] == club):
            rows.append(row)
    keys = {
        'most': lambda r: (-r['picks'], -r['net'], r['club'].lower()),
        'worst': lambda r: (r['net'], r['accuracy'], -r['picks'], r['club'].lower()),
        'best': lambda r: (-r['net'], -r['accuracy'], -r['picks'], r['club'].lower()),
    }
    return sorted(rows, key=keys.get(sort, keys['best'])), names


def personal_summary(standings, form, player_id):
    official = next((row for row in standings if row['player_id'] == player_id), None)
    if official is None:
        return None
    record = next(row for row in form if row['id'] == player_id)
    picks = official['correct'] + official['incorrect'] + official['draws']
    return dict(official, record=record,
                correct_percentage=f"{official['correct'] / picks * 100:.1f}%" if picks else '—')


def weekly_results(players, meetings, records_by_week, player_id):
    """Present official finalized pick records from the selected player's perspective."""
    names = {player.id: player.name for player in players}
    empty = dict(correct=0, incorrect=0, draws=0)
    rows = []
    for game in sorted(meetings, key=lambda g: g['week'], reverse=True):
        if player_id not in (game['a'], game['b']):
            continue
        opponent_id = game['b'] if player_id == game['a'] else game['a']
        records = records_by_week.get(game['week'], {})
        own_record = records.get(player_id, empty)
        matchup_net = game['net'] if player_id == game['a'] else -game['net']
        rows.append(dict(week=game['week'], opponent=names.get(opponent_id, 'Unknown player'),
                         total_net=matchup_net,
                         own=own_record, opponent_record=records.get(opponent_id, empty)))
    return rows


def explorer(h2h, meetings, player_a, player_b):
    if player_a is None or player_b is None:
        return None
    record = next((row for row in h2h if row['opponent'] == player_b.name), None)
    games = []
    for game in reversed(sorted(meetings, key=lambda g: g['week'])):
        if {game['a'], game['b']} == {player_a.id, player_b.id}:
            own = game['a'] == player_a.id
            games.append({'week': game['week'], 'for': game['for'] if own else game['against'],
                          'against': game['against'] if own else game['for'],
                          'net': game['net'] if own else -game['net']})
    return {'a': player_a.name, 'b': player_b.name, 'record': record, 'meetings': games}
