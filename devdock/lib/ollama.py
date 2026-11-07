"""Ollama qua HTTP API (urllib, không cần thư viện ngoài): danh sách model, chi tiết, tải về (streaming), xoá, dỡ khỏi bộ nhớ, chat streaming."""
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_HOST = 'http://127.0.0.1:11434'
NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._\-/:@]{0,200}$')

# Gợi ý model phổ biến (người dùng vẫn nhập tên bất kỳ). (tên, mô tả)
SUGGESTIONS = [
    ('llama3.2:3b', 'Meta Llama 3.2 3B — nhỏ, nhanh, dùng hằng ngày'),
    ('llama3.1:8b', 'Meta Llama 3.1 8B — cân bằng'),
    ('qwen3:4b', 'Qwen3 4B — đa ngôn ngữ, có chế độ suy nghĩ'),
    ('qwen3:8b', 'Qwen3 8B — mạnh hơn, cần ~6 GB RAM/VRAM'),
    ('qwen2.5-coder:7b', 'Qwen2.5 Coder 7B — viết/giải thích code'),
    ('gemma3:4b', 'Google Gemma 3 4B'),
    ('mistral:7b', 'Mistral 7B'),
    ('phi4-mini', 'Microsoft Phi-4 mini — nhỏ gọn'),
    ('deepseek-r1:7b', 'DeepSeek-R1 7B — suy luận'),
    ('nomic-embed-text', 'Embedding (tìm kiếm ngữ nghĩa), không chat'),
    ('bge-m3', 'Embedding đa ngôn ngữ, không chat'),
]


class OllamaError(RuntimeError):
    pass


def normalize_host(host=None):
    """Chuẩn hoá địa chỉ máy chủ: tham số > biến OLLAMA_HOST > mặc định. Chỉ nhận http/https."""
    h = (host or os.environ.get('OLLAMA_HOST') or DEFAULT_HOST).strip()
    if '://' not in h:
        h = 'http://' + h
    u = urllib.parse.urlsplit(h)
    if u.scheme not in ('http', 'https') or not u.hostname:
        raise OllamaError(f'Địa chỉ Ollama không hợp lệ: {host}')
    hostname = '127.0.0.1' if u.hostname in ('0.0.0.0', '::') else u.hostname  # OLLAMA_HOST=0.0.0.0 nghĩa là lắng nghe mọi nơi
    return f'{u.scheme}://{hostname}:{u.port or 11434}'


def valid_name(name):
    return bool(name) and bool(NAME_RE.match(name))


class Cancel:
    """Cờ huỷ cho tác vụ streaming; huỷ sẽ đóng kết nối để luồng đọc thoát ngay."""

    def __init__(self):
        self.flag, self._resp, self._lock = False, None, threading.Lock()

    def attach(self, resp):
        with self._lock:
            self._resp = resp
        if self.flag:
            self._close()

    def cancel(self):
        self.flag = True
        self._close()

    def _close(self):
        with self._lock:
            r = self._resp
        if r is not None:
            try:
                r.close()
            except OSError:
                pass


class Client:
    def __init__(self, host=None):
        self.host = normalize_host(host)

    # ---- HTTP
    def _open(self, method, path, body=None, timeout=15):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.host + path, data=data, method=method, headers={'Content-Type': 'application/json', 'User-Agent': 'DevDock'})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode()).get('error', '')
            except (ValueError, OSError):
                msg = ''
            raise OllamaError(msg or f'Ollama trả về lỗi {e.code}') from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            reason = getattr(e, 'reason', e)
            raise OllamaError(f'Không kết nối được Ollama tại {self.host} ({reason}). Ollama đã chạy chưa?') from None

    def _json(self, method, path, body=None, timeout=15):
        with self._open(method, path, body, timeout) as r:
            raw = r.read()
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            raise OllamaError('Phản hồi không hợp lệ từ Ollama') from None

    def _stream(self, method, path, body, cancel, timeout=600):
        """Đọc NDJSON: trả về từng dict. Tôn trọng `cancel`."""
        resp = self._open(method, path, body, timeout)
        if cancel:
            cancel.attach(resp)
        try:
            for line in resp:
                if cancel and cancel.flag:
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and ev.get('error'):
                    raise OllamaError(str(ev['error']))
                yield ev
        except (OSError, ValueError) as e:
            if cancel and cancel.flag:
                return
            raise OllamaError(f'Mất kết nối khi đang nhận dữ liệu: {e}') from None
        finally:
            try:
                resp.close()
            except OSError:
                pass

    # ---- thông tin
    def version(self):
        return self._json('GET', '/api/version', timeout=4).get('version', '')

    def tags(self):
        out = []
        for m in self._json('GET', '/api/tags').get('models', []):
            d = m.get('details') or {}
            out.append({'name': m.get('name') or m.get('model', ''), 'size': int(m.get('size') or 0), 'modified': m.get('modified_at', ''), 'digest': m.get('digest', ''),
                        'family': d.get('family', ''), 'params': d.get('parameter_size', ''), 'quant': d.get('quantization_level', ''), 'format': d.get('format', '')})
        return sorted(out, key=lambda m: m['name'].lower())

    def ps(self):
        return [{'name': m.get('name', ''), 'size': int(m.get('size') or 0), 'vram': int(m.get('size_vram') or 0), 'expires': m.get('expires_at', ''), 'ctx': m.get('context_length', 0)}
                for m in self._json('GET', '/api/ps').get('models', [])]

    def show(self, name):
        if not valid_name(name):
            raise OllamaError('Tên model không hợp lệ')
        v = self._json('POST', '/api/show', {'model': name}, timeout=30)
        info = v.get('model_info') or {}
        ctx = next((val for k, val in info.items() if k.endswith('.context_length')), None)
        emb = next((val for k, val in info.items() if k.endswith('.embedding_length')), None)
        return {'capabilities': v.get('capabilities') or [], 'modelfile': v.get('modelfile', ''), 'template': v.get('template', ''), 'parameters': v.get('parameters', ''),
                'license': v.get('license', ''), 'details': v.get('details') or {}, 'context_length': ctx, 'embedding_length': emb, 'modified': v.get('modified_at', '')}

    def capabilities(self, name):
        return self.show(name)['capabilities']

    # ---- thao tác
    def delete(self, name):
        if not valid_name(name):
            raise OllamaError('Tên model không hợp lệ')
        self._json('DELETE', '/api/delete', {'model': name}, timeout=60)

    def unload(self, name):
        if not valid_name(name):
            raise OllamaError('Tên model không hợp lệ')
        self._json('POST', '/api/generate', {'model': name, 'keep_alive': 0}, timeout=60)

    def pull(self, name, on_progress=None, cancel=None):
        """Tải model; gọi on_progress({'status','fraction','completed','total'}). Trả về True nếu xong, False nếu bị huỷ."""
        if not valid_name(name):
            raise OllamaError('Tên model không hợp lệ (vd. llama3.2:3b)')
        layers = {}
        for ev in self._stream('POST', '/api/pull', {'model': name, 'stream': True}, cancel, timeout=120):
            st = ev.get('status', '')
            if ev.get('digest') and ev.get('total'):
                layers[ev['digest']] = (int(ev['total']), int(ev.get('completed') or 0))
            total = sum(t for t, _ in layers.values())
            done = sum(c for _, c in layers.values())
            if on_progress:
                on_progress({'status': st, 'total': total, 'completed': done, 'fraction': (done / total) if total else 0.0})
            if st == 'success':
                return True
        return not (cancel and cancel.flag)

    def chat(self, model, messages, options=None, think=None, cancel=None):
        """Chat streaming. Sinh ('thinking'|'content', text) rồi cuối cùng ('done', thống kê)."""
        if not valid_name(model):
            raise OllamaError('Tên model không hợp lệ')
        body = {'model': model, 'messages': messages, 'stream': True}
        if options:
            body['options'] = options
        if think is not None:
            body['think'] = bool(think)
        for ev in self._stream('POST', '/api/chat', body, cancel, timeout=600):
            msg = ev.get('message') or {}
            if msg.get('thinking'):
                yield 'thinking', msg['thinking']
            if msg.get('content'):
                yield 'content', msg['content']
            if ev.get('done'):
                n, dur = ev.get('eval_count') or 0, ev.get('eval_duration') or 0
                yield 'done', {'tokens': n, 'tok_per_s': (n / (dur / 1e9)) if dur else 0.0, 'total_s': (ev.get('total_duration') or 0) / 1e9, 'reason': ev.get('done_reason', '')}
                return


def split_think(text):
    """Tách phần suy nghĩ khỏi nội dung. Hỗ trợ <think>...</think> (kể cả chưa đóng khi đang stream) và
    trường hợp model chỉ xuất thẻ đóng </think> (suy luận nằm trước nó). Trả (suy_nghĩ, câu_trả_lời)."""
    m = re.match(r'^\s*<think>(.*?)(?:</think>|$)', text, re.S)
    if m:
        return m.group(1).strip(), text[m.end():].lstrip()
    if '</think>' in text:
        before, _, after = text.partition('</think>')
        return before.strip(), after.lstrip()
    return '', text
