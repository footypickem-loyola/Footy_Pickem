"""Synthetic-only UNIQUE declaration compatibility and refusal tests.

The inline season case models the reported declaration form only. No private
DDL, database, checkpoint or real-data review packet is opened by these tests.
"""
from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch
import foundation_league_keys as keys
import test_foundation_migrations as frozen
from test_foundation_league_keys import LeagueKeysTests
from test_runtime_league_keys import CoexistenceTests


class DeclarationParserTests(unittest.TestCase):
    def test_inline_removes_only_keyword_preserving_other_constraints_and_literals(self):
        sql="""CREATE TABLE players (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE
        DEFAULT 'UNIQUE(name), literal ) ''quoted''' CHECK(length(name)>0) UNIQUE,
        unrelated TEXT UNIQUE, UNIQUE(id,name), CHECK(id>0))"""
        after=keys.remove_global_unique(sql,'name')
        self.assertEqual(after,sql.replace('CHECK(length(name)>0) UNIQUE','CHECK(length(name)>0) '))
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute(after)
            db.execute("INSERT INTO players VALUES(1,'Same','one')")
            db.execute("INSERT INTO players VALUES(2,'SAME','two')")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM players WHERE name='same'").fetchone()[0],2)
            db.execute("INSERT INTO players(id,unrelated) VALUES(3,'three')")
            self.assertEqual(db.execute('SELECT name FROM players WHERE id=3').fetchone()[0],"UNIQUE(name), literal ) 'quoted'")
            for values in ((4,None,'four'),(5,'','five'),(6,'valid','one'),(-1,'valid','negative')):
                with self.assertRaises(sqlite3.IntegrityError):db.execute('INSERT INTO players VALUES(?,?,?)',values)
            indexes=[tuple(r[2] for r in db.execute('PRAGMA index_info('+keys.quoted(i[1])+')')) for i in db.execute('PRAGMA index_list(players)').fetchall() if i[2]]
            self.assertIn(('unrelated',),indexes);self.assertIn(('id','name'),indexes)
            self.assertNotIn(('name',),indexes)

    def test_table_level_preserves_other_unique_and_nested_expressions(self):
        sql="""CREATE TABLE seasons (id INTEGER PRIMARY KEY,
        code TEXT NOT NULL CHECK(instr(code, 'UNIQUE(code), )')>=0), name TEXT UNIQUE,
        UNIQUE (code), UNIQUE(id,code))"""
        self.assertEqual(keys.remove_global_unique(sql,'code'),sql.replace(',\n        UNIQUE (code)',''))

    def test_quoted_identifiers_and_inline_constraint_order(self):
        for key,table in [('name','players'),('code','seasons')]:
            for quoted in ('"'+key+'"','['+key+']','`'+key+'`'):
                inline=f'CREATE TABLE {table} (id INTEGER PRIMARY KEY, {quoted} TEXT UNIQUE NOT NULL)'
                self.assertEqual(keys.remove_global_unique(inline,key),inline.replace('UNIQUE','',1))
                body=f'CREATE TABLE {table} (id INTEGER PRIMARY KEY, {quoted} TEXT NOT NULL, UNIQUE ({quoted}))'
                self.assertEqual(keys.remove_global_unique(body,key),body.replace(f', UNIQUE ({quoted})',''))

    def test_ambiguous_duplicated_missing_or_decorated_targets_refuse(self):
        declarations=[
            'name TEXT UNIQUE, UNIQUE(name)',
            'name TEXT, UNIQUE(name), UNIQUE(name)',
            'name TEXT UNIQUE UNIQUE',
            'name TEXT UNIQUE ON CONFLICT IGNORE',
            'name TEXT CONSTRAINT named_key UNIQUE',
            'name TEXT, CONSTRAINT named_key UNIQUE(name)',
            'name TEXT, UNIQUE(name COLLATE NOCASE)',
            'name TEXT, UNIQUE(name DESC)',
            "name TEXT DEFAULT 'UNIQUE(name)'",
            'name TEXT, UNIQUE(id,name)',
            'name TEXT UNIQUE /* comment */',
            'name TEXT UNIQUE -- comment\n',
        ]
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                with self.assertRaisesRegex(keys.CompatibilityError,'UNSUPPORTED_UNIQUE_DECLARATION'):
                    keys.remove_global_unique('CREATE TABLE players (id INTEGER PRIMARY KEY, '+declaration+')','name')
        for sql in ("CREATE TABLE players '(' name TEXT UNIQUE ')'",
                    'CREATE TABLE players (name TEXT UNIQUE',
                    "CREATE TABLE players (name TEXT DEFAULT 'unclosed UNIQUE)"):
            with self.assertRaises(keys.CompatibilityError):keys.remove_global_unique(sql,'name')


def legacy_layout(player_inline,season_inline):
    """Independent edits to the public frozen synthetic pre-Stage-1 DDL."""
    ddl=frozen.LEGACY_DDL
    for table,key,inline in [('players','name',player_inline),('seasons','code',season_inline)]:
        start=ddl.index('CREATE TABLE '+table+' (');end=ddl.index('\n);',start)+3
        original=ddl[start:end];block=original
        if inline:
            target=', \n\tUNIQUE ('+key+')'
            if block.count(target)!=1:raise AssertionError('Unexpected frozen test constraint')
            block=block.replace(target,'',1)
        column=key+' VARCHAR NOT NULL'
        if block.count(column)!=1:raise AssertionError('Unexpected frozen test column')
        replacement=column+" COLLATE NOCASE DEFAULT 'UNIQUE("+key+"), literal' CHECK(length("+key+")>0)"+(' UNIQUE' if inline else '')
        block=block.replace(column,replacement,1)
        block=block[:-3]+',\n\tUNIQUE (id,'+key+')'+block[-3:]
        ddl=ddl[:start]+block+ddl[end:]
    return ddl


class DeclarationLayoutTests(unittest.TestCase):
    def check_layout(self,player_inline,season_inline):
        with patch.object(frozen,'LEGACY_DDL',legacy_layout(player_inline,season_inline)):
            case=LeagueKeysTests('test_rebuild_preserves_rows_foreign_keys_indexes_receipts_and_idempotency')
            try:
                case.setUp()
                case.test_atomic_rollback_after_each_drop_preserves_everything()
                case.test_rebuild_preserves_rows_foreign_keys_indexes_receipts_and_idempotency()
                with closing(sqlite3.connect(case.path)) as db:
                    for table,key in [('players','name'),('seasons','code')]:
                        ddl=db.execute('SELECT sql FROM sqlite_schema WHERE name=?',(table,)).fetchone()[0]
                        self.assertIn('COLLATE NOCASE',ddl)
                        self.assertIn("DEFAULT 'UNIQUE("+key+"), literal'",ddl)
                        self.assertIn('CHECK(length('+key+')>0)',ddl)
                        indexes=[tuple(r[2] for r in db.execute('PRAGMA index_info('+keys.quoted(i[1])+')')) for i in db.execute('PRAGMA index_list('+table+')').fetchall() if i[2]]
                        self.assertIn(('id',key),indexes)
                        self.assertNotIn((key,),indexes)
            finally:case.doCleanups()
            for method in ('test_duplicate_names_codes_shared_result_points_records_and_payouts',
                           'test_shared_future_fixture_is_drafted_independently'):
                coexist=CoexistenceTests(method)
                try:
                    coexist.setUp();getattr(coexist,method)()
                finally:coexist.doCleanups()

    def test_inline_season_table_player_reported_form(self):self.check_layout(False,True)
    def test_inline_player_table_season(self):self.check_layout(True,False)
    def test_both_inline(self):self.check_layout(True,True)

    def test_sqlite_coalesced_duplicate_declarations_refuse_preparation(self):
        ddl=legacy_layout(False,True).replace('CHECK(length(name)>0)', 'CHECK(length(name)>0) UNIQUE',1)
        case=LeagueKeysTests('test_rebuild_preserves_rows_foreign_keys_indexes_receipts_and_idempotency')
        with patch.object(frozen,'LEGACY_DDL',ddl):
            try:
                with self.assertRaisesRegex(keys.CompatibilityError,'UNSUPPORTED_UNIQUE_DECLARATION'):
                    case.setUp()
                with closing(sqlite3.connect(case.path)) as db:
                    self.assertEqual(keys.verify_state(db,**dict(case.evidence,require_completed=True))['status'],'stage2_complete')
                    self.assertFalse(db.execute('SELECT 1 FROM schema_migrations WHERE version=?',(keys.VERSION,)).fetchone())
            finally:case.doCleanups()


if __name__=='__main__':unittest.main()
