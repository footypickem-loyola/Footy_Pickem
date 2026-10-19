"""Execute only the existing scoring functions against read-only row snapshots.

No Flask import, application initialization, ORM engine, network or database
writes. The small query adapter implements just the operators used by the seven
reviewed functions. Unsupported operations fail rather than approximating them.
"""
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from foundation_migrations import digest, quoted

FUNCTIONS = (
    'season_players', 'weekly_points_map', 'pick_contribution',
    'weekly_for_against', 'weekly_pick_records', 'standings_through_week',
    'payouts_for_week',
)
TABLES = {'Player': 'players', 'Season': 'seasons', 'Week': 'weeks',
          'Fixture': 'fixtures', 'Matchup': 'matchups', 'Pick': 'picks',
          'Result': 'results'}
JOINS = {('Matchup', 'Week'): 'week_id', ('Pick', 'Matchup'): 'matchup_id',
         ('Result', 'Fixture'): 'fixture_id'}


class _Field:
    def __init__(self, model, name):
        self.model, self.name = model, name

    def value(self, row):
        return getattr(row[self.model], self.name)

    def __eq__(self, value):
        return lambda row: self.value(row) == value

    def __le__(self, value):
        return lambda row: self.value(row) <= value

    def in_(self, values):
        return lambda row: self.value(row) in values

    def asc(self):
        return self


class _Query:
    def __init__(self, store, model):
        self.store, self.model = store, model
        self.rows = [{model: row} for row in store.rows[model].values()]

    def join(self, other):
        other = next(name for name, value in self.store.models.items() if value is other)
        key = JOINS[self.model, other]
        self.rows = [dict(row, **{other: self.store.rows[other][getattr(row[self.model], key)]})
                     for row in self.rows]
        return self

    def filter(self, *conditions):
        self.rows = [row for row in self.rows if all(condition(row) for condition in conditions)]
        return self

    def filter_by(self, **values):
        self.rows = [row for row in self.rows if
                     all(getattr(row[self.model], key) == value for key, value in values.items())]
        return self

    def order_by(self, field):
        self.rows.sort(key=field.value)
        return self

    def all(self):
        return [row[self.model] for row in self.rows]

    def __iter__(self):
        return iter(self.all())


class _Rows:
    def __init__(self, db):
        self.rows, self.models = {}, {}
        for model, table in TABLES.items():
            cursor = db.execute('SELECT * FROM ' + quoted(table) + ' ORDER BY id')
            columns = [c[0] for c in cursor.description]
            records = [dict(zip(columns, row)) for row in cursor]
            self.rows[model] = {row['id']: SimpleNamespace(**row) for row in records}
            self.models[model] = SimpleNamespace(**{c: _Field(model, c) for c in columns})
        for pick in self.rows['Pick'].values():
            pick.fixture = self.rows['Fixture'][pick.fixture_id]
        for match in self.rows['Matchup'].values():
            match.player_a = self.rows['Player'][match.player_a_id]
            match.player_b = self.rows['Player'][match.player_b_id]

    def query(self, model):
        name = next(name for name, value in self.models.items() if value is model)
        return _Query(self, name)


def scoring_evidence(db):
    """Hash actual weekly points/records, every standings cutoff, and payouts.

    Full output stays in memory. Only counts, source-function digest and output
    digest escape; no names, addresses, picks or private context are reported.
    """
    source = Path(__file__).with_name('pickem_flask_htmx_tabs.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS]
    if {node.name for node in nodes} != set(FUNCTIONS) or any(node.decorator_list for node in nodes):
        raise ValueError('SCORING_FUNCTION_CONTRACT_CHANGED')
    function_hash = digest([ast.dump(node, include_attributes=False) for node in nodes])
    rows = _Rows(db)
    namespace = dict(rows.models)
    # Defer type annotations; execute functions only, never application top level.
    module = ast.Module(body=[ast.ImportFrom(module='__future__',
                         names=[ast.alias(name='annotations')], level=0)] + nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), namespace)
    # Memoize per-week outputs within this one immutable row snapshot.
    for name in ('weekly_points_map', 'weekly_for_against', 'weekly_pick_records'):
        function, cache = namespace[name], {}
        def cached(db, week, function=function, cache=cache):
            if week.id not in cache:
                cache[week.id] = function(db, week)
            return deepcopy(cache[week.id])
        namespace[name] = cached
    output = []
    for week in rows.rows['Week'].values():
        season = rows.rows['Season'][week.season_id]
        output.append(dict(week_id=week.id,
                           points=namespace['weekly_points_map'](rows, week),
                           for_against=namespace['weekly_for_against'](rows, week),
                           records=namespace['weekly_pick_records'](rows, week),
                           standings=namespace['standings_through_week'](rows, season, week.number),
                           payouts=namespace['payouts_for_week'](rows, week)))
    return dict(functions_sha256=function_hash, outputs_sha256=digest(output),
                weeks=len(output), seasons=len(rows.rows['Season']),
                picks=len(rows.rows['Pick']), results=len(rows.rows['Result']))
