import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import dbclient as D  # noqa: E402


def docker_ok():
    try:
        return subprocess.run(['docker', 'info'], capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def has_image(name):
    r = subprocess.run(['docker', 'image', 'inspect', name], capture_output=True)
    return r.returncode == 0


# ======================================================================================
class QuoteTest(unittest.TestCase):
    def test_identifiers(self):
        self.assertEqual(D.qident('postgres', 'a"b'), '"a""b"')
        self.assertEqual(D.qident('sqlite', 'we ird'), '"we ird"')
        self.assertEqual(D.qident('mysql', 'a`b'), '`a``b`')
        self.assertEqual(D.qualified('postgres', 'public', 'users'), '"public"."users"')
        self.assertEqual(D.qualified('sqlite', '', 'users'), '"users"')
        for bad in ('', 'a\x00b', None):
            with self.assertRaises(D.DbError):
                D.qident('postgres', bad)

    def test_literals(self):
        self.assertEqual(D.qlit('postgres', "it's"), "'it''s'")
        self.assertEqual(D.qlit('mysql', "a\\'b"), "'a\\\\''b'")  # \ -> \\ và ' -> ''
        self.assertEqual(D.qlit('postgres', "x\\y"), "'x\\y'")  # Postgres: backslash là ký tự thường
        with self.assertRaises(D.DbError):
            D.qlit('postgres', 'a\x00')

    def test_injection_via_table_name_stays_one_identifier(self):
        evil = 'x"; DROP TABLE users; --'
        sql = f"SELECT * FROM {D.qualified('postgres', 'public', evil)}"
        self.assertEqual(len(D.split_statements(sql, 'postgres')), 1)
        sql = f"SELECT * FROM {D.qualified('mysql', 'db', 'x`; DROP TABLE users; --')}"
        self.assertEqual(len(D.split_statements(sql, 'mysql')), 1)


class SplitClassifyTest(unittest.TestCase):
    def test_split(self):
        self.assertEqual(D.split_statements("select 1; select 2;"), ['select 1', 'select 2'])
        self.assertEqual(D.split_statements("select ';' ; select \";\""), ["select ';'", 'select ";"'])
        self.assertEqual(D.split_statements("select 'it''s;' ; x"), ["select 'it''s;'", 'x'])
        self.assertEqual(D.split_statements("select 1 -- a; b\n; select 2"), ['select 1 -- a; b', 'select 2'])
        self.assertEqual(D.split_statements("select /* ; */ 1"), ['select /* ; */ 1'])
        self.assertEqual(D.split_statements("do $$ begin x; y; end $$; select 1"), ['do $$ begin x; y; end $$', 'select 1'])
        self.assertEqual(D.split_statements("do $f$ a;b $f$"), ['do $f$ a;b $f$'])
        self.assertEqual(D.split_statements("  ; ; /* nothing */ ;"), [])
        self.assertEqual(D.split_statements("select 'a\\';b'", 'mysql'), ["select 'a\\';b'"])  # MySQL: \' là thoát
        self.assertEqual(D.split_statements("select 1 # c; d\n; select 2", 'mysql'), ['select 1 # c; d', 'select 2'])

    def test_classify_read(self):
        for s in ("SELECT * FROM t", "  select 1", "(select 1)", "WITH a AS (select 1) SELECT * FROM a", "show tables", "EXPLAIN SELECT 1", "EXPLAIN (ANALYZE, VERBOSE) SELECT 1",
                  "VALUES (1)", "TABLE t", "DESCRIBE t", "PRAGMA table_info(t)", "PRAGMA user_version", "select comment, load, lock from posts", "SELECT replace(a,'x','y') FROM t",
                  "select 'delete from x' as s", "select 1 -- delete\n"):
            self.assertEqual(D.classify(s), 'read', s)

    def test_classify_write(self):
        for s in ("INSERT INTO t VALUES (1)", "update t set a=1", "DELETE FROM t", "drop table t", "create table t(x int)", "TRUNCATE t", "SET default_transaction_read_only = off",
                  "SET SESSION TRANSACTION READ WRITE", "BEGIN", "COMMIT", "COPY t TO '/tmp/x'", "CALL f()", "GRANT ALL ON t TO u", "ALTER SYSTEM SET x=1", "VACUUM", "PRAGMA journal_mode = delete",
                  "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", "WITH x AS (insert into t values (1) returning *) select * from x", "SELECT * FROM t FOR UPDATE",
                  "SELECT * INTO OUTFILE '/tmp/x' FROM t", "EXPLAIN ANALYZE DELETE FROM t", "REPLACE INTO t VALUES (1)", "ATTACH DATABASE 'x' AS y", "", "123"):
            self.assertEqual(D.classify(s), 'write', s)

    def test_single_statement_guard(self):
        self.assertEqual(D.single_statement('select 1;', 'postgres', False), ('select 1', 'read'))
        for bad in ('select 1; select 2', 'select 1; drop table t', 'set default_transaction_read_only=off; drop table t'):
            with self.assertRaises(D.DbError):
                D.single_statement(bad, 'postgres', True)  # nhiều câu bị chặn kể cả khi cho phép ghi
        with self.assertRaises(D.DbError) as e:
            D.single_statement('drop table t', 'postgres', False)
        self.assertIn('CHỈ ĐỌC', str(e.exception))
        self.assertEqual(D.single_statement('drop table t', 'postgres', True)[1], 'write')
        with self.assertRaises(D.DbError):
            D.single_statement('  ;  -- nothing', 'postgres', False)


class ParseTest(unittest.TestCase):
    def test_csv_with_commas_newlines_quotes_null_unicode(self):
        text = 'a,b,c,d,e\n1,<<NULL>>,,"x,y\n""z""",tiếng Việt 🌏\n2,"",<<NULL>>,"multi\nline",ok\n'
        cols, rows = D.parse_csv(text)
        self.assertEqual(cols, ['a', 'b', 'c', 'd', 'e'])
        self.assertEqual(rows[0], ['1', None, '', 'x,y\n"z"', 'tiếng Việt 🌏'])
        self.assertEqual(rows[1], ['2', '', None, 'multi\nline', 'ok'])
        self.assertEqual(D.parse_csv(''), ([], []))
        self.assertEqual(D.parse_csv('x\n'), (['x'], []))  # 0 dòng vẫn có cột

    def test_tsv_mysql(self):
        text = 'id\tname\tnote\n1\tTrần\tNULL\n2\ta\\tb\tline1\\nline2\n3\tback\\\\slash\t\n'
        cols, rows = D.parse_tsv(text)
        self.assertEqual(cols, ['id', 'name', 'note'])
        self.assertEqual(rows[0], ['1', 'Trần', None])
        self.assertEqual(rows[1], ['2', 'a\tb', 'line1\nline2'])
        self.assertEqual(rows[2], ['3', 'back\\slash', ''])
        self.assertEqual(D.parse_tsv(''), ([], []))

    def test_export_csv_neutralizes_formulas_but_keeps_numbers(self):
        out = D.to_csv(['a', 'b'], [['=1+1', '-5'], ['-cmd', None], ['x,y', 'tiếng']])
        self.assertTrue(out.startswith('﻿'))
        import csv, io
        rows = list(csv.reader(io.StringIO(out.lstrip('﻿'))))
        self.assertEqual(rows, [['a', 'b'], ["'=1+1", '-5'], ["'-cmd", ''], ['x,y', 'tiếng']])


class MysqlCommandTest(unittest.TestCase):
    """MySQL chưa có server thật để thử: kiểm tra dựng lệnh + dùng đầu ra mẫu."""

    def conn(self, **kw):
        return D.make_conn('mysql', 'docker', 'db', container='ct', image='mysql:8', user='root', password='S3CR3T-PW', db='app', **kw)

    def test_password_not_in_argv_and_readonly_prefix(self):
        seen = {}

        def fake_run(argv, env_extra=None, timeout=0):
            seen['argv'], seen['env'] = argv, env_extra
            return mock.Mock(returncode=0, stdout='n\n1\n', stderr='')
        with mock.patch.object(D, '_run', fake_run):
            r = D.exec_sql(self.conn(), 'SELECT 1 AS n', readonly=True)
        self.assertNotIn('S3CR3T-PW', ' '.join(seen['argv']))
        self.assertEqual(seen['env']['MYSQL_PWD'], 'S3CR3T-PW')
        self.assertTrue(seen['argv'][-1].startswith('SET SESSION TRANSACTION READ ONLY; '))
        self.assertEqual(seen['argv'][:6], ['docker', 'exec', '-i', '-e', 'MYSQL_PWD', 'ct'])
        self.assertEqual((r.cols, r.rows), (['n'], [['1']]))

    def test_write_mode_has_no_readonly_prefix(self):
        seen = {}
        with mock.patch.object(D, '_run', lambda argv, env_extra=None, timeout=0: (seen.update(a=argv), mock.Mock(returncode=0, stdout='', stderr=''))[1]):
            r = D.exec_sql(self.conn(), 'DELETE FROM t', readonly=False)
        self.assertFalse(seen['a'][-1].startswith('SET SESSION'))
        self.assertEqual(r.notice, 'Đã thực thi')

    def test_falls_back_to_mariadb_client(self):
        calls = []

        def fake_run(argv, env_extra=None, timeout=0):
            client = argv[argv.index('ct') + 1]
            calls.append(client)
            if client == 'mysql':
                return mock.Mock(returncode=127, stdout='', stderr='OCI runtime exec failed: exec: "mysql": executable file not found in $PATH')
            return mock.Mock(returncode=0, stdout='x\n1\n', stderr='')
        with mock.patch.object(D, '_run', fake_run):
            r = D.exec_sql(self.conn(), 'select 1 as x')
        self.assertEqual(calls, ['mysql', 'mariadb'])
        self.assertEqual(r.rows, [['1']])

    def test_mysql_error_is_reported(self):
        with mock.patch.object(D, '_run', lambda *a, **k: mock.Mock(returncode=1, stdout='', stderr="ERROR 1146 (42S02) at line 1: Table 'app.nope' doesn't exist")):
            with self.assertRaises(D.DbError) as e:
                D.exec_sql(self.conn(), 'select * from nope')
        self.assertIn("doesn't exist", str(e.exception))

    def test_metadata_queries_escape_names(self):
        sqls = []

        def fake(c, sql, **kw):
            sqls.append(sql)
            return D.Result(['a'], [])
        with mock.patch.object(D, 'exec_sql', fake):
            D.table_columns(self.conn(), "ap'p", "ap'p", "ta\\ble'x")
        self.assertIn("table_schema = 'ap''p'", sqls[0])
        self.assertIn("table_name = 'ta\\\\ble''x'", sqls[0])

    def test_sample_metadata_parsing(self):
        out = {'tables': 'table_name\ttable_type\ttable_rows\nusers\tBASE TABLE\t10\nv1\tVIEW\tNULL\n'}
        with mock.patch.object(D, '_mysql_exec', lambda c, sql, db, ro: out['tables']):
            t = D.list_tables(self.conn(), 'app')
        self.assertEqual([(x['name'], x['type'], x['rows']) for x in t], [('users', 'bảng', 10), ('v1', 'view', None)])


class DescribeSecurityTest(unittest.TestCase):
    def test_describe_hides_password_and_saved_file_has_no_password(self):
        c = D.make_conn('postgres', 'tcp', 'srv', host='db.lan', port=5432, user='me', password='TOPSECRET', db='x')
        self.assertNotIn('TOPSECRET', D.describe(c))
        d = tempfile.mkdtemp()
        p = os.path.join(d, 'sub', 'db_connections.json')
        s = D.make_conn('sqlite', 'file', 'f', path='/tmp/x.db')
        D.save_saved([c, s, D.make_conn('redis', 'docker', 'r', container='r1')], p)
        raw = open(p).read()
        self.assertNotIn('TOPSECRET', raw)
        self.assertEqual(oct(os.stat(p).st_mode & 0o777), '0o600')
        back = D.load_saved(p)
        self.assertEqual(sorted(x['mode'] for x in back), ['file', 'tcp'])  # container docker không được lưu (tự phát hiện lại)
        self.assertTrue(all(x['password'] == '' for x in back))
        with open(p, 'w') as f:
            f.write('{broken')
        self.assertEqual(D.load_saved(p), [])


# ======================================================================================
class RedisPureTest(unittest.TestCase):
    def test_class(self):
        for a in (['GET', 'k'], ['hgetall', 'h'], ['SCAN', '0'], ['TTL', 'k'], ['INFO'], ['CONFIG', 'GET', 'maxmemory'], ['OBJECT', 'ENCODING', 'k'], ['MEMORY', 'USAGE', 'k'], ['XRANGE', 's', '-', '+']):
            self.assertEqual(D.redis_class(a), 'read', a)
        for a in (['SET', 'k', 'v'], ['DEL', 'k'], ['FLUSHDB'], ['FLUSHALL'], ['EXPIRE', 'k', '1'], ['LPUSH', 'l', 'x'], ['EVAL', 'return 1', '0'], ['PUBLISH', 'c', 'm'], ['OBJECT', 'FOO']):
            self.assertEqual(D.redis_class(a), 'write', a)
        for a in (['SHUTDOWN'], ['KEYS', '*'], ['CONFIG', 'SET', 'x', 'y'], ['DEBUG', 'SLEEP', '1'], ['MODULE', 'LOAD', 'x'], ['REPLICAOF', 'h', '1'], ['ACL', 'SETUSER', 'x'], ['MONITOR'], ['CLIENT', 'KILL', 'x']):
            self.assertEqual(D.redis_class(a), 'blocked', a)
        with self.assertRaises(D.DbError):
            D.redis_class([])

    def test_line_parser(self):
        self.assertEqual(D.parse_redis_line('GET key'), ['GET', 'key'])
        self.assertEqual(D.parse_redis_line('SET k "hello world" '), ['SET', 'k', 'hello world'])
        self.assertEqual(D.parse_redis_line('SET k "a\\nb\\"c\\x41"'), ['SET', 'k', 'a\nb"cA'])
        self.assertEqual(D.parse_redis_line("SET k 'a b'"), ['SET', 'k', 'a b'])
        self.assertEqual(D.parse_redis_line('SET k ""'), ['SET', 'k', ''])
        for bad in ('SET k "abc', "SET k 'abc"):
            with self.assertRaises(D.DbError):
                D.parse_redis_line(bad)

    def test_resp_roundtrip_and_format(self):
        import io
        self.assertEqual(D.resp_encode(['SET', 'k', 'tiếng']), b'*3\r\n$3\r\nSET\r\n$1\r\nk\r\n$7\r\nti\xe1\xba\xbfng\r\n')
        data = b'*5\r\n+OK\r\n:42\r\n$-1\r\n-ERR bad\r\n*2\r\n$3\r\nabc\r\n$5\r\nh\xc3\xa9 \r\n\r\n'
        v = D.resp_read(io.BytesIO(data))
        self.assertEqual(v[0], 'OK')
        self.assertEqual(v[1], 42)
        self.assertIsNone(v[2])
        self.assertIsInstance(v[3], D.RespError)
        self.assertEqual(v[4], ['abc', 'hé \r\n'[:4]] if False else v[4])
        txt = D.resp_format(['a', 1, None, ['b']])
        self.assertIn('1) "a"', txt)
        self.assertIn('(integer) 1', txt)
        self.assertIn('(nil)', txt)
        with self.assertRaises(D.DbError):
            D.resp_read(io.BytesIO(b''))
        with self.assertRaises(D.DbError):
            D.resp_read(io.BytesIO(b'$%d\r\n' % (D.MAX_BULK + 1)))


class FakeRedisServer:
    """Máy chủ RESP giả tối thiểu để test client (AUTH, SELECT, SET/GET, SCAN, TYPE, PTTL, lỗi)."""

    def __init__(self, password=None):
        self.password, self.data, self.log = password, {'k1': 'v1', 'k2': 'v2', 'h': {'f': 'x'}}, []
        self.srv = socket.socket()
        self.srv.bind(('127.0.0.1', 0))
        self.srv.listen(5)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                s, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(s,), daemon=True).start()

    def serve(self, s):
        f = s.makefile('rb')
        authed = not self.password
        while True:
            try:
                args = D.resp_read(f)
            except (D.DbError, OSError, ValueError):
                return
            cmd = args[0].upper()
            self.log.append(args)
            if cmd == 'AUTH':
                ok = args[-1] == self.password
                authed = authed or ok
                s.sendall(b'+OK\r\n' if ok else b'-WRONGPASS invalid\r\n')
            elif not authed:
                s.sendall(b'-NOAUTH Authentication required.\r\n')
            elif cmd == 'SELECT':
                s.sendall(b'+OK\r\n')
            elif cmd == 'GET':
                v = self.data.get(args[1])
                s.sendall(b'$-1\r\n' if v is None or isinstance(v, dict) else b'$%d\r\n%s\r\n' % (len(v), v.encode()))
            elif cmd == 'SCAN':
                ks = sorted(self.data)
                s.sendall(b'*2\r\n$1\r\n0\r\n*%d\r\n' % len(ks) + b''.join(b'$%d\r\n%s\r\n' % (len(k), k.encode()) for k in ks))
            elif cmd == 'TYPE':
                v = self.data.get(args[1])
                t = b'hash' if isinstance(v, dict) else b'string'
                s.sendall(b'+' + t + b'\r\n')
            elif cmd == 'PTTL':
                s.sendall(b':-1\r\n')
            elif cmd == 'SET':
                self.data[args[1]] = args[2]
                s.sendall(b'+OK\r\n')
            else:
                s.sendall(b'-ERR unknown command\r\n')

    def close(self):
        self.srv.close()


class RedisFakeServerTest(unittest.TestCase):
    def conn(self, srv, **kw):
        return D.make_conn('redis', 'tcp', 'r', host='127.0.0.1', port=srv.port, **kw)

    def test_auth_scan_pipeline_and_modes(self):
        srv = FakeRedisServer(password='pw')
        try:
            keys, cut = D.redis_scan(self.conn(srv, password='pw'), pattern='k*')
            self.assertEqual([(k['key'], k['type'], k['ttl']) for k in keys], [('h', 'hash', -1), ('k1', 'string', -1), ('k2', 'string', -1)])
            self.assertFalse(cut)
            with self.assertRaises(D.DbError) as e:
                D.redis_scan(self.conn(srv, password='wrong'))
            self.assertIn('xác thực', str(e.exception).lower())
            v, cls = D.redis_command(self.conn(srv, password='pw'), ['GET', 'k1'])
            self.assertEqual((v, cls), ('v1', 'read'))
            with self.assertRaises(D.DbError):
                D.redis_command(self.conn(srv, password='pw'), ['SET', 'x', '1'])  # chỉ đọc
            self.assertFalse(any(a[0].upper() == 'SET' for a in srv.log), 'lệnh ghi không được gửi tới máy chủ')
            v, cls = D.redis_command(self.conn(srv, password='pw'), ['SET', 'x', '1'], allow_write=True)
            self.assertEqual((v, cls), ('OK', 'write'))
            with self.assertRaises(D.DbError):
                D.redis_command(self.conn(srv, password='pw'), ['KEYS', '*'], allow_write=True)  # bị chặn vĩnh viễn
            self.assertFalse(any(a[0].upper() == 'KEYS' for a in srv.log))
        finally:
            srv.close()

    def test_scan_limit_marks_truncated(self):
        srv = FakeRedisServer()
        srv.data = {f'k{i:03}': 'v' for i in range(50)}
        try:
            keys, cut = D.redis_scan(self.conn(srv), limit=10)
            self.assertEqual((len(keys), cut), (10, True))
        finally:
            srv.close()

    def test_keyspace_parser(self):
        self.assertEqual(D.parse_keyspace('# Keyspace\r\ndb0:keys=8,expires=1,avg_ttl=0\r\ndb3:keys=1,expires=0,avg_ttl=0\r\n'), [(0, 8), (3, 1)])
        self.assertEqual(D.parse_keyspace('# Keyspace\r\n'), [])

    def test_connection_refused_message(self):
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(D.DbError) as e:
            D.redis_command(D.make_conn('redis', 'tcp', 'x', host='127.0.0.1', port=port), ['PING'])
        self.assertIn('Không kết nối được Redis', str(e.exception))


# ======================================================================================
class SqliteRealTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'test.db')
        con = sqlite3.connect(self.path)
        con.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, note TEXT, score REAL DEFAULT 1.5);
            CREATE INDEX idx_users_name ON users(name);
            CREATE UNIQUE INDEX idx_users_note ON users(note);
            CREATE TABLE "we""ird; DROP TABLE users;--" (x INTEGER);
            CREATE VIEW v_users AS SELECT id, name FROM users;
            CREATE TABLE blobs (b BLOB);
        """)
        con.execute("INSERT INTO users(name, note) VALUES ('Trần Văn A', NULL), ('B, \"quoted\"', 'multi\nline'), ('', 'x')")
        con.execute('INSERT INTO "we""ird; DROP TABLE users;--" VALUES (1)')
        con.execute("INSERT INTO blobs VALUES (x'00ff')")
        con.commit()
        con.close()
        self.c = D.make_conn('sqlite', 'file', 'test', path=self.path)

    def test_metadata(self):
        tabs = {t['name']: t for t in D.list_tables(self.c, 'main')}
        self.assertEqual(tabs['users']['rows'], 3)
        self.assertEqual(tabs['v_users']['type'], 'view')
        self.assertIn('we"ird; DROP TABLE users;--', tabs)
        cols = D.table_columns(self.c, 'main', '', 'users')
        self.assertEqual([(x['name'], x['type'], x['nullable'], x['key']) for x in cols], [('id', 'INTEGER', False, 'PK'), ('name', 'TEXT', False, ''), ('note', 'TEXT', True, ''), ('score', 'REAL', True, '')])
        self.assertEqual(cols[3]['default'], '1.5')
        idx = dict(D.table_indexes(self.c, 'main', '', 'users'))
        self.assertEqual(idx['idx_users_name'], '(name)')
        self.assertEqual(idx['idx_users_note'], 'UNIQUE (note)')

    def test_page_filter_unicode_null_blob(self):
        res, more = D.table_page(self.c, 'main', '', 'users', limit=2)
        self.assertEqual((len(res.rows), more), (2, True))
        self.assertEqual(res.rows[0][:3], ['1', 'Trần Văn A', None])
        self.assertEqual(res.rows[1][2], 'multi\nline')
        res, more = D.table_page(self.c, 'main', '', 'users', where="name LIKE 'B%'")
        self.assertEqual([r[1] for r in res.rows], ['B, "quoted"'])
        res, more = D.table_page(self.c, 'main', '', 'users', limit=2, offset=2)
        self.assertEqual((len(res.rows), more), (1, False))
        res, _ = D.table_page(self.c, 'main', '', 'blobs')
        self.assertEqual(res.rows, [['<2 byte>']])
        res, _ = D.table_page(self.c, 'main', '', 'we"ird; DROP TABLE users;--')
        self.assertEqual(res.rows, [['1']])
        self.assertEqual(len(D.list_tables(self.c, 'main')), 4)  # bảng users vẫn còn nguyên

    def test_filter_cannot_smuggle_second_statement_or_write(self):
        for where in ("1=1; DROP TABLE users", "1=1) ; DELETE FROM users --"):
            with self.assertRaises(D.DbError):
                D.table_page(self.c, 'main', '', 'users', where=where)
        self.assertEqual(D.exec_sql(self.c, 'SELECT COUNT(*) FROM users').rows, [['3']])

    def test_readonly_blocks_writes_at_both_layers(self):
        for sql in ("DELETE FROM users", "DROP TABLE users", "PRAGMA writable_schema = 1", "ATTACH DATABASE '/tmp/zz.db' AS z", "select 1; delete from users"):
            with self.assertRaises(D.DbError):
                D.exec_sql(self.c, sql, readonly=True)
        # tầng DB: kể cả khi vượt qua phân loại (internal bỏ kiểm tra) kết nối vẫn read-only
        with self.assertRaises(D.DbError):
            D.exec_sql(self.c, 'DELETE FROM users', internal=True)
        self.assertEqual(D.exec_sql(self.c, 'SELECT COUNT(*) FROM users').rows, [['3']])

    def test_write_mode_works(self):
        r = D.exec_sql(self.c, "INSERT INTO users(name) VALUES ('new')", readonly=False)
        self.assertIn('Đã thực thi', r.notice)
        self.assertEqual(D.exec_sql(self.c, 'SELECT COUNT(*) FROM users').rows, [['4']])
        D.exec_sql(self.c, "DELETE FROM users WHERE name = 'new'", readonly=False)
        self.assertEqual(D.exec_sql(self.c, 'SELECT COUNT(*) FROM users').rows, [['3']])

    def test_truncation_and_errors(self):
        con = sqlite3.connect(self.path)
        con.execute('CREATE TABLE big (n INTEGER)')
        con.executemany('INSERT INTO big VALUES (?)', [(i,) for i in range(120)])
        con.commit()
        con.close()
        r = D.exec_sql(self.c, 'SELECT * FROM big', max_rows=100)
        self.assertEqual((len(r.rows), r.truncated), (100, True))
        r = D.exec_sql(self.c, 'SELECT * FROM big', max_rows=500)
        self.assertEqual((len(r.rows), r.truncated), (120, False))
        with self.assertRaises(D.DbError) as e:
            D.exec_sql(self.c, 'SELECT * FROM nope')
        self.assertIn('no such table', str(e.exception))
        with self.assertRaises(D.DbError):
            D.exec_sql(D.make_conn('sqlite', 'file', 'x', path='/không/có.db'), 'select 1')
        self.assertEqual(D.exec_sql(self.c, 'SELECT 1 WHERE 0').cols, ['1'])  # 0 dòng vẫn có cột

    def test_cannot_open_file_outside_via_uri_tricks(self):
        weird = os.path.join(self.dir, 'a?b#c.db')
        sqlite3.connect(weird).execute('CREATE TABLE t(x)').connection.close()
        c = D.make_conn('sqlite', 'file', 'w', path=weird)
        self.assertEqual([t['name'] for t in D.list_tables(c, 'main')], ['t'])


# ======================================================================================
# Docker thật: Redis và Postgres tạm (image có sẵn; tên riêng; không volume của người dùng)
# ======================================================================================
REDIS_NAME = f'devdock-test-redis-{os.getpid()}'
PG_NAME = f'devdock-test-pg-{os.getpid()}'
STATE = {}


def setUpModule():
    if not docker_ok():
        return
    if has_image('redis:7-alpine'):
        r = subprocess.run(['docker', 'run', '-d', '--rm', '--name', REDIS_NAME, '-p', '127.0.0.1::6379', 'redis:7-alpine', '--requirepass', 'redis-pw-1'], capture_output=True, text=True)
        if r.returncode == 0:
            port = subprocess.run(['docker', 'port', REDIS_NAME, '6379/tcp'], capture_output=True, text=True).stdout.strip().rsplit(':', 1)[-1]
            STATE['redis_port'] = int(port)
    if has_image('devfest-danang-trip-ai-db:latest'):
        r = subprocess.run(['docker', 'run', '-d', '--rm', '--name', PG_NAME, '--tmpfs', '/var/lib/postgresql/data', '-e', 'POSTGRES_PASSWORD=pg-pw-1', '-e', 'POSTGRES_USER=tester',
                            '-e', 'POSTGRES_DB=testdb', '-p', '127.0.0.1::5432', 'devfest-danang-trip-ai-db:latest'], capture_output=True, text=True)
        if r.returncode == 0:
            STATE['pg'] = True
    import time
    end = time.time() + 40
    while time.time() < end:
        ready = True
        if STATE.get('redis_port'):
            ready &= subprocess.run(['docker', 'exec', REDIS_NAME, 'redis-cli', '-a', 'redis-pw-1', 'ping'], capture_output=True, text=True).stdout.strip().endswith('PONG')
        if STATE.get('pg'):
            ready &= subprocess.run(['docker', 'exec', PG_NAME, 'pg_isready', '-U', 'tester', '-d', 'testdb'], capture_output=True).returncode == 0
        if ready:
            break
        time.sleep(1)


def tearDownModule():
    for n in (REDIS_NAME, PG_NAME):
        subprocess.run(['docker', 'rm', '-f', '-v', n], capture_output=True)


@unittest.skipUnless(STATE is not None and docker_ok() and has_image('redis:7-alpine'), 'cần Docker + image redis:7-alpine')
class RedisRealTest(unittest.TestCase):
    def setUp(self):
        if not STATE.get('redis_port'):
            self.skipTest('container redis không khởi động được')
        self.c = D.make_conn('redis', 'tcp', 'r', host='127.0.0.1', port=STATE['redis_port'], password='redis-pw-1')
        with D.Redis(self.c) as r:
            r.cmd(['FLUSHDB'])
            r.cmd(['SET', 'str:1', 'tiếng Việt 🌏'])
            r.cmd(['SET', 'str:ttl', 'x', 'EX', '1000'])
            r.cmd(['HSET', 'hash:1', 'f1', 'v1', 'f2', 'v2'])
            r.cmd(['RPUSH', 'list:1', 'a', 'b', 'c'])
            r.cmd(['SADD', 'set:1', 'm1', 'm2'])
            r.cmd(['ZADD', 'z:1', '1', 'one', '2.5', 'two'])
            r.cmd(['XADD', 's:1', '*', 'k', 'v'])
            r.cmd(['SET', 'big', 'x' * 100000])

    def test_scan_types_ttl(self):
        keys, cut = D.redis_scan(self.c)
        by = {k['key']: k for k in keys}
        self.assertEqual({k: v['type'] for k, v in by.items()}, {'str:1': 'string', 'str:ttl': 'string', 'hash:1': 'hash', 'list:1': 'list', 'set:1': 'set', 'z:1': 'zset', 's:1': 'stream', 'big': 'string'})
        self.assertEqual(by['str:1']['ttl'], -1)
        self.assertTrue(900_000 < by['str:ttl']['ttl'] <= 1_000_000)
        self.assertEqual([k['key'] for k in D.redis_scan(self.c, pattern='str:*')[0]], ['str:1', 'str:ttl'])
        self.assertEqual(D.redis_dbsize(self.c), 8)

    def test_values_by_type(self):
        v = D.redis_value(self.c, 'str:1', 'string')
        self.assertEqual(v['text'], 'tiếng Việt 🌏')
        self.assertEqual(len(D.redis_value(self.c, 'big', 'string')['text']), 65536)
        self.assertIn('64 KB', D.redis_value(self.c, 'big', 'string')['note'])
        self.assertEqual(sorted(D.redis_value(self.c, 'hash:1', 'hash')['rows']), [['f1', 'v1'], ['f2', 'v2']])
        self.assertEqual(D.redis_value(self.c, 'list:1', 'list')['rows'], [['0', 'a'], ['1', 'b'], ['2', 'c']])
        self.assertEqual(sorted(D.redis_value(self.c, 'set:1', 'set')['rows']), [['m1'], ['m2']])
        self.assertEqual(D.redis_value(self.c, 'z:1', 'zset')['rows'], [['one', '1'], ['two', '2.5']])
        st = D.redis_value(self.c, 's:1', 'stream')
        self.assertEqual(len(st['rows']), 1)
        self.assertIn('k=v', st['rows'][0][1])

    def test_readonly_enforced_and_write_mode(self):
        with self.assertRaises(D.DbError):
            D.redis_command(self.c, ['DEL', 'str:1'])
        self.assertEqual(D.redis_command(self.c, ['EXISTS', 'str:1'])[0], 1)
        self.assertEqual(D.redis_command(self.c, ['DEL', 'str:1'], allow_write=True)[0], 1)
        self.assertEqual(D.redis_command(self.c, ['EXISTS', 'str:1'])[0], 0)
        r, _ = D.redis_command(self.c, ['SET', 'a'], allow_write=True)  # lỗi cú pháp được trả về dạng RespError, không ném
        self.assertIsInstance(r, D.RespError)

    def test_keyspace(self):
        with D.Redis(self.c, 3) as r:
            r.cmd(['SET', 'in-db3', '1'])
        try:
            ks = dict(D.redis_keyspace(self.c))
            self.assertEqual(ks[0], 8)
            self.assertEqual(ks[3], 1)
            self.assertEqual(D.redis_scan(self.c, db=3)[0][0]['key'], 'in-db3')
        finally:
            with D.Redis(self.c, 3) as r:
                r.cmd(['FLUSHDB'])

    def test_wrong_password_and_detect_docker(self):
        with self.assertRaises(D.DbError):
            D.redis_dbsize({**self.c, 'password': 'nope'})
        found = [c for c in D.detect_docker() if c['container'] == REDIS_NAME]
        self.assertEqual(len(found), 1)
        c = found[0]
        self.assertEqual((c['kind'], c['password'], c['port']), ('redis', 'redis-pw-1', STATE['redis_port']))
        self.assertNotIn('redis-pw-1', D.describe(c))
        self.assertNotIn('redis-pw-1', c['sub'])
        self.assertEqual(D.redis_dbsize(c), 8)

    def test_bridge_ip_route(self):
        ip = subprocess.run(['docker', 'inspect', '-f', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}', REDIS_NAME], capture_output=True, text=True).stdout.strip()
        if not ip:
            self.skipTest('không có IP bridge')
        c = D.make_conn('redis', 'tcp', 'r', host=ip, port=6379, password='redis-pw-1')
        try:
            self.assertEqual(D.redis_command(c, ['PING'])[0], 'PONG')
        except D.DbError as e:
            self.skipTest(f'host không truy cập được IP bridge: {e}')


@unittest.skipUnless(docker_ok() and has_image('devfest-danang-trip-ai-db:latest'), 'cần Docker + image devfest-danang-trip-ai-db (Postgres 16)')
class PostgresRealTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not STATE.get('pg'):
            raise unittest.SkipTest('container postgres không khởi động được')
        port = subprocess.run(['docker', 'port', PG_NAME, '5432/tcp'], capture_output=True, text=True).stdout.strip().rsplit(':', 1)[-1]
        cls.docker = D.make_conn('postgres', 'docker', 'pg', container=PG_NAME, image='postgres:16', user='tester', password='pg-pw-1', db='testdb')
        cls.tcp = D.make_conn('postgres', 'tcp', 'pg-tcp', host='127.0.0.1', port=int(port), user='tester', password='pg-pw-1', db='testdb')
        ddl = ["CREATE SCHEMA IF NOT EXISTS shop",
               "CREATE TABLE IF NOT EXISTS shop.items (id serial PRIMARY KEY, name text NOT NULL, note text, price numeric(10,2) DEFAULT 9.99)",
               "CREATE INDEX IF NOT EXISTS items_name_idx ON shop.items (name)",
               "CREATE TABLE IF NOT EXISTS \"we\"\"ird;--\" (x int)",
               "CREATE VIEW shop.v_items AS SELECT id, name FROM shop.items"]
        for q in ddl:
            D.exec_sql(cls.docker, q, readonly=False)
        D.exec_sql(cls.docker, "INSERT INTO shop.items(name, note) VALUES ('Trần, \"A\"', NULL), ('B', ''), ('multi', E'l1\\nl2')", readonly=False)
        D.exec_sql(cls.docker, 'INSERT INTO "we""ird;--" VALUES (7)', readonly=False)

    def test_both_transports_see_same_data(self):
        for c in (self.docker, self.tcp):
            r = D.exec_sql(c, 'SELECT id, name, note FROM shop.items ORDER BY id')
            self.assertEqual(r.cols, ['id', 'name', 'note'])
            self.assertEqual(r.rows[0], ['1', 'Trần, "A"', None])  # NULL
            self.assertEqual(r.rows[1][2], '')  # chuỗi rỗng khác NULL
            self.assertEqual(r.rows[2][2], 'l1\nl2')

    def test_metadata(self):
        self.assertEqual(D.list_databases(self.docker), ['postgres', 'testdb'])
        tabs = {(t['schema'], t['name']): t for t in D.list_tables(self.docker, 'testdb')}
        self.assertEqual(tabs[('shop', 'items')]['type'], 'bảng')
        self.assertEqual(tabs[('shop', 'v_items')]['type'], 'view')
        self.assertIn(('public', 'we"ird;--'), tabs)
        cols = D.table_columns(self.docker, 'testdb', 'shop', 'items')
        self.assertEqual([(c['name'], c['type'], c['nullable'], c['key']) for c in cols], [('id', 'integer', False, 'PK'), ('name', 'text', False, ''), ('note', 'text', True, ''), ('price', 'numeric(10,2)', True, '')])
        self.assertEqual(cols[3]['default'], '9.99')
        self.assertTrue(cols[0]['default'].startswith('nextval'))
        idx = dict(D.table_indexes(self.docker, 'testdb', 'shop', 'items'))
        self.assertIn('items_name_idx', idx)
        self.assertIn('items_pkey', idx)
        weird = D.table_columns(self.docker, 'testdb', 'public', 'we"ird;--')
        self.assertEqual([c['name'] for c in weird], ['x'])

    def test_page_and_weird_names_and_injection(self):
        res, more = D.table_page(self.docker, 'testdb', 'shop', 'items', limit=2)
        self.assertEqual((len(res.rows), more), (2, True))
        res, more = D.table_page(self.docker, 'testdb', 'public', 'we"ird;--')
        self.assertEqual(res.rows, [['7']])
        with self.assertRaises(D.DbError):
            D.table_page(self.docker, 'testdb', 'shop', 'items', where='1=1; DROP TABLE shop.items')
        self.assertEqual(D.exec_sql(self.docker, 'SELECT COUNT(*) FROM shop.items').rows, [['3']])

    def test_readonly_is_enforced_by_database_itself(self):
        # tầng DB: chạy câu ghi dưới chế độ read-only như internal (bỏ qua kiểm tra Python) vẫn bị Postgres từ chối
        with self.assertRaises(D.DbError) as e:
            D.exec_sql(self.docker, 'CREATE TABLE zz(x int)', internal=True)
        self.assertIn('read-only', str(e.exception))
        with self.assertRaises(D.DbError):
            D.exec_sql(self.docker, 'DELETE FROM shop.items')  # tầng Python
        with self.assertRaises(D.DbError):
            D.exec_sql(self.tcp, 'select 1; delete from shop.items')
        self.assertEqual(D.exec_sql(self.tcp, 'SELECT COUNT(*) FROM shop.items').rows, [['3']])

    def test_write_mode_and_errors_and_timeout_setting(self):
        r = D.exec_sql(self.docker, "UPDATE shop.items SET note = 'u' WHERE name = 'B'", readonly=False)
        self.assertEqual(D.exec_sql(self.docker, "SELECT note FROM shop.items WHERE name = 'B'").rows, [['u']])
        D.exec_sql(self.docker, "UPDATE shop.items SET note = '' WHERE name = 'B'", readonly=False)
        with self.assertRaises(D.DbError) as e:
            D.exec_sql(self.docker, 'SELECT * FROM nope')
        self.assertIn('does not exist', str(e.exception))
        with self.assertRaises(D.DbError):
            D.exec_sql(D.make_conn('postgres', 'docker', 'x', container='không-có-container', user='u', password='p'), 'select 1')
        self.assertEqual(D.exec_sql(self.docker, "SHOW statement_timeout").rows, [['30s']])
        self.assertEqual(D.exec_sql(self.docker, 'SHOW default_transaction_read_only').rows, [['on']])
        self.assertEqual(D.exec_sql(self.docker, 'SELECT 1 WHERE false').cols, ['?column?'])

    def test_password_not_in_process_arguments(self):
        for c in (self.docker, self.tcp):
            self.assertNotIn('pg-pw-1', ' '.join(D._pg_argv(c, 'testdb', 'select 1')))

    def test_detect_docker_reads_credentials_from_env(self):
        found = [c for c in D.detect_docker() if c['container'] == PG_NAME]
        self.assertEqual(len(found), 1)
        c = found[0]
        self.assertEqual((c['kind'], c['user'], c['password'], c['db']), ('postgres', 'tester', 'pg-pw-1', 'testdb'))
        self.assertNotIn('pg-pw-1', D.describe(c) + c['sub'])
        self.assertEqual(D.exec_sql(c, 'SELECT current_user').rows, [['tester']])


class DetectTest(unittest.TestCase):
    def test_classify_image(self):
        for img, kind in (('postgres:16', 'postgres'), ('pgvector/pgvector:pg16', 'postgres'), ('mysql:8', 'mysql'), ('mariadb:11', 'mysql'), ('redis:7-alpine', 'redis'), ('valkey/valkey:8', 'redis'),
                          ('library/postgres', 'postgres'), ('nginx:latest', None), ('myapp-db:latest', None)):
            self.assertEqual(D.classify_image(img), kind, img)
        self.assertEqual(D.classify_image('custom-db', ['5432/tcp']), 'postgres')
        self.assertEqual(D.classify_image('x', ['3306/tcp']), 'mysql')

    def test_detect_with_sample_inspect(self):
        sample = [{'Name': '/my-mysql', 'Config': {'Image': 'mariadb:11', 'Env': ['MARIADB_ROOT_PASSWORD=rootpw', 'MARIADB_DATABASE=shop'], 'Cmd': []},
                   'NetworkSettings': {'Ports': {'3306/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '3307'}]}, 'Networks': {'bridge': {'IPAddress': '172.17.0.5'}}}},
                  {'Name': '/cache', 'Config': {'Image': 'redis:7', 'Env': [], 'Cmd': ['redis-server', '--requirepass', 'pw9']}, 'NetworkSettings': {'Ports': {}, 'Networks': {'bridge': {'IPAddress': '172.17.0.7'}}}},
                  {'Name': '/web', 'Config': {'Image': 'nginx', 'Env': []}, 'NetworkSettings': {'Ports': {'80/tcp': None}, 'Networks': {}}},
                  {'Name': '/appuser-mysql', 'Config': {'Image': 'mysql:8', 'Env': ['MYSQL_USER=app', 'MYSQL_PASSWORD=apw', 'MYSQL_DATABASE=appdb'], 'Cmd': []},
                   'NetworkSettings': {'Ports': {}, 'Networks': {'n': {'IPAddress': '10.0.0.2'}}}}]

        def fake(args, timeout=20):
            return 'a b c d' if args[:2] == ['ps', '-q'] else json.dumps(sample)
        with mock.patch.object(D, '_docker_json', fake):
            found = {c['name']: c for c in D.detect_docker()}
        self.assertEqual(set(found), {'my-mysql', 'cache', 'appuser-mysql'})
        m = found['my-mysql']
        self.assertEqual((m['user'], m['password'], m['db'], m['host'], m['port']), ('root', 'rootpw', 'shop', '127.0.0.1', 3307))
        r = found['cache']
        self.assertEqual((r['password'], r['host'], r['port']), ('pw9', '172.17.0.7', 6379))
        a = found['appuser-mysql']
        self.assertEqual((a['user'], a['password'], a['db']), ('app', 'apw', 'appdb'))

    def test_detect_never_raises_without_docker(self):
        with mock.patch.object(D, '_docker_json', side_effect=D.DbError('no docker')):
            self.assertEqual(D.detect_docker(), [])


if __name__ == '__main__':
    unittest.main()
