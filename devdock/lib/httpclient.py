"""API Tester: dựng request (biến {{x}}, params, auth, body), gửi bằng http.client (huỷ được, theo redirect có kiểm soát), curl, lưu bộ sưu tập."""
import base64
import gzip
import http.client
import json
import os
import re
import shlex
import socket
import ssl
import tempfile
import threading
import time
import uuid
import zlib
from urllib.parse import urlencode, urljoin, urlsplit

METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']
MAX_BODY = 2 * 1024 * 1024
TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
BAD_VALUE_RE = re.compile(r'[\x00-\x08\x0a-\x1f\x7f]')          # mọi ký tự điều khiển trừ TAB (gồm CR, LF)
BAD_URL_RE = re.compile(r'[\x00-\x20\x7f]')
VAR_RE = re.compile(r'\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}')
HISTORY_MAX = 50


class HttpError(RuntimeError):
    pass


# ---------------- kiểm tra đầu vào ----------------
def check_url(url):
    """Chỉ http/https, có host, không ký tự điều khiển (chặn CRLF injection). Khoảng trắng trong đường dẫn được mã hoá %20."""
    u = (url or '').strip()
    if not u:
        raise HttpError('Chưa nhập URL')
    if re.search(r'[\x00-\x1f\x7f]', u):
        raise HttpError('URL chứa ký tự điều khiển không hợp lệ')
    u = u.replace(' ', '%20')
    p = urlsplit(u)
    if p.scheme not in ('http', 'https'):
        raise HttpError(f'Chỉ hỗ trợ http/https (nhận được: {p.scheme or "không có scheme"})')
    if not p.hostname:
        raise HttpError('URL thiếu host')
    try:
        p.port
    except ValueError:
        raise HttpError('Cổng không hợp lệ') from None
    return u


def check_header(name, value):
    if not TOKEN_RE.match(name or ''):
        raise HttpError(f'Tên header không hợp lệ: {name!r}')
    if BAD_VALUE_RE.search(str(value)):
        raise HttpError(f'Giá trị header "{name}" chứa ký tự điều khiển (CR/LF)')


def substitute(text, variables):
    return VAR_RE.sub(lambda m: str(variables.get(m.group(1), m.group(0))), text or '')


def unresolved(text, variables):
    return sorted({m.group(1) for m in VAR_RE.finditer(text or '') if m.group(1) not in variables})


# ---------------- mô hình request ----------------
def new_request(name='Request mới', method='GET', url=''):
    return {'id': uuid.uuid4().hex[:10], 'name': name, 'method': method, 'url': url, 'params': [], 'headers': [], 'body_type': 'none', 'body': '', 'form': [],
            'auth': {'type': 'none', 'token': '', 'user': '', 'password': '', 'key_name': 'X-API-Key', 'key_value': ''}}


def _enabled(rows):
    return [(r.get('k', ''), r.get('v', '')) for r in rows or [] if r.get('on', True) and (r.get('k') or '').strip()]


def all_texts(req):
    """Mọi chuỗi có thể chứa biến (để báo biến chưa đặt)."""
    out = [req.get('url', ''), req.get('body', '')]
    for key in ('params', 'headers', 'form'):
        for k, v in _enabled(req.get(key)):
            out += [k, v]
    a = req.get('auth') or {}
    if a.get('type') == 'bearer':
        out.append(a.get('token', ''))
    elif a.get('type') == 'basic':
        out += [a.get('user', ''), a.get('password', '')]
    elif a.get('type') == 'apikey':
        out += [a.get('key_name', ''), a.get('key_value', '')]
    return out


def missing_vars(req, variables):
    names = set()
    for t in all_texts(req):
        names.update(unresolved(t, variables))
    return sorted(names)


def build(req, variables=None):
    """Request lưu → request sẵn sàng gửi: {'method','url','headers':[(k,v)],'body':bytes|None}. Ném HttpError nếu không hợp lệ."""
    v = variables or {}
    sub = lambda s: substitute(s, v)
    method = (req.get('method') or 'GET').upper()
    if method not in METHODS:
        raise HttpError(f'Method không hỗ trợ: {method}')
    miss = missing_vars(req, v)
    if miss:
        raise HttpError('Biến chưa được đặt: ' + ', '.join('{{' + m + '}}' for m in miss))
    url = check_url(sub(req.get('url', '')))
    params = [(sub(k), sub(val)) for k, val in _enabled(req.get('params'))]
    if params:
        p = urlsplit(url)
        q = (p.query + '&' if p.query else '') + urlencode(params)
        url = p._replace(query=q, fragment='').geturl()
    headers = [(sub(k).strip(), sub(val)) for k, val in _enabled(req.get('headers'))]
    a = req.get('auth') or {}
    if a.get('type') == 'bearer' and sub(a.get('token', '')):
        headers.append(('Authorization', 'Bearer ' + sub(a['token']).strip()))
    elif a.get('type') == 'basic':
        raw = f"{sub(a.get('user', ''))}:{sub(a.get('password', ''))}".encode('utf-8')
        headers.append(('Authorization', 'Basic ' + base64.b64encode(raw).decode()))
    elif a.get('type') == 'apikey' and sub(a.get('key_name', '')).strip():
        headers.append((sub(a['key_name']).strip(), sub(a.get('key_value', ''))))
    for k, val in headers:
        check_header(k, val)
    names = {k.lower() for k, _ in headers}
    body, btype = None, req.get('body_type', 'none')
    if btype == 'json':
        body = sub(req.get('body', '')).encode('utf-8')
        if 'content-type' not in names:
            headers.append(('Content-Type', 'application/json'))
    elif btype == 'form':
        body = urlencode([(sub(k), sub(val)) for k, val in _enabled(req.get('form'))]).encode()
        if 'content-type' not in names:
            headers.append(('Content-Type', 'application/x-www-form-urlencoded'))
    elif btype == 'raw':
        body = sub(req.get('body', '')).encode('utf-8')
    if method in ('GET', 'HEAD') and body == b'':
        body = None
    names = {k.lower() for k, _ in headers}
    if 'user-agent' not in names:
        headers.append(('User-Agent', 'DevDock-API-Tester/1.0'))
    if 'accept' not in names:
        headers.append(('Accept', '*/*'))
    if 'accept-encoding' not in names:
        headers.append(('Accept-Encoding', 'identity'))
    return {'method': method, 'url': url, 'headers': headers, 'body': body}


def to_curl(prep, follow=True, verify=True):
    """Lệnh curl tương đương (escape bằng shlex.quote)."""
    parts = ['curl', '-X', prep['method'], shlex.quote(prep['url'])]
    for k, val in prep['headers']:
        if k.lower() in ('user-agent', 'accept-encoding') and val in ('DevDock-API-Tester/1.0', 'identity'):
            continue
        parts += ['-H', shlex.quote(f'{k}: {val}')]
    if prep['body'] is not None:
        parts += ['--data-raw', shlex.quote(prep['body'].decode('utf-8', errors='replace'))]
    if follow:
        parts.append('-L')
    if not verify:
        parts.append('-k')
    return ' '.join(parts)


# ---------------- gửi ----------------
class Cancel:
    def __init__(self):
        self.flag, self._conn, self._lock = False, None, threading.Lock()

    def attach(self, conn):
        with self._lock:
            self._conn = conn
        if self.flag:
            self._kill()

    def cancel(self):
        self.flag = True
        self._kill()

    def _kill(self):
        with self._lock:
            c = self._conn
        if c is not None:
            try:
                if c.sock is not None:
                    c.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass


def _decode_body(data, encoding):
    enc = (encoding or '').lower()
    try:
        if enc == 'gzip':
            return zlib.decompressobj(31).decompress(data, MAX_BODY + 1)
        if enc == 'deflate':
            return zlib.decompressobj().decompress(data, MAX_BODY + 1)
    except zlib.error:
        pass
    return data


def send(prep, follow=True, verify=True, timeout=30, cancel=None, max_redirects=5):
    """Gửi request, trả về dict kết quả. Ném HttpError (kể cả khi bị huỷ: message 'Đã huỷ')."""
    t0 = time.monotonic()
    method, url, headers, body = prep['method'], prep['url'], list(prep['headers']), prep['body']
    chain, blocked = [], ''
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    for _hop in range(max_redirects + 1):
        u = urlsplit(url)
        host, port = u.hostname, u.port
        conn = (http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx) if u.scheme == 'https' else http.client.HTTPConnection(host, port, timeout=timeout))
        if cancel:
            cancel.attach(conn)
        try:
            path = (u.path or '/') + ('?' + u.query if u.query else '')
            conn.putrequest(method, path, skip_host=any(k.lower() == 'host' for k, _ in headers), skip_accept_encoding=True)
            for k, val in headers:
                conn.putheader(k, val)
            if body is not None and not any(k.lower() == 'content-length' for k, _ in headers):
                conn.putheader('Content-Length', str(len(body)))
            conn.endheaders(body)
            resp = conn.getresponse()
            rheaders = resp.getheaders()
            data = b'' if method == 'HEAD' else resp.read(MAX_BODY + 1)
            truncated = len(data) > MAX_BODY
            data = data[:MAX_BODY]
            status, reason, ver = resp.status, resp.reason, resp.version
        except (OSError, http.client.HTTPException, ValueError) as e:
            if cancel and cancel.flag:
                raise HttpError('Đã huỷ') from None
            if isinstance(e, socket.timeout):
                raise HttpError(f'Hết thời gian chờ ({timeout}s)') from None
            if isinstance(e, ssl.SSLError):
                raise HttpError(f'Lỗi TLS: {getattr(e, "reason", None) or e} (tắt "kiểm tra TLS" nếu là chứng chỉ tự ký)') from None
            if isinstance(e, ConnectionRefusedError):
                raise HttpError(f'Máy chủ từ chối kết nối ({host}:{port or (443 if u.scheme == "https" else 80)})') from None
            if isinstance(e, socket.gaierror):
                raise HttpError(f'Không phân giải được tên miền: {host}') from None
            raise HttpError(f'Lỗi kết nối: {e}') from None
        finally:
            try:
                conn.close()
            except OSError:
                pass
        loc = dict((k.lower(), v) for k, v in rheaders).get('location')
        if follow and status in (301, 302, 303, 307, 308) and loc:
            nxt = urljoin(url, loc)
            try:
                nxt = check_url(nxt)
            except HttpError as e:
                blocked = f'Redirect bị chặn: {e}'
                break
            if urlsplit(nxt).scheme != u.scheme:
                blocked = f'Redirect sang scheme khác bị chặn: {nxt}'
                break
            if len(chain) >= max_redirects:
                blocked = f'Quá {max_redirects} lần redirect'
                break
            chain.append((status, url))
            a, b = urlsplit(url), urlsplit(nxt)
            if (a.hostname, a.port or 0) != (b.hostname, b.port or 0):  # sang host khác: không gửi kèm thông tin xác thực
                headers = [(k, v) for k, v in headers if k.lower() not in ('authorization', 'cookie', 'proxy-authorization')]
            if status in (301, 302, 303) and method not in ('GET', 'HEAD'):
                method, body = 'GET', None
                headers = [(k, v) for k, v in headers if k.lower() not in ('content-type', 'content-length')]
            url = nxt
            continue
        enc = dict((k.lower(), v) for k, v in rheaders).get('content-encoding')
        shown = _decode_body(data, enc) if enc else data
        return {'status': status, 'reason': reason, 'headers': rheaders, 'body': shown[:MAX_BODY], 'truncated': truncated or len(shown) > MAX_BODY, 'elapsed': time.monotonic() - t0,
                'size': len(data), 'url': url, 'redirects': chain, 'blocked': blocked, 'http_version': 'HTTP/1.1' if ver == 11 else 'HTTP/1.0'}
    # thoát vòng do bị chặn/quá nhiều redirect: trả về phản hồi 3xx cuối cùng
    return {'status': status, 'reason': reason, 'headers': rheaders, 'body': data, 'truncated': False, 'elapsed': time.monotonic() - t0, 'size': len(data), 'url': url,
            'redirects': chain, 'blocked': blocked, 'http_version': 'HTTP/1.1' if ver == 11 else 'HTTP/1.0'}


def pretty(body, content_type=''):
    """Văn bản hiển thị: JSON được format đẹp, còn lại decode UTF-8 (thay ký tự lỗi)."""
    text = body.decode('utf-8', errors='replace')
    if 'json' in (content_type or '').lower() or text.lstrip()[:1] in ('{', '['):
        try:
            return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except ValueError:
            pass
    return text


# ---------------- lưu trữ ----------------
def default_path():
    return os.path.join(os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config'), 'devdock', 'api_requests.json')


def redact(req):
    """Bản sao request đã xoá bí mật (dùng cho lịch sử)."""
    r = json.loads(json.dumps(req))
    a = r.get('auth') or {}
    for k in ('token', 'password', 'key_value'):
        a[k] = ''
    r['auth'] = a
    return r


class ApiStore:
    def __init__(self, path=None):
        self.path = path or default_path()
        self.data = {'collections': [], 'history': [], 'options': {'follow': True, 'verify': True, 'timeout': 30}, 'current': ''}
        try:
            with open(self.path, encoding='utf-8') as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.data.update({k: d[k] for k in self.data if k in d})
        except (OSError, ValueError):
            pass
        if not self.data['collections']:
            self.data['collections'].append(self._coll('Mặc định'))

    @staticmethod
    def _coll(name):
        return {'id': uuid.uuid4().hex[:8], 'name': name, 'vars': {}, 'requests': []}

    # --- bộ sưu tập
    @property
    def collections(self):
        return self.data['collections']

    def coll(self, cid):
        return next((c for c in self.collections if c['id'] == cid), self.collections[0])

    def add_collection(self, name):
        c = self._coll(name.strip() or 'Bộ sưu tập')
        self.collections.append(c)
        self.save()
        return c

    def rename_collection(self, cid, name):
        self.coll(cid)['name'] = name.strip() or self.coll(cid)['name']
        self.save()

    def delete_collection(self, cid):
        if len(self.collections) > 1:
            self.data['collections'] = [c for c in self.collections if c['id'] != cid]
            self.save()

    # --- request
    def save_request(self, cid, req):
        c = self.coll(cid)
        for i, r in enumerate(c['requests']):
            if r['id'] == req['id']:
                c['requests'][i] = json.loads(json.dumps(req))
                break
        else:
            c['requests'].append(json.loads(json.dumps(req)))
        self.save()

    def delete_request(self, cid, rid):
        c = self.coll(cid)
        c['requests'] = [r for r in c['requests'] if r['id'] != rid]
        self.save()

    def duplicate_request(self, cid, rid):
        c = self.coll(cid)
        src = next((r for r in c['requests'] if r['id'] == rid), None)
        if not src:
            return None
        r = json.loads(json.dumps(src))
        r['id'], r['name'] = uuid.uuid4().hex[:10], src['name'] + ' (bản sao)'
        c['requests'].append(r)
        self.save()
        return r

    # --- lịch sử
    def add_history(self, req, status, elapsed, error=''):
        self.data['history'].insert(0, {'ts': time.time(), 'req': redact(req), 'status': status, 'elapsed': elapsed, 'error': error})
        self.data['history'] = self.data['history'][:HISTORY_MAX]
        self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix='.api-')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False)
            os.chmod(tmp, 0o600)  # có thể chứa token/mật khẩu
            os.replace(tmp, self.path)
        except OSError:
            pass
