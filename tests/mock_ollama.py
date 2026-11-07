"""Máy chủ Ollama giả (HTTP) dùng cho test: danh sách model, show, delete, pull streaming, chat streaming, unload."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class State:
    def __init__(self):
        self.models = {
            'tiny:1b': {'size': 700_000_000, 'family': 'llama', 'params': '1B', 'quant': 'Q4_K_M', 'caps': ['completion']},
            'think:4b': {'size': 2_500_000_000, 'family': 'qwen3', 'params': '4B', 'quant': 'Q4_K_M', 'caps': ['completion', 'thinking']},
            'embed:m': {'size': 500_000_000, 'family': 'bert', 'params': '500M', 'quant': 'F16', 'caps': ['embedding']},
        }
        self.running = {'tiny:1b'}
        self.chat_requests = []
        self.pull_delay = 0.02


def make_server(state=None):
    st = state or State()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _body(self):
            n = int(self.headers.get('content-length') or 0)
            return json.loads(self.rfile.read(n) or b'{}') if n else {}

        def _send(self, obj, code=200):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _line(self, obj):
            self.wfile.write(json.dumps(obj).encode() + b'\n')
            self.wfile.flush()

        def do_GET(self):
            if self.path == '/api/version':
                return self._send({'version': '9.9.9-mock'})
            if self.path == '/api/tags':
                return self._send({'models': [{'name': n, 'model': n, 'size': m['size'], 'modified_at': '2026-01-01T00:00:00Z', 'digest': 'd-' + n,
                                               'details': {'family': m['family'], 'parameter_size': m['params'], 'quantization_level': m['quant'], 'format': 'gguf'}} for n, m in st.models.items()]})
            if self.path == '/api/ps':
                return self._send({'models': [{'name': n, 'size': st.models[n]['size'], 'size_vram': st.models[n]['size'] // 2, 'expires_at': 'x', 'context_length': 4096} for n in st.running if n in st.models]})
            self._send({'error': 'not found'}, 404)

        def do_DELETE(self):
            b = self._body()
            if self.path == '/api/delete':
                if b.get('model') in st.models:
                    del st.models[b['model']]
                    st.running.discard(b['model'])
                    return self._send({})
                return self._send({'error': f"model '{b.get('model')}' not found"}, 404)
            self._send({'error': 'not found'}, 404)

        def do_POST(self):
            b = self._body()
            if self.path == '/api/show':
                m = st.models.get(b.get('model'))
                if not m:
                    return self._send({'error': f"model '{b.get('model')}' not found"}, 404)
                return self._send({'capabilities': m['caps'], 'modelfile': 'FROM x', 'template': '{{ .Prompt }}', 'parameters': 'stop "<x>"', 'license': 'MIT', 'modified_at': '2026-01-01T00:00:00Z',
                                   'details': {'family': m['family'], 'parameter_size': m['params'], 'quantization_level': m['quant'], 'format': 'gguf'},
                                   'model_info': {'llama.context_length': 8192, 'llama.embedding_length': 2048}})
            if self.path == '/api/generate':
                if b.get('keep_alive') == 0:
                    st.running.discard(b.get('model'))
                return self._send({'done': True})
            if self.path == '/api/pull':
                name = b.get('model')
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson')
                self.end_headers()
                if name == 'does-not-exist':
                    return self._line({'error': 'pull model manifest: file does not exist'})
                self._line({'status': 'pulling manifest'})
                total = 1000
                for i in range(1, 6):
                    self._line({'status': 'pulling abc', 'digest': 'sha256:abc', 'total': total, 'completed': total * i // 5})
                    time.sleep(st.pull_delay)
                self._line({'status': 'success'})
                st.models[name] = {'size': total, 'family': 'new', 'params': '1B', 'quant': 'Q4', 'caps': ['completion']}
                return
            if self.path == '/api/chat':
                st.chat_requests.append(b)
                m = st.models.get(b.get('model'))
                if not m:
                    return self._send({'error': f"model '{b.get('model')}' not found"}, 404)
                if b.get('think') and 'thinking' not in m['caps']:
                    return self._send({'error': f"\"{b['model']}\" does not support thinking"}, 400)
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson')
                self.end_headers()
                if b.get('think'):
                    for w in ('Hmm', ' let me', ' think'):
                        self._line({'message': {'role': 'assistant', 'content': '', 'thinking': w}, 'done': False})
                last = b['messages'][-1]['content']
                words = ('Echo: ' + last).split(' ')
                for i, w in enumerate(words):
                    self._line({'message': {'role': 'assistant', 'content': w + (' ' if i < len(words) - 1 else '')}, 'done': False})
                    time.sleep(0.01)
                self._line({'message': {'role': 'assistant', 'content': ''}, 'done': True, 'done_reason': 'stop', 'eval_count': len(words), 'eval_duration': 1_000_000_000, 'total_duration': 2_000_000_000})
                return
            self._send({'error': 'not found'}, 404)

    srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
    srv.handle_error = lambda *a: None  # client huỷ giữa chừng => BrokenPipe là bình thường
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, st
