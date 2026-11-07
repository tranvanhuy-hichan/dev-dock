"""Test giao diện API Tester: dùng máy chủ HTTP giả (localhost), cấu hình/dữ liệu tạm (do ui_smoke đặt XDG_*)."""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from uihelp import check, pump, wait_for  # noqa: E402

from test_apitester import SPEC, Echo, serve  # noqa: E402


def spec_server():
    """Máy chủ giả phục vụ /openapi.json (SPEC) + echo."""
    from http.server import ThreadingHTTPServer

    class H(Echo):
        def handle_any(self):
            if self.path == '/openapi.json':
                body = json.dumps(SPEC).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return Echo.handle_any(self)
        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = handle_any
    srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
    srv.handle_error = lambda *a: None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_address[1]}'


def get_text(buf):
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


def api_tests(win):
    from devdock.lib import httpclient as H
    srv, base = spec_server()
    try:
        win.select_module('api')
        pg = win.pages['api']
        pump(300)
        check('api page opens with default collection', len(pg.store.collections) == 1 and pg.resp_stack.get_visible_child_name() == 'empty')
        check('nothing sent automatically', pg.store.data['history'] == [] and pg.last is None)
        check('header controls registered', len(pg.hdr_start) == 1 and len(pg.hdr_end) == 1)

        # ---- biến + gửi POST JSON có header/auth
        pg.coll()['vars']['base_url'] = base
        pg.new_request()
        pg.method_dd.set_selected(H.METHODS.index('POST'))
        pg.url.set_text('{{base_url}}/echo?x=1')
        pg.kv_params.add_row(True, 'q', 'a b')
        pg.kv_headers.add_row(True, 'X-Test', 'abc')
        pg.body_dd.set_selected(1)
        pg.body_buf.set_text('{"hello":"world"}', -1)
        pg.auth_dd.set_selected(1)
        pg.a_token.set_text('SECRET-TOKEN')
        pg.send()
        check('send button shows Huỷ while sending', pg.sending and pg.send_btn.get_label() == 'Huỷ')
        wait_for(lambda: not pg.sending and pg.last, 10)
        check('response status shown', pg.last and pg.last['status'] == 200 and pg.status.get_text().startswith('200') and pg.status.has_css_class('ok'), pg.status.get_text())
        echo = json.loads(pg.last['body'])
        check('request reached server correctly', echo['method'] == 'POST' and echo['path'] == '/echo?x=1&q=a+b' and echo['body'] == '{"hello":"world"}'
              and echo['headers']['x-test'] == 'abc' and echo['headers']['authorization'] == 'Bearer SECRET-TOKEN', str(echo)[:200])
        check('pretty JSON body rendered (multi-line)', '"method": "POST"' in get_text(pg.resp_buf) and get_text(pg.resp_buf).count('\n') > 3)
        pg.pretty_sw.set_active(False)
        check('raw body when pretty off (single line)', get_text(pg.resp_buf).count('\n') == 0 and '"method": "POST"' in get_text(pg.resp_buf))
        pg.pretty_sw.set_active(True)
        check('headers table filled', pg.hdr_table.store.get_n_items() >= 2)
        check('meta shows time/size/url', 'ms' in pg.meta.get_text() and base in pg.meta.get_text(), pg.meta.get_text())
        check('history recorded without secrets', len(pg.store.data['history']) == 1 and pg.store.data['history'][0]['req']['auth']['token'] == ''
              and 'SECRET-TOKEN' not in open(pg.store.path).read().split('"history"')[1])

        # ---- lỗi: biến thiếu, scheme lạ, CRLF → không gửi
        pg.kv_params.set([])
        pg.url.set_text('{{nope}}/x')
        pg.send()
        check('missing variable blocks send', pg.err.get_visible() and 'nope' in pg.err.text.get_text() and not pg.sending)
        for bad in ('ftp://h/x', 'file:///etc/passwd'):
            pg.url.set_text(bad)
            pg.send()
            check(f'scheme rejected: {bad}', pg.err.get_visible() and not pg.sending, pg.err.text.get_text())
        pg.url.set_text(base + '/echo')
        pg.kv_headers.add_row(True, 'X-Bad', 'a\r\nInjected: 1')
        pg.send()
        check('CRLF header rejected', pg.err.get_visible() and 'CR/LF' in pg.err.text.get_text() and not pg.sending, pg.err.text.get_text())
        pg.kv_headers.set([])

        # ---- lỗi mạng hiển thị thân thiện
        pg.url.set_text('http://127.0.0.1:9/')
        pg.send()
        wait_for(lambda: not pg.sending, 10)
        check('connection refused shown friendly', pg.status.get_text() == 'Lỗi' and 'từ chối kết nối' in pg.meta.get_text(), pg.meta.get_text())

        # ---- huỷ giữa chừng
        pg.url.set_text(base + '/slow')
        pg.send()
        pump(500)
        t0 = time.time()
        pg.send_or_cancel()
        wait_for(lambda: not pg.sending, 5)
        check('cancel works fast', not pg.sending and pg.status.get_text() == 'Đã huỷ' and time.time() - t0 < 3, pg.status.get_text())

        # ---- phản hồi lớn bị cắt + 4xx
        pg.url.set_text(base + '/big')
        pg.send()
        wait_for(lambda: not pg.sending and pg.last, 20)
        check('large response truncated notice', pg.last['truncated'] and pg.resp_strip.get_visible() and '2 MB' in pg.resp_strip.text.get_text() and 'KB đầu' in pg.resp_strip.text.get_text(), pg.resp_strip.text.get_text())
        shown = get_text(pg.resp_buf)
        check('huge body display capped and line-wrapped (UI stays responsive)', len(shown) < 600_000 and max(map(len, shown.split('\n'))) <= 2000, str(len(shown)))
        pg.url.set_text(base + '/status/404')
        pg.send()
        wait_for(lambda: not pg.sending and pg.last and pg.last['status'] == 404, 10)
        check('4xx colored warn', pg.status.has_css_class('warn') and not pg.resp_strip.get_visible())
        pg.url.set_text(base + '/status/500')
        pg.send()
        wait_for(lambda: not pg.sending and pg.last and pg.last['status'] == 500, 10)
        check('5xx colored bad', pg.status.has_css_class('bad'))

        # ---- lưu / nạp lại / nhân bản
        pg.new_request()
        pg.name.set_text('Health check')
        pg.url.set_text('{{base_url}}/status/200')
        pg.auth_dd.set_selected(1)
        pg.a_token.set_text('KEEP-ME')
        pg.save_request()
        check('request saved to collection', [r['name'] for r in pg.coll()['requests']] == ['Health check'])
        check('saved list shows it selected', pg.saved_list.get_selected_row() is not None and pg.saved_list.get_selected_row().rid == pg.req['id'])
        check('store file perms 0600', oct(os.stat(pg.store.path).st_mode & 0o777) == '0o600')
        pg.new_request()
        pg.open_saved(pg.coll()['requests'][0]['id'])
        check('saved request loads back with auth token', pg.url.get_text() == '{{base_url}}/status/200' and pg.a_token.get_text() == 'KEEP-ME' and pg.auth_dd.get_selected() == 1)
        pg.dup_request()
        check('duplicate adds copy and selects it', len(pg.coll()['requests']) == 2 and pg.name.get_text() == 'Health check (bản sao)')
        # nạp lại từ đĩa
        st2 = H.ApiStore(pg.store.path)
        check('persisted on disk', len(st2.coll(pg.coll_id)['requests']) == 2 and st2.coll(pg.coll_id)['vars']['base_url'] == base)

        # ---- copy curl
        pg.url.set_text("{{base_url}}/x?a=it's")
        pg.copy_curl()
        wait_for(lambda: True, 0.2)
        clip = pg.get_clipboard()
        got = {}
        clip.read_text_async(None, lambda c, res: got.update(t=c.read_text_finish(res)))
        wait_for(lambda: 't' in got, 3)
        import shlex
        parts = shlex.split(got.get('t') or 'curl')
        check('curl copied and shell-safe', parts[0] == 'curl' and "a=it's" in parts[3] and any(p == 'Authorization: Bearer KEEP-ME' for p in parts), str(parts)[:160])

        # ---- OpenAPI: tải từ URL, liệt kê theo tag, tạo request
        pg.ltabs.set_active_name('openapi')
        pg.coll()['vars'].pop('base_url', None)
        pg.spec_url.set_text(base + '/openapi.json')
        pg.load_spec_url()
        wait_for(lambda: pg.spec is not None, 10)
        check('openapi loaded', pg.spec is not None and len(pg.eps) == 6, str(len(pg.eps)))
        check('base_url auto-set from servers', pg.coll()['vars'].get('base_url') == 'http://api.local:8000', str(pg.coll()['vars']))
        rows = []
        r = pg.ep_list.get_first_child()
        while r:
            if hasattr(r, 'ep'):
                rows.append(r)
            r = r.get_next_sibling()
        check('endpoint rows listed', len(rows) == 6)
        pg.ep_search.set_text('login')
        pump(300)
        check('endpoint filter', [r.ep['path'] for r in rows if r.get_child_visible() and pg.ep_filter(r)] == ['/login'])
        pg.ep_search.set_text('')
        pg.use_endpoint(next(e for e in pg.eps if e['path'] == '/users' and e['method'] == 'POST'))
        body = json.loads(get_text(pg.body_buf))
        check('endpoint → request with sample body', pg.url.get_text() == '{{base_url}}/users' and pg.method_dd.get_selected() == H.METHODS.index('POST')
              and body['email'] == 'user@example.com' and body['addr']['city'] == 'Hà Nội' and pg.body_dd.get_selected() == 1, str(body)[:100])
        pg.use_endpoint(next(e for e in pg.eps if e['path'] == '/users/{user_id}'))
        check('path/query params prefilled', pg.url.get_text() == '{{base_url}}/users/7' and any(r['k'] == 'fields' and r['on'] for r in pg.kv_params.get()))
        # file YAML
        import yaml
        pg.set_spec(yaml.safe_dump(SPEC), None)
        check('yaml spec accepted', len(pg.eps) == 6)
        pg.set_spec('{"not":"a spec"}', None)
        check('invalid spec rejected with message', 'không phải' in pg.spec_info.get_text().lower() or 'không phải' in pg.spec_info.get_text() and len(pg.eps) == 6, pg.spec_info.get_text())

        # ---- lịch sử → nạp lại
        pg.ltabs.set_active_name('history')
        n = 0
        r = pg.hist_list.get_first_child()
        while r:
            n += 1
            r = r.get_next_sibling()
        check('history list populated', n >= 5, str(n))
        first = pg.hist_list.get_first_child()
        pg.use_history(first.h)
        check('history entry loads request', pg.url.get_text() != '' and pg.a_token.get_text() == '')

        # ---- tuỳ chọn lưu lại
        pg.opt_follow.set_active(False)
        pg.opt_timeout.set_value(7)
        check('options persisted', H.ApiStore(pg.store.path).data['options'] == {'follow': False, 'verify': True, 'timeout': 7})
        pg.opt_follow.set_active(True)
        pg.opt_timeout.set_value(30)

        # ---- xoá bộ sưu tập
        pg.store.add_collection('Khác')
        pg.refresh_collections()
        check('two collections listed', pg.coll_dd.get_model().get_n_items() == 2)
    finally:
        srv.shutdown()


TESTS = [api_tests]
