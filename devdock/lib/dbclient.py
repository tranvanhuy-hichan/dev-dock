"""Database Explorer: PostgreSQL, MySQL/MariaDB, SQLite, Redis — không cần thư viện ngoài.

- Postgres/MySQL: dùng client CLI (`psql`/`mysql`) trong container (docker exec) hoặc trên host (TCP).
- SQLite: module sqlite3 của Python. Redis: giao thức RESP tự cài (socket).
- Mặc định CHỈ ĐỌC, ép ở hai tầng: (1) phía DB (read-only transaction/session, sqlite mode=ro, whitelist lệnh Redis),
  (2) phía Python: chỉ cho chạy MỘT câu lệnh mỗi lần và phân loại đọc/ghi (chặn `SET ... read_only=off`, `INTO OUTFILE`...).
- Mật khẩu chỉ nằm trong bộ nhớ và truyền qua biến môi trường của tiến trình con (không xuất hiện trong dòng lệnh, không log).
"""
import csv
import io
import json
import os
import re
import socket
import sqlite3
import subprocess
import time

KINDS = ('postgres', 'mysql', 'redis', 'sqlite')
MAX_ROWS = 5000
TIMEOUT = 30
NULL = '<<NULL>>'  # dấu NULL của psql (phân biệt với chuỗi rỗng); trong kết quả được đổi thành None


class DbError(RuntimeError):
    pass


# ======================================================================================
# Quote / escape
# ======================================================================================
def qident(kind, name):
    """Quote định danh (tên bảng/schema/cột) đúng theo từng loại DB."""
    if not isinstance(name, str) or not name or '\x00' in name:
        raise DbError('Tên không hợp lệ')
    if kind == 'mysql':
        return '`' + name.replace('`', '``') + '`'
    return '"' + name.replace('"', '""') + '"'


def qlit(kind, s):
    """Chuỗi literal an toàn để đưa vào SQL (dùng cho câu truy vấn metadata)."""
    if '\x00' in s:
        raise DbError('Chuỗi chứa ký tự NUL')
    if kind == 'mysql':
        return "'" + s.replace('\\', '\\\\').replace("'", "''") + "'"
    return "'" + s.replace("'", "''") + "'"


def qualified(kind, schema, table):
    if kind == 'sqlite' or not schema:
        return qident(kind, table)
    return qident(kind, schema) + '.' + qident(kind, table)


# ======================================================================================
# Tách và phân loại câu lệnh
# ======================================================================================
def split_statements(sql, kind='postgres'):
    """Tách theo ';' ngoài chuỗi/comment/dollar-quote. Bỏ câu rỗng và câu chỉ có comment."""
    out, cur, i, n = [], [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'" or c == '"' or (c == '`' and kind == 'mysql'):
            j = i + 1
            while j < n:
                if kind == 'mysql' and sql[j] == '\\' and c != '`':
                    j += 2
                    continue
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:  # '' hoặc "" là ký tự thoát
                        j += 2
                        continue
                    break
                j += 1
            cur.append(sql[i:j + 1])
            i = j + 1
        elif c == '-' and sql[i:i + 2] == '--' or (c == '#' and kind == 'mysql'):
            j = sql.find('\n', i)
            j = n if j < 0 else j
            cur.append(sql[i:j])
            i = j
        elif sql[i:i + 2] == '/*':
            j = sql.find('*/', i + 2)
            j = n if j < 0 else j + 2
            cur.append(sql[i:j])
            i = j
        elif c == '$' and kind == 'postgres' and (m := re.match(r'\$([A-Za-z_]\w*)?\$', sql[i:])):
            tag = m.group(0)
            j = sql.find(tag, i + len(tag))
            j = n if j < 0 else j + len(tag)
            cur.append(sql[i:j])
            i = j
        elif c == ';':
            out.append(''.join(cur))
            cur = []
            i += 1
        else:
            cur.append(c)
            i += 1
    out.append(''.join(cur))
    return [s.strip() for s in out if strip_comments(s, kind).strip()]


def strip_comments(sql, kind='postgres'):
    """Bỏ comment và NỘI DUNG chuỗi (thay bằng '') để phân tích từ khoá an toàn."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c in ('"', "'", '`'):
            j = i + 1
            while j < n:
                if kind == 'mysql' and sql[j] == '\\' and c != '`':
                    j += 2
                    continue
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            out.append(c + c)
            i = j + 1
        elif sql[i:i + 2] == '--' or (c == '#' and kind == 'mysql'):
            j = sql.find('\n', i)
            i = n if j < 0 else j
        elif sql[i:i + 2] == '/*':
            j = sql.find('*/', i + 2)
            i = n if j < 0 else j + 2
            out.append(' ')
        elif c == '$' and kind == 'postgres' and (m := re.match(r'\$([A-Za-z_]\w*)?\$', sql[i:])):
            tag = m.group(0)
            j = sql.find(tag, i + len(tag))
            i = n if j < 0 else j + len(tag)
            out.append("''")
        else:
            out.append(c)
            i += 1
    return ''.join(out)


_READ_LEAD = {'select', 'show', 'values', 'table', 'describe', 'desc', 'explain', 'with', 'pragma'}
# Trong thân SELECT/WITH chỉ cần bắt các câu thay đổi dữ liệu lồng bên trong (CTE ghi, INTO OUTFILE, khoá ghi);
# phần còn lại đã bị chặn ở tầng DB (read-only) nên không quét rộng để khỏi chặn nhầm cột tên `comment`, `load`...
_WRITE_WORDS = re.compile(r'\b(insert|update|delete|merge|into\s+(outfile|dumpfile)|for\s+(update|share))\b', re.I)


def classify(stmt, kind='postgres'):
    """'read' hoặc 'write' cho MỘT câu lệnh. Bảo thủ: không chắc chắn là đọc thì coi là ghi."""
    s = strip_comments(stmt, kind).strip().rstrip(';').strip()
    m = re.match(r'\(*\s*([A-Za-z]+)', s)
    if not m:
        return 'write'
    lead = m.group(1).lower()
    if lead not in _READ_LEAD:
        return 'write'
    if lead == 'pragma':  # PRAGMA x = v là ghi; PRAGMA x / PRAGMA f(x) là đọc
        return 'write' if '=' in s else 'read'
    body = s[m.end():]
    if lead == 'explain':
        body = re.sub(r'^\s*\(?\s*(analyze|verbose|costs|format\s+\w+|buffers|\s|,)*\)?\s*', '', body, flags=re.I)
        return classify(body, kind) if body.strip() else 'read'
    # SELECT/WITH/SHOW...: nếu có từ khoá thay đổi dữ liệu/trạng thái thì coi là ghi
    if _WRITE_WORDS.search(body):
        return 'write'
    return 'read'


def single_statement(sql, kind, allow_write):
    """Kiểm tra trước khi chạy: đúng MỘT câu lệnh và (nếu chỉ đọc) phải là câu đọc. Trả về (câu_lệnh, loại)."""
    stmts = split_statements(sql, kind)
    if not stmts:
        raise DbError('Chưa có câu lệnh nào')
    if len(stmts) > 1:
        raise DbError('Mỗi lần chỉ chạy được một câu lệnh — hãy bỏ các câu còn lại (dấu ; ở giữa)')
    cls = classify(stmts[0], kind)
    if cls == 'write' and not allow_write:
        raise DbError('Đang ở chế độ CHỈ ĐỌC: chỉ chạy được SELECT/SHOW/EXPLAIN. Bật "Cho phép ghi" nếu thật sự muốn thay đổi dữ liệu.')
    return stmts[0], cls


# ======================================================================================
# Phân tích đầu ra
# ======================================================================================
def parse_csv(text, null=NULL):
    """CSV của psql → (cột, dòng). Giá trị `null` thành None. Hỗ trợ dấu phẩy/xuống dòng/dấu nháy trong ô."""
    if not text.strip():
        return [], []
    rd = csv.reader(io.StringIO(text, newline=''))
    rows = list(rd)
    cols = rows[0]
    return cols, [[None if v == null else v for v in r] for r in rows[1:]]


_TSV_ESC = {'n': '\n', 't': '\t', '\\': '\\', '0': '\x00'}


def _tsv_unescape(s):
    if '\\' not in s:
        return s
    return re.sub(r'\\(.)', lambda m: _TSV_ESC.get(m.group(1), m.group(0)), s)


def parse_tsv(text):
    """Đầu ra `mysql --batch` → (cột, dòng). Chữ `NULL` nguyên văn là NULL (giới hạn: không phân biệt với chuỗi 'NULL')."""
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    if not lines:
        return [], []
    cols = [_tsv_unescape(c) for c in lines[0].split('\t')]
    rows = []
    for ln in lines[1:]:
        cells = ln.split('\t')
        rows.append([None if c == 'NULL' else _tsv_unescape(c) for c in cells])
    return cols, rows


def to_csv(cols, rows):
    """Xuất CSV (UTF-8 có BOM để Excel đọc đúng). Ô bắt đầu bằng = + @ hoặc '-' theo sau chữ bị Excel coi là công thức nên thêm dấu nháy."""
    def cell(v):
        if v is None:
            return ''
        t = str(v)
        if t[:1] in ('=', '+', '@', '\t', '\r') or re.match(r'^-[A-Za-z(]', t):
            return "'" + t
        return t
    buf = io.StringIO(newline='')
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator='\r\n')
    w.writerow([cell(c) for c in cols])
    for r in rows:
        w.writerow([cell(v) for v in r])
    return '﻿' + buf.getvalue()


# ======================================================================================
# Kết nối
# ======================================================================================
def make_conn(kind, mode, name, **kw):
    """Mô tả kết nối. mode: docker | tcp | file. Mật khẩu (password) chỉ giữ trong bộ nhớ."""
    if kind not in KINDS:
        raise DbError(f'Loại DB không hỗ trợ: {kind}')
    c = {'kind': kind, 'mode': mode, 'name': name, 'container': '', 'image': '', 'host': '', 'port': 0, 'user': '', 'password': '', 'db': '', 'path': '', 'sub': ''}
    c.update(kw)
    c['id'] = f"{mode}:{kind}:{c['container'] or c['path'] or c['host']}:{c['port']}:{c['user']}"
    return c


def describe(c):
    """Chuỗi mô tả KHÔNG chứa mật khẩu."""
    if c['kind'] == 'sqlite':
        return c['path']
    where = f"container {c['container']}" if c['mode'] == 'docker' else f"{c['host']}:{c['port']}"
    return f"{c['kind']} · {where}" + (f" · user {c['user']}" if c['user'] else '')


def _run(argv, env_extra=None, timeout=TIMEOUT):
    env = {**os.environ, **(env_extra or {})}
    try:
        return subprocess.run(argv, capture_output=True, text=True, errors='replace', timeout=timeout, stdin=subprocess.DEVNULL, env=env)
    except FileNotFoundError:
        raise DbError(f'Không tìm thấy lệnh `{argv[0]}`') from None
    except subprocess.TimeoutExpired:
        raise DbError(f'Quá thời gian chờ ({timeout}s) — câu lệnh bị huỷ phía client') from None


def _clean_err(text):
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return '\n'.join(lines[:6]) or 'Lỗi không rõ'


class Result:
    def __init__(self, cols=None, rows=None, truncated=False, ms=0, notice=''):
        self.cols, self.rows, self.truncated, self.ms, self.notice = cols or [], rows or [], truncated, ms, notice


# ---------------- Postgres ----------------
def _pg_argv(c, db, sql):
    base = ['psql', '-X', '-q', '-w', '--csv', '-P', f'null={NULL}', '-v', 'ON_ERROR_STOP=1']
    if c['mode'] == 'docker':
        argv = ['docker', 'exec', '-i', '-e', 'PGPASSWORD', '-e', 'PGOPTIONS', c['container'], *base]
    else:
        argv = [*base, '-h', c['host'], '-p', str(c['port'])]
    argv += ['-U', c['user'] or 'postgres', '-d', db or c['db'] or c['user'] or 'postgres', '-c', sql]
    return argv


def _pg_exec(c, sql, db, readonly):
    opts = f'-c statement_timeout={TIMEOUT * 1000}' + (' -c default_transaction_read_only=on' if readonly else '')
    r = _run(_pg_argv(c, db, sql), {'PGPASSWORD': c['password'], 'PGOPTIONS': opts}, TIMEOUT + 5)
    if r.returncode:
        raise DbError(_clean_err(r.stderr or r.stdout))
    return r.stdout


# ---------------- MySQL ----------------
def _mysql_argv(c, db, sql, client='mysql'):
    base = [client, '--batch', '--default-character-set=utf8mb4']
    if c['mode'] == 'docker':
        argv = ['docker', 'exec', '-i', '-e', 'MYSQL_PWD', c['container'], *base]
    else:
        argv = [*base, '-h', c['host'], '-P', str(c['port']), '--protocol=tcp']
    argv += ['-u', c['user'] or 'root']
    d = db or c['db']
    if d:
        argv += ['-D', d]
    return argv + ['-e', sql]


def _mysql_exec(c, sql, db, readonly):
    full = ('SET SESSION TRANSACTION READ ONLY; ' if readonly else '') + sql
    clients = ['mariadb', 'mysql'] if 'maria' in (c.get('image') or '').lower() else ['mysql', 'mariadb']
    last = None
    for cl in clients:
        r = _run(_mysql_argv(c, db, full, cl), {'MYSQL_PWD': c['password']}, TIMEOUT + 5)
        if r.returncode in (126, 127) or 'executable file not found' in r.stderr or 'not found' in r.stderr[:200] and r.returncode:
            last = r
            continue
        if r.returncode:
            raise DbError(_clean_err(r.stderr or r.stdout))
        return r.stdout
    raise DbError(_clean_err(last.stderr if last else 'Không có client mysql/mariadb'))


# ---------------- SQLite ----------------
def _sqlite_connect(c, readonly):
    path = c['path']
    if not os.path.isfile(path):
        raise DbError(f'Không tìm thấy file: {path}')
    uri = 'file:' + path.replace('?', '%3f').replace('#', '%23') + ('?mode=ro' if readonly else '?mode=rw')
    con = sqlite3.connect(uri, uri=True, timeout=5)
    if readonly:
        con.execute('PRAGMA query_only = ON')
    deadline = time.time() + TIMEOUT
    con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 20000)
    return con


def _sqlite_exec(c, sql, readonly, max_rows):
    con = _sqlite_connect(c, readonly)
    try:
        try:
            cur = con.execute(sql)
            if cur.description is None:
                con.commit()
                return Result(notice=f'Đã thực thi ({con.total_changes} dòng bị ảnh hưởng)')
            cols = [d[0] for d in cur.description]
            data = cur.fetchmany(max_rows + 1)
        except sqlite3.OperationalError as e:
            raise DbError('Quá thời gian chờ — câu lệnh bị huỷ' if 'interrupted' in str(e) else str(e)) from None
        except sqlite3.Error as e:
            raise DbError(str(e)) from None
        trunc = len(data) > max_rows
        rows = [[v if v is None or isinstance(v, str) else (f'<{len(v)} byte>' if isinstance(v, bytes) else str(v)) for v in r] for r in data[:max_rows]]
        return Result(cols, rows, trunc)
    finally:
        con.close()


# ---------------- điều phối ----------------
def exec_sql(c, sql, db=None, readonly=True, internal=False, max_rows=MAX_ROWS):
    """Chạy MỘT câu lệnh SQL. internal=True: truy vấn metadata do chương trình tự dựng (bỏ kiểm tra người dùng, vẫn read-only)."""
    kind = c['kind']
    if kind == 'redis':
        raise DbError('Redis dùng redis_command()')
    if not internal:
        sql, cls = single_statement(sql, kind, allow_write=not readonly)
    else:
        cls = 'read'
    t0 = time.time()
    if kind == 'sqlite':
        res = _sqlite_exec(c, sql, readonly or internal, max_rows)
    else:
        ro = readonly or internal
        out = _pg_exec(c, sql, db, ro) if kind == 'postgres' else _mysql_exec(c, sql, db, ro)
        if cls == 'write':
            res = Result(notice=out.strip() or 'Đã thực thi')
        else:
            cols, rows = parse_csv(out) if kind == 'postgres' else parse_tsv(out)
            trunc = len(rows) > max_rows
            res = Result(cols, rows[:max_rows], trunc)
    res.ms = int((time.time() - t0) * 1000)
    return res


# ======================================================================================
# Metadata
# ======================================================================================
def list_databases(c):
    k = c['kind']
    if k == 'sqlite':
        return ['main']
    if k == 'postgres':
        r = exec_sql(c, "SELECT datname FROM pg_database WHERE NOT datistemplate AND datallowconn ORDER BY 1", db=c['db'] or 'postgres', internal=True)
        return [x[0] for x in r.rows]
    r = exec_sql(c, 'SHOW DATABASES', internal=True)
    return [x[0] for x in r.rows if x[0] not in ('information_schema', 'performance_schema', 'mysql', 'sys')]


def list_tables(c, db):
    """[{'schema','name','type'(bảng|view),'rows'(ước lượng hoặc None)}]"""
    k = c['kind']
    if k == 'sqlite':
        r = exec_sql(c, "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name", internal=True)
        out = []
        for name, typ in r.rows:
            n = None
            if typ == 'table':
                try:
                    n = int(exec_sql(c, f'SELECT COUNT(*) FROM {qident(k, name)}', internal=True).rows[0][0])
                except DbError:
                    pass
            out.append({'schema': '', 'name': name, 'type': 'bảng' if typ == 'table' else 'view', 'rows': n})
        return out
    if k == 'postgres':
        r = exec_sql(c, "SELECT n.nspname, c.relname, c.relkind::text, c.reltuples::bigint FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE c.relkind IN ('r','p','v','m','f') AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' ORDER BY 1, 2", db=db, internal=True)
        kinds = {'r': 'bảng', 'p': 'bảng', 'v': 'view', 'm': 'matview', 'f': 'foreign'}
        return [{'schema': s, 'name': n, 'type': kinds.get(t, t), 'rows': (int(x) if x not in (None, '') and int(x) >= 0 else None)} for s, n, t, x in r.rows]
    r = exec_sql(c, f"SELECT table_name, table_type, table_rows FROM information_schema.tables WHERE table_schema = {qlit(k, db)} ORDER BY table_name", db=db, internal=True)
    return [{'schema': db, 'name': n, 'type': 'view' if 'VIEW' in t else 'bảng', 'rows': int(x) if x not in (None, '') else None} for n, t, x in r.rows]


def table_columns(c, db, schema, table):
    """[{'name','type','nullable','default','key'}]"""
    k = c['kind']
    if k == 'sqlite':
        r = exec_sql(c, f'PRAGMA table_info({qlit(k, table)})', internal=True)
        return [{'name': x[1], 'type': x[2], 'nullable': x[3] == '0' and x[5] == '0', 'default': x[4], 'key': 'PK' if x[5] != '0' else ''} for x in r.rows]
    if k == 'postgres':
        reg = qlit(k, qualified(k, schema, table))
        r = exec_sql(c, "SELECT a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull, pg_get_expr(d.adbin, d.adrelid), "
                        "COALESCE((SELECT 'PK' FROM pg_index i WHERE i.indrelid = a.attrelid AND i.indisprimary AND a.attnum = ANY(i.indkey) LIMIT 1), '') "
                        f"FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum WHERE a.attrelid = to_regclass({reg}) AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum",
                     db=db, internal=True)
        return [{'name': x[0], 'type': x[1], 'nullable': x[2] == 't', 'default': x[3], 'key': x[4] or ''} for x in r.rows]
    r = exec_sql(c, f"SELECT column_name, column_type, is_nullable, column_default, column_key FROM information_schema.columns WHERE table_schema = {qlit(k, db)} AND table_name = {qlit(k, table)} ORDER BY ordinal_position", db=db, internal=True)
    return [{'name': x[0], 'type': x[1], 'nullable': x[2] == 'YES', 'default': x[3], 'key': {'PRI': 'PK', 'UNI': 'UQ', 'MUL': 'IDX'}.get(x[4] or '', x[4] or '')} for x in r.rows]


def table_indexes(c, db, schema, table):
    """[(tên, định nghĩa)]"""
    k = c['kind']
    if k == 'sqlite':
        out = []
        for row in exec_sql(c, f'PRAGMA index_list({qlit(k, table)})', internal=True).rows:
            name = row[1]
            cols = [x[2] for x in exec_sql(c, f'PRAGMA index_info({qlit(k, name)})', internal=True).rows]
            out.append((name, ('UNIQUE ' if row[2] == '1' else '') + '(' + ', '.join(str(x) for x in cols) + ')'))
        return out
    if k == 'postgres':
        r = exec_sql(c, f"SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = {qlit(k, schema)} AND tablename = {qlit(k, table)} ORDER BY 1", db=db, internal=True)
        return [(x[0], x[1]) for x in r.rows]
    r = exec_sql(c, "SELECT index_name, non_unique, GROUP_CONCAT(column_name ORDER BY seq_in_index) FROM information_schema.statistics "
                    f"WHERE table_schema = {qlit(k, db)} AND table_name = {qlit(k, table)} GROUP BY index_name, non_unique ORDER BY index_name", db=db, internal=True)
    return [(x[0], ('UNIQUE ' if x[1] == '0' else '') + '(' + str(x[2]) + ')') for x in r.rows]


def table_page(c, db, schema, table, where='', limit=100, offset=0, readonly=True):
    """Một trang dữ liệu bảng. Lấy limit+1 dòng để biết còn trang sau không. Trả (Result, còn_trang_sau)."""
    k = c['kind']
    q = f"SELECT * FROM {qualified(k, schema, table)}"
    if where.strip():
        q += f" WHERE {where.strip()}"
    q += f" LIMIT {int(limit) + 1} OFFSET {int(offset)}"
    res = exec_sql(c, q, db=db, readonly=True, internal=False)  # người dùng nhập bộ lọc => đi qua kiểm tra câu lệnh
    more = len(res.rows) > limit
    res.rows = res.rows[:limit]
    return res, more


# ======================================================================================
# Redis (RESP2 tự cài)
# ======================================================================================
REDIS_READ = {
    'get', 'mget', 'strlen', 'getrange', 'substr', 'exists', 'type', 'ttl', 'pttl', 'expiretime', 'pexpiretime', 'scan', 'sscan', 'hscan', 'zscan', 'hget', 'hgetall', 'hkeys',
    'hvals', 'hlen', 'hmget', 'hexists', 'hstrlen', 'lrange', 'llen', 'lindex', 'smembers', 'scard', 'sismember', 'smismember', 'srandmember', 'zrange', 'zcard', 'zscore',
    'zrangebyscore', 'zrevrange', 'zrevrangebyscore', 'zrank', 'zrevrank', 'zcount', 'zlexcount', 'xrange', 'xrevrange', 'xlen', 'xinfo', 'dbsize', 'info', 'ping', 'echo',
    'time', 'lolwut', 'randomkey', 'bitcount', 'bitpos', 'getbit', 'pfcount', 'geopos', 'geodist', 'geohash', 'geosearch', 'object', 'memory', 'select', 'json.get', 'json.type',
}
REDIS_BLOCKED = {'shutdown', 'debug', 'module', 'replicaof', 'slaveof', 'failover', 'acl', 'migrate', 'keys', 'monitor', 'sync', 'psync', 'cluster', 'bgsave', 'bgrewriteaof', 'save'}


def redis_class(args):
    """'read' | 'write' | 'blocked' cho một lệnh Redis (danh sách đối số)."""
    if not args:
        raise DbError('Lệnh trống')
    cmd = args[0].lower()
    sub = args[1].lower() if len(args) > 1 else ''
    if cmd in REDIS_BLOCKED:
        return 'blocked'
    if cmd == 'config':
        return 'read' if sub == 'get' else 'blocked'
    if cmd == 'client':
        return 'read' if sub in ('list', 'info', 'getname') else 'blocked'
    if cmd == 'memory':
        return 'read' if sub in ('usage', 'stats', 'doctor') else 'write'
    if cmd == 'object':
        return 'read' if sub in ('encoding', 'freq', 'idletime', 'refcount', 'help') else 'write'
    if cmd == 'xinfo':
        return 'read' if sub in ('stream', 'groups', 'consumers', 'help') else 'write'
    if cmd == 'info' or cmd in REDIS_READ:
        return 'read'
    return 'write'


def parse_redis_line(line):
    """Tách dòng lệnh kiểu shell: hỗ trợ "nháy kép" (có \\n \\" \\xHH), 'nháy đơn'."""
    args, cur, i, n, started = [], [], 0, len(line), False
    while i < n:
        ch = line[i]
        if ch.isspace():
            if started:
                args.append(''.join(cur))
                cur, started = [], False
            i += 1
        elif ch == '"':
            started = True
            i += 1
            while i < n and line[i] != '"':
                if line[i] == '\\' and i + 1 < n:
                    e = line[i + 1]
                    if e == 'x' and i + 3 < n and re.match(r'[0-9a-fA-F]{2}', line[i + 2:i + 4]):
                        cur.append(chr(int(line[i + 2:i + 4], 16)))
                        i += 4
                        continue
                    cur.append({'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\'}.get(e, e))
                    i += 2
                else:
                    cur.append(line[i])
                    i += 1
            if i >= n:
                raise DbError('Thiếu dấu " đóng')
            i += 1
        elif ch == "'":
            started = True
            j = line.find("'", i + 1)
            if j < 0:
                raise DbError("Thiếu dấu ' đóng")
            cur.append(line[i + 1:j].replace("\\'", "'"))
            i = j + 1
        else:
            started = True
            cur.append(ch)
            i += 1
    if started:
        args.append(''.join(cur))
    return args


class RespError(Exception):
    pass


MAX_BULK = 8 * 1024 * 1024


def resp_encode(args):
    out = [b'*%d\r\n' % len(args)]
    for a in args:
        b = a if isinstance(a, bytes) else str(a).encode('utf-8')
        out.append(b'$%d\r\n%s\r\n' % (len(b), b))
    return b''.join(out)


def resp_read(f):
    """Đọc một giá trị RESP2 từ file-like nhị phân. Lỗi Redis → đối tượng RespError (không ném) để pipeline đọc tiếp được."""
    line = f.readline()
    if not line:
        raise DbError('Redis đóng kết nối đột ngột')
    t, rest = line[:1], line[1:].rstrip(b'\r\n')
    if t == b'+':
        return rest.decode('utf-8', 'replace')
    if t == b'-':
        return RespError(rest.decode('utf-8', 'replace'))
    if t == b':':
        return int(rest)
    if t == b'$':
        n = int(rest)
        if n < 0:
            return None
        if n > MAX_BULK:
            raise DbError(f'Giá trị quá lớn ({n // 1024} KB) — hãy dùng GETRANGE/SCAN để xem từng phần')
        data = f.read(n + 2)[:-2]
        return data.decode('utf-8', 'replace')
    if t == b'*':
        n = int(rest)
        return None if n < 0 else [resp_read(f) for _ in range(n)]
    raise DbError(f'Phản hồi Redis không hiểu được: {line[:40]!r}')


def resp_format(v, indent=0):
    """Hiển thị phản hồi Redis giống redis-cli."""
    pad = ' ' * indent
    if v is None:
        return pad + '(nil)'
    if isinstance(v, RespError):
        return pad + f'(error) {v}'
    if isinstance(v, int):
        return pad + f'(integer) {v}'
    if isinstance(v, list):
        if not v:
            return pad + '(empty array)'
        return '\n'.join(f'{pad}{i + 1}) ' + resp_format(x, 0).lstrip() if not isinstance(x, list) else f'{pad}{i + 1})\n' + resp_format(x, indent + 3) for i, x in enumerate(v))
    return pad + json.dumps(v, ensure_ascii=False)


class Redis:
    """Kết nối Redis ngắn hạn (mở → lệnh → đóng). Hỗ trợ pipeline."""

    def __init__(self, c, db=0, timeout=10):
        host, port = c['host'], int(c['port'] or 6379)
        if not host:
            raise DbError('Chưa xác định được địa chỉ Redis (container chưa publish cổng và không có IP bridge?)')
        try:
            self.sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as e:
            raise DbError(f'Không kết nối được Redis {host}:{port} ({e})') from None
        self.sock.settimeout(timeout)
        self.f = self.sock.makefile('rb')
        if c.get('password'):
            r = self.cmd(['AUTH', c['user'], c['password']] if c.get('user') else ['AUTH', c['password']])
            if isinstance(r, RespError):
                self.close()
                raise DbError(f'Xác thực Redis thất bại: {r}')
        if db:
            r = self.cmd(['SELECT', db])
            if isinstance(r, RespError):
                self.close()
                raise DbError(str(r))

    def cmd(self, args):
        self.sock.sendall(resp_encode(args))
        return resp_read(self.f)

    def pipeline(self, cmds):
        self.sock.sendall(b''.join(resp_encode(a) for a in cmds))
        return [resp_read(self.f) for _ in cmds]

    def close(self):
        try:
            self.f.close()
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def redis_command(c, args, db=0, allow_write=False):
    """Chạy một lệnh Redis theo quy tắc an toàn. Trả về (phản_hồi, loại)."""
    cls = redis_class(args)
    if cls == 'blocked':
        raise DbError(f'Lệnh {args[0].upper()} bị chặn (nguy hiểm hoặc chặn máy chủ). Dùng SCAN thay cho KEYS.')
    if cls == 'write' and not allow_write:
        raise DbError('Đang ở chế độ CHỈ ĐỌC: lệnh này có thể thay đổi dữ liệu. Bật "Cho phép ghi" nếu cần.')
    with Redis(c, db) as r:
        return r.cmd(args), cls


def redis_scan(c, db=0, pattern='*', limit=1000):
    """Quét key bằng SCAN (không dùng KEYS). Trả ([{'key','type','ttl'}], bị_cắt)."""
    keys, cursor, cut = [], '0', False
    with Redis(c, db) as r:
        while True:
            res = r.cmd(['SCAN', cursor, 'MATCH', pattern or '*', 'COUNT', 500])
            if isinstance(res, RespError):
                raise DbError(str(res))
            cursor, batch = res[0], res[1]
            keys.extend(batch)
            if len(keys) >= limit:
                keys, cut = keys[:limit], cursor != '0' or len(keys) > limit
                break
            if cursor == '0':
                break
        info = r.pipeline([cmd for k in keys for cmd in (['TYPE', k], ['PTTL', k])]) if keys else []
    out = []
    for i, k in enumerate(keys):
        typ, pttl = info[2 * i], info[2 * i + 1]
        out.append({'key': k, 'type': typ if isinstance(typ, str) else '?', 'ttl': pttl if isinstance(pttl, int) else -1})
    return sorted(out, key=lambda x: x['key']), cut


def redis_value(c, key, typ, db=0, limit=500):
    """Giá trị của một key theo kiểu: ('text', str) | ('table', cols, rows) và ghi chú."""
    with Redis(c, db) as r:
        if typ == 'string':
            n = r.cmd(['STRLEN', key])
            v = r.cmd(['GETRANGE', key, 0, 65535]) if isinstance(n, int) and n > 65536 else r.cmd(['GET', key])
            return {'kind': 'text', 'text': v if isinstance(v, str) else '(nil)', 'note': f'{n} byte' + (' — chỉ hiện 64 KB đầu' if isinstance(n, int) and n > 65536 else '')}
        if typ == 'hash':
            res = r.cmd(['HSCAN', key, '0', 'COUNT', limit])
            flat = res[1] if isinstance(res, list) else []
            return {'kind': 'table', 'cols': ['Field', 'Giá trị'], 'rows': [[flat[i], flat[i + 1]] for i in range(0, len(flat) - 1, 2)], 'note': f'{r.cmd(["HLEN", key])} field'}
        if typ == 'list':
            vals = r.cmd(['LRANGE', key, 0, limit - 1])
            return {'kind': 'table', 'cols': ['#', 'Giá trị'], 'rows': [[str(i), v] for i, v in enumerate(vals)], 'note': f'{r.cmd(["LLEN", key])} phần tử'}
        if typ == 'set':
            res = r.cmd(['SSCAN', key, '0', 'COUNT', limit])
            return {'kind': 'table', 'cols': ['Phần tử'], 'rows': [[v] for v in (res[1] if isinstance(res, list) else [])], 'note': f'{r.cmd(["SCARD", key])} phần tử'}
        if typ == 'zset':
            vals = r.cmd(['ZRANGE', key, 0, limit - 1, 'WITHSCORES'])
            return {'kind': 'table', 'cols': ['Phần tử', 'Điểm'], 'rows': [[vals[i], vals[i + 1]] for i in range(0, len(vals) - 1, 2)], 'note': f'{r.cmd(["ZCARD", key])} phần tử'}
        if typ == 'stream':
            ents = r.cmd(['XRANGE', key, '-', '+', 'COUNT', 100])
            rows = [[e[0], ' '.join(f'{e[1][i]}={e[1][i + 1]}' for i in range(0, len(e[1]) - 1, 2))] for e in ents]
            return {'kind': 'table', 'cols': ['ID', 'Trường'], 'rows': rows, 'note': f'{r.cmd(["XLEN", key])} bản ghi (hiện 100 đầu)'}
        return {'kind': 'text', 'text': resp_format(r.cmd(['TYPE', key])), 'note': f'Kiểu {typ} chưa hỗ trợ xem'}


def parse_keyspace(text):
    """Đầu ra `INFO keyspace` → [(số_db, số_key)]."""
    return [(int(m.group(1)), int(m.group(2))) for m in re.finditer(r'^db(\d+):keys=(\d+)', text or '', re.M)]


def redis_keyspace(c):
    """Các database Redis đang có key (luôn gồm db0)."""
    with Redis(c) as r:
        v = r.cmd(['INFO', 'keyspace'])
    ks = dict(parse_keyspace(v if isinstance(v, str) else ''))
    ks.setdefault(0, 0)
    return sorted(ks.items())


def redis_dbsize(c, db=0):
    with Redis(c, db) as r:
        v = r.cmd(['DBSIZE'])
        return v if isinstance(v, int) else 0


# ======================================================================================
# Phát hiện DB trong Docker
# ======================================================================================
def _docker_json(args, timeout=20):
    r = _run(['docker', *args], timeout=timeout)
    if r.returncode:
        raise DbError(_clean_err(r.stderr))
    return r.stdout


def classify_image(image, ports=()):
    img = (image or '').lower().split('/')[-1]
    for k, words in (('postgres', ('postgres', 'postgis', 'pgvector', 'timescale', 'supabase/postgres')), ('mysql', ('mysql', 'percona')), ('mysql', ('mariadb',)), ('redis', ('redis', 'valkey', 'keydb', 'dragonfly'))):
        if any(w in img for w in words):
            return k
    inner = {str(p).split('/')[0] for p in ports}
    for k, p in (('postgres', '5432'), ('mysql', '3306'), ('redis', '6379')):
        if p in inner:
            return k
    return None


def _env_map(env):
    return {k: v for k, _, v in (e.partition('=') for e in env or [])}


def detect_docker():
    """Quét container ĐANG CHẠY, nhận diện DB và lấy thông tin đăng nhập từ biến môi trường. Không start/sửa container."""
    try:
        ids = _docker_json(['ps', '-q']).split()
    except DbError:
        return []
    if not ids:
        return []
    try:
        data = json.loads(_docker_json(['inspect', *ids]))
    except (DbError, ValueError):
        return []
    out = []
    for v in data:
        cfg, net = v.get('Config', {}), v.get('NetworkSettings', {})
        ports = list((net.get('Ports') or {}).keys()) or list((cfg.get('ExposedPorts') or {}).keys())
        kind = classify_image(cfg.get('Image', ''), ports)
        if not kind:
            continue
        env = _env_map(cfg.get('Env'))
        name = (v.get('Name') or '').lstrip('/')
        image = cfg.get('Image', '')
        cport = {'postgres': 5432, 'mysql': 3306, 'redis': 6379}[kind]
        bind = ((net.get('Ports') or {}).get(f'{cport}/tcp') or [None])[0]
        host, port = ('127.0.0.1', int(bind['HostPort'])) if bind and bind.get('HostPort') else ('', cport)
        if not host:  # chưa publish cổng: dùng IP trong mạng bridge (truy cập được từ máy Linux)
            ips = [n.get('IPAddress') for n in (net.get('Networks') or {}).values() if n.get('IPAddress')]
            host = ips[0] if ips else ''
        sub = f'{image} · ' + (f'cổng {port}' if bind else (f'{host}:{port}' if host else 'không có cổng'))
        if kind == 'postgres':
            user = env.get('POSTGRES_USER', 'postgres')
            c = make_conn(kind, 'docker', name, container=name, image=image, host=host, port=port, user=user, password=env.get('POSTGRES_PASSWORD', ''), db=env.get('POSTGRES_DB', user), sub=sub)
        elif kind == 'mysql':
            if env.get('MYSQL_ROOT_PASSWORD') or env.get('MARIADB_ROOT_PASSWORD') or not (env.get('MYSQL_USER') or env.get('MARIADB_USER')):
                user, pw = 'root', env.get('MYSQL_ROOT_PASSWORD') or env.get('MARIADB_ROOT_PASSWORD', '')
            else:
                user, pw = env.get('MYSQL_USER') or env.get('MARIADB_USER'), env.get('MYSQL_PASSWORD') or env.get('MARIADB_PASSWORD', '')
            c = make_conn(kind, 'docker', name, container=name, image=image, host=host, port=port, user=user, password=pw, db=env.get('MYSQL_DATABASE') or env.get('MARIADB_DATABASE', ''), sub=sub)
        else:
            cmd = ' '.join(cfg.get('Cmd') or [])
            m = re.search(r'--requirepass\s+(\S+)', cmd)
            c = make_conn(kind, 'docker', name, container=name, image=image, host=host, port=port, password=(m.group(1) if m else env.get('REDIS_PASSWORD', '')), sub=sub)
        out.append(c)
    return sorted(out, key=lambda c: c['name'])


# ======================================================================================
# Kết nối đã lưu (KHÔNG lưu mật khẩu)
# ======================================================================================
def config_path():
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(base, 'devdock', 'db_connections.json')


SAVED_KEYS = ('kind', 'mode', 'name', 'host', 'port', 'user', 'db', 'path')


def load_saved(path=None):
    try:
        with open(path or config_path(), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for d in data if isinstance(data, list) else []:
        try:
            if d.get('kind') in KINDS and d.get('mode') in ('tcp', 'file'):
                out.append(make_conn(d['kind'], d['mode'], d.get('name') or d.get('path') or d.get('host', ''), **{k: d[k] for k in SAVED_KEYS if k in d and k not in ('kind', 'mode', 'name')}))
        except (TypeError, DbError):
            continue
    return out


def save_saved(conns, path=None):
    p = path or config_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    data = [{k: c.get(k, '') for k in SAVED_KEYS} for c in conns if c['mode'] in ('tcp', 'file')]  # tuyệt đối không có password
    tmp = p + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, p)
