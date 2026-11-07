import gzip
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import httpclient as H  # noqa: E402
from devdock.lib import openapi as OA  # noqa: E402


class Echo(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _reply(self, code, body, ctype='application/json', extra=()):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def handle_any(self):
        n = int(self.headers.get('content-length') or 0)
        body = self.rfile.read(n) if n else b''
        p = self.path
        if p.startswith('/redirect-to-'):
            return self._reply(302, b'', extra=[('Location', p[len('/redirect-to-'):].replace('~', '/'))])
        if p.startswith('/redir'):
            k = int(p.split('/')[2]) if p.count('/') >= 2 and p.split('/')[2].isdigit() else 0
            return self._reply(302, b'', extra=[('Location', f'/redir/{k + 1}')]) if k < 20 else self._reply(200, b'{}')
        if p.startswith('/post-redirect'):
            return self._reply(307 if 'keep' in p else 303, b'', extra=[('Location', '/echo')])
        if p == '/slow':
            time.sleep(5)
            return self._reply(200, b'{}')
        if p == '/big':
            return self._reply(200, b'{"x":"' + b'a' * (3 * 1024 * 1024) + b'"}')
        if p == '/gz':
            return self._reply(200, gzip.compress(b'{"gz":true}'), extra=[('Content-Encoding', 'gzip')])
        if p.startswith('/status/'):
            return self._reply(int(p.split('/')[2]), b'{"err":"x"}')
        out = {'method': self.command, 'path': p, 'headers': {k.lower(): v for k, v in self.headers.items()}, 'body': body.decode('utf-8', 'replace')}
        self._reply(200, json.dumps(out).encode())

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = handle_any


def serve():
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Echo)
    srv.handle_error = lambda *a: None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_address[1]}'


class ValidationTest(unittest.TestCase):
    def test_url_rules(self):
        self.assertEqual(H.check_url(' http://localhost:8000/a b '), 'http://localhost:8000/a%20b')
        for bad in ('', 'ftp://x/y', 'file:///etc/passwd', 'javascript:alert(1)', 'localhost:8000', 'http://', 'http://x/\r\nHost: evil', 'http://x/\x00', 'http://x:99999999/'):
            with self.assertRaises(H.HttpError, msg=repr(bad)):
                H.check_url(bad)

    def test_header_rules(self):
        H.check_header('X-Token', 'abc def')
        H.check_header('X-T', 'tab\tok')
        for n, v in (('Bad Name', 'x'), ('X', 'a\r\nInjected: 1'), ('X', 'a\nb'), ('', 'x'), ('X:Y', 'v'), ('X', 'nul\x00')):
            with self.assertRaises(H.HttpError, msg=repr((n, v))):
                H.check_header(n, v)

    def test_variables(self):
        self.assertEqual(H.substitute('{{base}}/u/{{ id }}', {'base': 'http://x', 'id': 5}), 'http://x/u/5')
        self.assertEqual(H.unresolved('{{a}} {{b}} {{a}}', {'a': 1}), ['b'])
        r = H.new_request(url='{{base}}/x')
        with self.assertRaises(H.HttpError) as e:
            H.build(r, {})
        self.assertIn('{{base}}', str(e.exception))
        self.assertEqual(H.build(r, {'base': 'http://h'})['url'], 'http://h/x')

    def test_build_params_auth_body(self):
        r = H.new_request(method='POST', url='http://h/p?a=1')
        r['params'] = [{'on': True, 'k': 'q', 'v': 'x y&z'}, {'on': False, 'k': 'off', 'v': '1'}]
        r['headers'] = [{'on': True, 'k': 'X-A', 'v': 'v'}]
        r['auth'].update(type='bearer', token=' tok ')
        r['body_type'], r['body'] = 'json', '{"a":1}'
        b = H.build(r)
        self.assertEqual(b['url'], 'http://h/p?a=1&q=x+y%26z')
        h = dict(b['headers'])
        self.assertEqual((h['Authorization'], h['Content-Type'], b['body']), ('Bearer tok', 'application/json', b'{"a":1}'))
        r['auth'].update(type='basic', user='u', password='p:w')
        self.assertEqual(dict(H.build(r)['headers'])['Authorization'], 'Basic dTpwOnc=')
        r['auth'].update(type='apikey', key_name='X-Key', key_value='k')
        self.assertEqual(dict(H.build(r)['headers'])['X-Key'], 'k')
        r['body_type'], r['form'] = 'form', [{'on': True, 'k': 'a', 'v': 'é b'}]
        self.assertEqual(H.build(r)['body'], b'a=%C3%A9+b')
        r['headers'].append({'on': True, 'k': 'Bad', 'v': 'x\r\ny: z'})
        with self.assertRaises(H.HttpError):
            H.build(r)

    def test_curl_quotes_special_chars(self):
        r = H.new_request(method='POST', url="http://h/p")
        r['headers'] = [{'on': True, 'k': 'X-Q', 'v': "it's \"quoted\" $HOME `x`"}]
        r['body_type'], r['body'] = 'json', '{"a":"it\'s; rm -rf /"}'
        c = H.to_curl(H.build(r), follow=False, verify=False)
        import shlex
        parts = shlex.split(c)
        self.assertEqual(parts[:3], ['curl', '-X', 'POST'])
        self.assertIn("X-Q: it's \"quoted\" $HOME `x`", parts)
        self.assertIn('{"a":"it\'s; rm -rf /"}', parts)
        self.assertIn('-k', parts)
        self.assertNotIn('-L', parts)


class SendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.base = serve()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def go(self, method='GET', path='/echo', **kw):
        r = H.new_request(method=method, url=self.base + path)
        for k in ('headers', 'params'):
            if k in kw:
                r[k] = kw.pop(k)
        for k in ('body_type', 'body'):
            if k in kw:
                r[k] = kw.pop(k)
        return H.send(H.build(r), **kw)

    def test_echo_methods_headers_body(self):
        for m in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'):
            res = self.go(m, body_type='raw', body='hello') if m in ('POST', 'PUT', 'PATCH') else self.go(m)
            echo = json.loads(res['body'])
            self.assertEqual((res['status'], echo['method']), (200, m))
        res = self.go('POST', body_type='json', body='{"k":"v"}', headers=[{'on': True, 'k': 'X-Test', 'v': 'abc'}])
        echo = json.loads(res['body'])
        self.assertEqual((echo['body'], echo['headers']['x-test'], echo['headers']['content-type']), ('{"k":"v"}', 'abc', 'application/json'))
        self.assertEqual(self.go('HEAD')['body'], b'')
        self.assertGreater(res['elapsed'], 0)

    def test_error_statuses_are_results_not_exceptions(self):
        for code in (404, 500, 418):
            self.assertEqual(self.go(path=f'/status/{code}')['status'], code)

    def test_redirects(self):
        res = self.go(path='/redir/0', max_redirects=3)
        self.assertEqual(res['status'], 302)
        self.assertIn('Quá 3 lần', res['blocked'])
        self.assertEqual(len(res['redirects']), 3)
        self.assertEqual(self.go(path='/redir/0', follow=False)['status'], 302)
        post = self.go('POST', '/post-redirect', body_type='raw', body='x')       # 303 → GET, bỏ body
        self.assertEqual(json.loads(post['body'])['method'], 'GET')
        keep = self.go('POST', '/post-redirect-keep', body_type='raw', body='x')    # 307 giữ method+body
        e = json.loads(keep['body'])
        self.assertEqual((e['method'], e['body']), ('POST', 'x'))

    def test_redirect_to_other_scheme_blocked(self):
        res = self.go(path='/redirect-to-https:~~127.0.0.1:9~x')
        self.assertEqual(res['status'], 302)
        self.assertIn('scheme khác', res['blocked'])
        res = self.go(path='/redirect-to-file:~~~etc~passwd')
        self.assertIn('Redirect bị chặn', res['blocked'])

    def test_auth_header_dropped_on_cross_host_redirect(self):
        srv2, base2 = serve()
        try:
            target = (base2 + '/echo').replace('/', '~')
            r = H.new_request(url=self.base + '/redirect-to-' + target)
            r['auth'].update(type='bearer', token='SECRET')
            res = H.send(H.build(r))
            self.assertNotIn('authorization', json.loads(res['body'])['headers'])  # 127.0.0.1:khác cổng = host khác
        finally:
            srv2.shutdown()

    def test_large_body_truncated_at_2mb(self):
        res = self.go(path='/big')
        self.assertTrue(res['truncated'])
        self.assertEqual(len(res['body']), H.MAX_BODY)

    def test_gzip_decoded(self):
        r = H.new_request(url=self.base + '/gz')
        r['headers'] = [{'on': True, 'k': 'Accept-Encoding', 'v': 'gzip'}]
        self.assertEqual(H.send(H.build(r))['body'], b'{"gz":true}')

    def test_timeout_and_connection_errors(self):
        t0 = time.time()
        with self.assertRaises(H.HttpError) as e:
            self.go(path='/slow', timeout=1)
        self.assertIn('Hết thời gian', str(e.exception))
        self.assertLess(time.time() - t0, 4)
        with self.assertRaises(H.HttpError) as e:
            H.send(H.build(H.new_request(url='http://127.0.0.1:9/')))
        self.assertIn('từ chối kết nối', str(e.exception))
        with self.assertRaises(H.HttpError) as e:
            H.send(H.build(H.new_request(url='http://không-có-host.invalid/')))

    def test_cancel_midflight(self):
        cancel, out = H.Cancel(), {}

        def work():
            try:
                self.go(path='/slow', cancel=cancel, timeout=20)
            except H.HttpError as e:
                out['err'] = str(e)
        t = threading.Thread(target=work)
        t.start()
        time.sleep(0.5)
        t0 = time.time()
        cancel.cancel()
        t.join(4)
        self.assertFalse(t.is_alive())
        self.assertEqual(out['err'], 'Đã huỷ')
        self.assertLess(time.time() - t0, 2)

    def test_pretty(self):
        self.assertEqual(H.pretty(b'{"a":1}', 'application/json'), '{\n  "a": 1\n}')
        self.assertEqual(H.pretty('Tiếng Việt'.encode(), 'text/plain'), 'Tiếng Việt')
        self.assertEqual(H.pretty(b'{broken', 'application/json'), '{broken')


class TlsTest(unittest.TestCase):
    def test_self_signed_verify_toggle(self):
        import shutil
        import subprocess
        if not shutil.which('openssl'):
            self.skipTest('cần openssl để tạo chứng chỉ thử')
        d = tempfile.mkdtemp()
        cert, key = os.path.join(d, 'c.pem'), os.path.join(d, 'k.pem')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', cert, '-days', '1', '-subj', '/CN=localhost'], capture_output=True, check=True)
        srv = ThreadingHTTPServer(('127.0.0.1', 0), Echo)
        srv.handle_error = lambda *a: None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            prep = H.build(H.new_request(url=f'https://127.0.0.1:{srv.server_address[1]}/echo'))
            with self.assertRaises(H.HttpError) as e:
                H.send(prep, verify=True)
            self.assertIn('TLS', str(e.exception))
            self.assertEqual(H.send(prep, verify=False)['status'], 200)
        finally:
            srv.shutdown()


SPEC = {
    'openapi': '3.0.3', 'info': {'title': 't', 'version': '1'}, 'servers': [{'url': 'http://api.local:8000'}],
    'paths': {
        '/users/{user_id}': {'parameters': [{'name': 'user_id', 'in': 'path', 'required': True, 'schema': {'type': 'integer', 'example': 7}}],
                             'get': {'tags': ['users'], 'summary': 'Get user', 'parameters': [{'name': 'verbose', 'in': 'query', 'schema': {'type': 'boolean'}},
                                                                                              {'name': 'fields', 'in': 'query', 'required': True, 'schema': {'type': 'string', 'enum': ['a', 'b']}}]}},
        '/users': {'post': {'tags': ['users'], 'summary': 'Create', 'requestBody': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/UserCreate'}}}}},
                   'get': {'tags': ['users'], 'parameters': [{'$ref': '#/components/parameters/Limit'}]}},
        '/login': {'post': {'tags': ['auth'], 'requestBody': {'content': {'application/x-www-form-urlencoded': {'schema': {'type': 'object', 'properties': {'username': {'type': 'string'}, 'password': {'type': 'string', 'format': 'password'}}}}}}}},
        '/tree': {'put': {'requestBody': {'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Node'}}}}}},
        '/old': {'get': {'deprecated': True, 'tags': ['users']}},
    },
    'components': {
        'parameters': {'Limit': {'name': 'limit', 'in': 'query', 'schema': {'type': 'integer', 'default': 10}}},
        'schemas': {
            'Base': {'type': 'object', 'required': ['email'], 'properties': {'email': {'type': 'string', 'format': 'email'}, 'age': {'type': 'integer', 'minimum': 18}}},
            'UserCreate': {'allOf': [{'$ref': '#/components/schemas/Base'}, {'type': 'object', 'properties': {'role': {'type': 'string', 'enum': ['admin', 'user']}, 'tags': {'type': 'array', 'items': {'type': 'string'}},
                                                                                                        'addr': {'$ref': '#/components/schemas/Addr'}, 'nick': {'type': ['string', 'null']}}}]},
            'Addr': {'type': 'object', 'properties': {'city': {'type': 'string', 'example': 'Hà Nội'}, 'zip': {'oneOf': [{'type': 'integer'}, {'type': 'string'}]}}},
            'Node': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'child': {'$ref': '#/components/schemas/Node'}}},
        }}}


class OpenApiTest(unittest.TestCase):
    def test_load_json_yaml_and_reject(self):
        self.assertIn('paths', OA.load_spec(json.dumps(SPEC)))
        import yaml
        self.assertIn('paths', OA.load_spec(yaml.safe_dump(SPEC)))
        for bad in ('{"a":1}', 'not a spec', '[]', '{"openapi":"3","paths":[]}'):
            with self.assertRaises(OA.SpecError):
                OA.load_spec(bad)

    def test_endpoints_grouping_and_params(self):
        eps = OA.endpoints(SPEC)
        self.assertEqual([(e['tag'], e['method'], e['path']) for e in eps][:3], [('(không có tag)', 'PUT', '/tree'), ('auth', 'POST', '/login'), ('users', 'GET', '/old')])
        get = next(e for e in eps if e['path'] == '/users/{user_id}')
        self.assertEqual(sorted(p['name'] for p in get['params']), ['fields', 'user_id', 'verbose'])  # path-level + operation-level
        lst = next(e for e in eps if e['path'] == '/users' and e['method'] == 'GET')
        self.assertEqual([p['name'] for p in lst['params']], ['limit'])                                   # $ref parameter
        self.assertTrue(next(e for e in eps if e['path'] == '/old')['deprecated'])
        self.assertEqual(OA.servers(SPEC), ['http://api.local:8000'])

    def test_request_from_get_fills_path_and_required_query(self):
        ep = next(e for e in OA.endpoints(SPEC) if e['path'] == '/users/{user_id}')
        r = OA.request_from(SPEC, ep)
        self.assertEqual((r['method'], r['url']), ('GET', '{{base_url}}/users/7'))
        q = {p['k']: p for p in r['params']}
        self.assertTrue(q['fields']['on'] and q['fields']['v'] == 'a')
        self.assertFalse(q['verbose']['on'])
        self.assertEqual(H.build(r, {'base_url': 'http://h'})['url'], 'http://h/users/7?fields=a')

    def test_body_sample_refs_allof_enum_array(self):
        ep = next(e for e in OA.endpoints(SPEC) if e['path'] == '/users' and e['method'] == 'POST')
        r = OA.request_from(SPEC, ep)
        body = json.loads(r['body'])
        self.assertEqual(r['body_type'], 'json')
        self.assertEqual(list(body)[0], 'email')                       # required trước
        self.assertEqual((body['email'], body['age'], body['role'], body['tags'], body['nick']), ('user@example.com', 18, 'admin', ['string'], 'string'))
        self.assertEqual(body['addr'], {'city': 'Hà Nội', 'zip': 0})   # $ref + example + oneOf[0]

    def test_recursive_schema_terminates(self):
        ep = next(e for e in OA.endpoints(SPEC) if e['path'] == '/tree')
        body = json.loads(OA.request_from(SPEC, ep)['body'])
        depth, cur = 0, body
        while isinstance(cur, dict) and 'child' in cur and cur['child'] is not None:
            cur, depth = cur['child'], depth + 1
        self.assertLessEqual(depth, OA.MAX_DEPTH)

    def test_form_body(self):
        ep = next(e for e in OA.endpoints(SPEC) if e['path'] == '/login')
        r = OA.request_from(SPEC, ep)
        self.assertEqual(r['body_type'], 'form')
        self.assertEqual([(x['k'], x['v']) for x in r['form']], [('username', 'string'), ('password', 'string')])

    def test_swagger2(self):
        sw = {'swagger': '2.0', 'host': 'h:1', 'basePath': '/v1', 'schemes': ['https'], 'paths': {'/p/{id}': {'post': {'consumes': ['application/json'], 'parameters': [
            {'name': 'id', 'in': 'path', 'required': True, 'type': 'string'}, {'name': 'b', 'in': 'body', 'schema': {'$ref': '#/definitions/B'}}]}}},
            'definitions': {'B': {'type': 'object', 'properties': {'x': {'type': 'integer'}}}}}
        r = OA.request_from(sw, OA.endpoints(sw)[0])
        self.assertEqual((r['url'], json.loads(r['body'])), ('{{base_url}}/p/string', {'x': 0}))
        self.assertEqual(OA.servers(sw), ['https://h:1/v1'])

    def test_external_refs_ignored_safely(self):
        spec = {'openapi': '3.0.0', 'paths': {'/x': {'post': {'requestBody': {'content': {'application/json': {'schema': {'$ref': 'http://evil/x.json'}}}}}}}}
        r = OA.request_from(spec, OA.endpoints(spec)[0])
        self.assertEqual(r['url'], '{{base_url}}/x')


class StoreTest(unittest.TestCase):
    def test_roundtrip_perms_history_redaction(self):
        path = os.path.join(tempfile.mkdtemp(), 'sub', 'api.json')
        s = H.ApiStore(path)
        self.assertEqual(len(s.collections), 1)
        c = s.add_collection('Project A')
        r = H.new_request(name='Login', method='POST', url='{{base_url}}/login')
        r['auth'].update(type='bearer', token='TOPSECRET')
        s.save_request(c['id'], r)
        s.add_history(r, 200, 0.12)
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), '0o600')
        s2 = H.ApiStore(path)
        self.assertEqual(s2.coll(c['id'])['requests'][0]['auth']['token'], 'TOPSECRET')      # request đã lưu giữ nguyên
        self.assertEqual(s2.data['history'][0]['req']['auth']['token'], '')                  # lịch sử thì không giữ bí mật
        d = s2.duplicate_request(c['id'], r['id'])
        self.assertEqual((len(s2.coll(c['id'])['requests']), d['name']), (2, 'Login (bản sao)'))
        s2.delete_request(c['id'], r['id'])
        self.assertEqual(len(s2.coll(c['id'])['requests']), 1)
        s2.delete_collection(c['id'])
        self.assertEqual(len(s2.collections), 1)
        for _ in range(H.HISTORY_MAX + 5):
            s2.add_history(r, 200, 0.1)
        self.assertEqual(len(s2.data['history']), H.HISTORY_MAX)
        self.assertEqual([f for f in os.listdir(os.path.dirname(path)) if f.startswith('.api-')], [])

    def test_corrupt_file_ignored(self):
        path = os.path.join(tempfile.mkdtemp(), 'api.json')
        with open(path, 'w') as f:
            f.write('{oops')
        self.assertEqual(len(H.ApiStore(path).collections), 1)


if __name__ == '__main__':
    unittest.main()
