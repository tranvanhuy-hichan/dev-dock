"""Test giao diện Database Explorer: SQLite tạm, Redis và Postgres thật trong container tạm (tự dọn)."""
import os
import sqlite3
import subprocess
import tempfile
import time

from uihelp import check, pump, wait_for

PID = os.getpid()
RD, PG = f'devdock-test-redis-ui-{PID}', f'devdock-test-pg-ui-{PID}'


def sh(*a):
    return subprocess.run(list(a), capture_output=True, text=True)


def conn_row(pg, pred):
    return next((r for r in pg._conn_rows() if pred(r.conn)), None)


def table_row(pg, name):
    r = pg.tlist.get_first_child()
    while r:
        if hasattr(r, 'item') and r.item['name'] == name:
            return r
        r = r.get_next_sibling()
    return None


def grid_of(holder):
    return holder.get_first_child()


def db_sqlite_tests(win):
    from devdock.lib import dbclient as D
    win.select_module('database')
    pg = win.pages['database']
    check('db page opens read-only by default', pg.readonly and not pg.strip_ro.get_visible())
    d = tempfile.mkdtemp(prefix='ddui-db-')
    path = os.path.join(d, 'shop.db')
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, city TEXT); CREATE INDEX idx_city ON customers(city);")
    con.executemany('INSERT INTO customers(name,email,city) VALUES (?,?,?)', [(f'Khách {i}', None if i % 3 == 0 else f'k{i}@x.vn', ['Hà Nội', 'Đà Nẵng'][i % 2]) for i in range(237)])
    con.commit()
    con.close()
    pg.add_conn(D.make_conn('sqlite', 'file', 'shop.db', path=path, sub=path))
    pump(200)
    saved = open(D.config_path()).read()
    check('saved connection has no password field', 'password' not in saved and 'shop.db' in saved)
    check('connection selected after add', pg.cur and pg.cur['kind'] == 'sqlite')
    check('sqlite tables listed', wait_for(lambda: table_row(pg, 'customers') is not None, 8), str([t['name'] for t in pg.tables]))
    check('table row count shown', next(t for t in pg.tables if t['name'] == 'customers')['rows'] == 237)
    pg.tlist.select_row(table_row(pg, 'customers'))
    check('data page loaded (100 of 237)', wait_for(lambda: pg.data_holder.get_first_child() is not None and 'dòng 1–100' in pg.page_info.get_text(), 8), pg.page_info.get_text())
    check('next enabled, prev disabled on first page', pg.next_btn.get_sensitive() and not pg.prev_btn.get_sensitive())
    pg.goto_page(1)
    check('page 2', wait_for(lambda: 'dòng 101–200' in pg.page_info.get_text(), 8), pg.page_info.get_text())
    pg.goto_page(1)
    check('last page has 37 rows, next disabled', wait_for(lambda: 'dòng 201–237' in pg.page_info.get_text(), 8) and not pg.next_btn.get_sensitive(), pg.page_info.get_text())
    pg.page_no = 0
    pg.filter_entry.set_text("city = 'Hà Nội' AND email IS NULL")
    pg.load_data()
    check('where filter applied', wait_for(lambda: pg.data_holder.get_first_child() is not None and pg.last[1] and all(r[3] == 'Hà Nội' and r[2] is None for r in pg.last[1]), 8) and len(pg.last[1]) > 0, str(pg.last[1][:2]))
    pg.filter_entry.set_text('1=1; DROP TABLE customers')
    pg.load_data()
    check('injected second statement is rejected', wait_for(lambda: pg.strip_err.get_visible(), 8))
    check('table still exists after injection attempt', D.exec_sql(pg.cur, 'SELECT COUNT(*) FROM customers').rows == [['237']])
    pg.filter_entry.set_text('')
    pg.load_data()
    wait_for(lambda: not pg.strip_err.get_visible() and pg.last[1] and len(pg.last[1]) == 100, 8)
    # --- chọn dòng + xem dòng
    t = grid_of(pg.data_holder)
    t.sel.set_selected(0)
    pump(200)
    check('row selection reveals action bar', pg.rbar.get_revealed() and pg.sel_row is not None)
    from devdock.pages.database import RowDialog
    RowDialog(*pg.sel_row)
    check('row dialog builds', True)
    # --- cấu trúc
    pg.tabs.set_active_name('struct')
    check('structure shows columns + index', wait_for(lambda: pg.struct_cols.get_first_child() is not None and pg.struct_idx.get_first_child() is not None, 8))
    # --- SQL
    pg.tabs.set_active_name('sql')
    pg.editor.get_buffer().set_text("SELECT city, COUNT(*) AS n FROM customers GROUP BY city ORDER BY city", -1)
    pg.run_sql()
    check('sql result grid + status', wait_for(lambda: pg.sql_holder.get_first_child() is not None and 'dòng' in pg.sql_status.get_text() and pg.last[0] == ['city', 'n'], 8), pg.sql_status.get_text())
    check('history recorded', len(pg.history) == 1)
    pg.editor.get_buffer().set_text("DELETE FROM customers", -1)
    pg.run_sql()
    check('write blocked in read-only mode', pg.strip_err.get_visible() and 'CHỈ ĐỌC' in pg.strip_err.text.get_tooltip_text())
    pg.editor.get_buffer().set_text("SELECT 1; SELECT 2", -1)
    pg.run_sql()
    check('multi-statement rejected', 'một câu lệnh' in pg.strip_err.text.get_tooltip_text())
    check('data intact after blocked attempts', D.exec_sql(pg.cur, 'SELECT COUNT(*) FROM customers').rows == [['237']])
    # --- chế độ ghi
    pg.write_btn.set_active(True)
    check('write mode shows warning strip', not pg.readonly and pg.strip_ro.get_visible())
    stmt, cls = D.single_statement("DELETE FROM customers WHERE city = 'Đà Nẵng'", 'sqlite', allow_write=True)
    pg._exec(stmt, cls)
    check('write executes in write mode', wait_for(lambda: D.exec_sql(pg.cur, 'SELECT COUNT(*) FROM customers').rows != [['237']], 8))
    pg.write_btn.set_active(False)
    check('back to read-only', pg.readonly and not pg.strip_ro.get_visible())
    # --- tên bảng lạ & bỏ kết nối
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE "we""ird; DROP TABLE customers;--" (x)')
    con.execute('INSERT INTO "we""ird; DROP TABLE customers;--" VALUES (5)')
    con.commit()
    con.close()
    pg.load_tables()
    check('weird table name listed and browsable', wait_for(lambda: table_row(pg, 'we"ird; DROP TABLE customers;--') is not None, 8))
    pg.tlist.select_row(table_row(pg, 'we"ird; DROP TABLE customers;--'))
    check('weird table data', wait_for(lambda: pg.last[1] == [['5']], 8), str(pg.last))
    pg.remove_conn(pg.cur)
    check('connection removed from list and saved file', conn_row(pg, lambda c: c['kind'] == 'sqlite') is None and 'shop.db' not in open(D.config_path()).read())
    # --- kết nối TCP cần mật khẩu: không tự kết nối khi chưa có mật khẩu
    tcp = D.make_conn('postgres', 'tcp', 'remote-pg', host='10.255.255.1', port=5432, user='u', sub='10.255.255.1:5432')
    pg.add_conn(tcp)
    pump(300)
    check('tcp conn without password asks instead of connecting', pg.cur is None or pg.cur['id'] != tcp['id'])
    pg.remove_conn(tcp)


def db_redis_tests(win):
    from devdock.lib import dbclient as D
    pg = win.pages['database']
    win.select_module('database')
    sh('docker', 'rm', '-f', RD)
    r = sh('docker', 'run', '-d', '--rm', '--name', RD, '-p', '127.0.0.1::6379', 'redis:7-alpine', '--requirepass', 'ui-redis-pw')
    if r.returncode:
        check('redis container (skipped)', True, r.stderr)
        return
    try:
        check('redis ready', wait_for(lambda: 'PONG' in sh('docker', 'exec', RD, 'redis-cli', '-a', 'ui-redis-pw', 'ping').stdout, 30, 500))
        port = int(sh('docker', 'port', RD, '6379/tcp').stdout.strip().rsplit(':', 1)[-1])
        c = D.make_conn('redis', 'tcp', 'x', host='127.0.0.1', port=port, password='ui-redis-pw')
        with D.Redis(c) as rc:
            for i in range(5):
                rc.cmd(['SET', f'ui:str:{i}', f'v{i}'])
            rc.cmd(['HSET', 'ui:hash', 'a', '1', 'b', '2'])
            rc.cmd(['SET', 'ui:ttl', 'x', 'EX', '500'])
        pg.refresh_conns(True)
        check('redis container auto-detected with password from Cmd', wait_for(lambda: conn_row(pg, lambda c: c['container'] == RD) is not None, 15))
        row = conn_row(pg, lambda c: c['container'] == RD)
        det = row.conn
        check('detected password not in sub/describe text', 'ui-redis-pw' not in det['sub'] + D.describe(det) and det['password'] == 'ui-redis-pw')
        pg.clist.select_row(row)
        check('redis view shown with db0 entry', wait_for(lambda: pg.rstack.get_visible_child_name() == 'redis' and table_row(pg, 'db0') is not None, 10))
        pg.tlist.select_row(table_row(pg, 'db0'))
        check('keys scanned', wait_for(lambda: pg.keys_table.store.get_n_items() == 7, 10), str(pg.keys_table.store.get_n_items()))
        idx = next(i for i in range(pg.keys_table.sorted.get_n_items()) if pg.keys_table.sorted.get_item(i).d['key'] == 'ui:hash')
        pg.keys_table.sel.set_selected(idx)
        check('hash value displayed as table', wait_for(lambda: pg.v_holder.get_first_child() is not None and 'hash' in pg.v_note.get_text() and 'field' in pg.v_note.get_text(), 8), pg.v_note.get_text())
        pg.pattern.set_text('ui:str:*')
        pg.scan_keys()
        check('pattern filters keys', wait_for(lambda: pg.keys_table.store.get_n_items() == 5, 8))
        # --- lệnh
        pg.rtabs.set_active_name('cmd')
        pg.cmd_entry.set_text('GET ui:str:1')
        pg.run_cmd()
        buf = pg.cmd_out.get_buffer()
        check('read command works', wait_for(lambda: '"v1"' in buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False), 8))
        pg.cmd_entry.set_text('SET ui:new 1')
        pg.run_cmd()
        check('write command blocked in read-only', pg.strip_err.get_visible() and 'CHỈ ĐỌC' in pg.strip_err.text.get_tooltip_text())
        check('and key was not created', D.redis_command(c, ['EXISTS', 'ui:new'])[0] == 0)
        pg.cmd_entry.set_text('KEYS *')
        pg.run_cmd()
        check('KEYS blocked', 'bị chặn' in pg.strip_err.text.get_tooltip_text())
        pg.write_btn.set_active(True)
        pg._cmd(['SET', 'ui:new', '1'], 'SET ui:new 1')
        check('write command works in write mode', wait_for(lambda: D.redis_command(c, ['EXISTS', 'ui:new'])[0] == 1, 8))
        check('FLUSHALL needs confirmation class', D.redis_class(['FLUSHALL']) == 'write')
        pg.write_btn.set_active(False)
    finally:
        sh('docker', 'rm', '-f', '-v', RD)


def db_postgres_tests(win):
    from devdock.lib import dbclient as D
    pg = win.pages['database']
    win.select_module('database')
    if sh('docker', 'image', 'inspect', 'devfest-danang-trip-ai-db:latest').returncode:
        check('postgres image missing (skipped)', True)
        return
    sh('docker', 'rm', '-f', PG)
    r = sh('docker', 'run', '-d', '--rm', '--name', PG, '--tmpfs', '/var/lib/postgresql/data', '-e', 'POSTGRES_PASSWORD=ui-pg-pw', '-e', 'POSTGRES_USER=uiuser', '-e', 'POSTGRES_DB=uidb',
           '-p', '127.0.0.1::5432', 'devfest-danang-trip-ai-db:latest')
    if r.returncode:
        check('postgres container (skipped)', True, r.stderr)
        return
    try:
        check('postgres ready', wait_for(lambda: sh('docker', 'exec', PG, 'pg_isready', '-U', 'uiuser', '-d', 'uidb').returncode == 0, 40, 500))
        c = D.make_conn('postgres', 'docker', 'x', container=PG, user='uiuser', password='ui-pg-pw', db='uidb')
        for q in ["CREATE SCHEMA shop", "CREATE TABLE shop.items(id serial PRIMARY KEY, name text NOT NULL, note text)", "CREATE INDEX items_name ON shop.items(name)",
                  "INSERT INTO shop.items(name, note) SELECT 'Mặt hàng ' || g, CASE WHEN g % 2 = 0 THEN NULL ELSE E'dòng 1\\ndòng 2, \"x\"' END FROM generate_series(1, 250) g"]:
            D.exec_sql(c, q, readonly=False)
        pg.refresh_conns(True)
        check('postgres container auto-detected with credentials from env', wait_for(lambda: conn_row(pg, lambda c: c['container'] == PG) is not None, 15))
        row = conn_row(pg, lambda c: c['container'] == PG)
        check('password hidden in row text', 'ui-pg-pw' not in row.conn['sub'] + D.describe(row.conn))
        pg.clist.select_row(row)
        check('databases dropdown populated, default db selected', wait_for(lambda: pg.db == 'uidb' and table_row(pg, 'items') is not None, 15), f'{pg.db} {[t["name"] for t in pg.tables]}')
        check('postgres tables grouped by schema', next(t for t in pg.tables if t['name'] == 'items')['schema'] == 'shop')
        pg.tlist.select_row(table_row(pg, 'items'))
        check('postgres data page', wait_for(lambda: 'dòng 1–100' in pg.page_info.get_text(), 12), pg.page_info.get_text())
        check('NULL vs multi-line values preserved', pg.last[1][0][2] == 'dòng 1\ndòng 2, "x"' and pg.last[1][1][2] is None, str(pg.last[1][:2]))
        pg.tabs.set_active_name('struct')
        check('postgres structure', wait_for(lambda: pg.struct_idx.get_first_child() is not None and pg.struct_cols.get_first_child() is not None, 10))
        pg.tabs.set_active_name('sql')
        pg.editor.get_buffer().set_text("SELECT count(*) AS total, count(note) AS with_note FROM shop.items", -1)
        pg.run_sql()
        check('postgres sql result', wait_for(lambda: pg.last[1] == [['250', '125']], 10), str(pg.last))
        pg.editor.get_buffer().set_text("DROP TABLE shop.items", -1)
        pg.run_sql()
        check('drop blocked (python layer)', pg.strip_err.get_visible())
        check('table intact', D.exec_sql(c, 'SELECT count(*) FROM shop.items').rows == [['250']])
        # tầng DB: ngay cả khi lọt qua Python, session vẫn read-only
        try:
            D.exec_sql(c, 'DROP TABLE shop.items', internal=True)
            ok = False
        except D.DbError as e:
            ok = 'read-only' in str(e)
        check('drop blocked by postgres read-only session too', ok)
    finally:
        sh('docker', 'rm', '-f', '-v', PG)


TESTS = [db_sqlite_tests, db_redis_tests, db_postgres_tests]
