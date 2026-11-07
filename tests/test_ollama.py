import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from devdock.lib import ollama as O  # noqa: E402
from devdock.lib.chats import ChatStore  # noqa: E402
from mock_ollama import make_server  # noqa: E402


class HostTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(O.normalize_host('127.0.0.1:11434'), 'http://127.0.0.1:11434')
        self.assertEqual(O.normalize_host('0.0.0.0'), 'http://127.0.0.1:11434')
        self.assertEqual(O.normalize_host('http://box.lan:9000'), 'http://box.lan:9000')
        for bad in ('ftp://x', 'file:///etc/passwd', 'http://'):
            with self.assertRaises(O.OllamaError):
                O.normalize_host(bad)

    def test_names_and_think_split(self):
        for ok in ('llama3.2:3b', 'library/qwen3:4b', 'hf.co/user/model:Q4_K_M', 'bge-m3'):
            self.assertTrue(O.valid_name(ok), ok)
        for bad in ('', '-rf', 'a b', 'x;rm', 'a\nb', '../x'.replace('.', '!')):
            self.assertFalse(O.valid_name(bad), bad)
        self.assertEqual(O.split_think('<think>abc</think>\n\nHello'), ('abc', 'Hello'))
        self.assertEqual(O.split_think('<think>still thinking'), ('still thinking', ''))
        self.assertEqual(O.split_think('no think'), ('', 'no think'))
        self.assertEqual(O.split_think('reasoning only...\n</think>\n\nAnswer'), ('reasoning only...', 'Answer'))  # chỉ có thẻ đóng (qwen3 khi tắt suy nghĩ)
        self.assertEqual(O.split_think('a <think> in the middle'), ('', 'a <think> in the middle'))


class MockClientTest(unittest.TestCase):
    def setUp(self):
        self.srv, self.state = make_server()
        self.c = O.Client(f'http://127.0.0.1:{self.srv.server_address[1]}')

    def tearDown(self):
        self.srv.shutdown()

    def test_info(self):
        self.assertEqual(self.c.version(), '9.9.9-mock')
        tags = {m['name']: m for m in self.c.tags()}
        self.assertEqual(set(tags), {'tiny:1b', 'think:4b', 'embed:m'})
        self.assertEqual((tags['think:4b']['params'], tags['think:4b']['quant'], tags['think:4b']['family']), ('4B', 'Q4_K_M', 'qwen3'))
        ps = self.c.ps()
        self.assertEqual([m['name'] for m in ps], ['tiny:1b'])
        self.assertEqual(ps[0]['vram'], ps[0]['size'] // 2)
        s = self.c.show('think:4b')
        self.assertEqual((s['capabilities'], s['context_length'], s['embedding_length']), (['completion', 'thinking'], 8192, 2048))
        with self.assertRaises(O.OllamaError) as e:
            self.c.show('nope')
        self.assertIn('not found', str(e.exception))
        with self.assertRaises(O.OllamaError):
            self.c.show('bad name;')

    def test_delete_and_unload(self):
        self.c.unload('tiny:1b')
        self.assertEqual(self.c.ps(), [])
        self.c.delete('embed:m')
        self.assertNotIn('embed:m', [m['name'] for m in self.c.tags()])
        with self.assertRaises(O.OllamaError):
            self.c.delete('embed:m')

    def test_pull_progress_and_result(self):
        seen = []
        ok = self.c.pull('brand:new', on_progress=seen.append)
        self.assertTrue(ok)
        self.assertEqual(seen[-1]['status'], 'success')
        fr = [s['fraction'] for s in seen if s['total']]
        self.assertEqual(fr, sorted(fr))
        self.assertAlmostEqual(fr[-1], 1.0)
        self.assertIn('brand:new', [m['name'] for m in self.c.tags()])

    def test_pull_error_and_invalid(self):
        with self.assertRaises(O.OllamaError) as e:
            self.c.pull('does-not-exist')
        self.assertIn('does not exist', str(e.exception))
        with self.assertRaises(O.OllamaError):
            self.c.pull('bad name')

    def test_pull_can_be_cancelled(self):
        self.state.pull_delay = 0.5
        cancel, res = O.Cancel(), {}
        t = threading.Thread(target=lambda: res.update(r=self.c.pull('slow:1', cancel=cancel)))
        t.start()
        time.sleep(0.7)
        cancel.cancel()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertFalse(res['r'])
        self.assertNotIn('slow:1', [m['name'] for m in self.c.tags()])

    def test_chat_stream_thinking_and_stats(self):
        ev = list(self.c.chat('think:4b', [{'role': 'user', 'content': 'hello world'}], options={'temperature': 0.2}, think=True))
        kinds = [k for k, _ in ev]
        self.assertEqual(kinds[:3], ['thinking'] * 3)
        text = ''.join(v for k, v in ev if k == 'content')
        self.assertEqual(text, 'Echo: hello world')
        self.assertEqual(ev[-1][0], 'done')
        self.assertEqual(ev[-1][1]['tokens'], 3)
        self.assertAlmostEqual(ev[-1][1]['tok_per_s'], 3.0)
        req = self.state.chat_requests[-1]
        self.assertEqual((req['stream'], req['think'], req['options']), (True, True, {'temperature': 0.2}))

    def test_think_not_sent_unless_requested(self):
        list(self.c.chat('tiny:1b', [{'role': 'user', 'content': 'x'}]))
        self.assertNotIn('think', self.state.chat_requests[-1])
        with self.assertRaises(O.OllamaError):
            list(self.c.chat('tiny:1b', [{'role': 'user', 'content': 'x'}], think=True))  # model không hỗ trợ suy nghĩ -> lỗi rõ ràng

    def test_chat_cancel_midstream(self):
        cancel, got = O.Cancel(), []
        gen = self.c.chat('tiny:1b', [{'role': 'user', 'content': ' '.join(['w'] * 200)}], cancel=cancel)
        for k, v in gen:
            got.append(v)
            if len(got) == 3:
                cancel.cancel()
        self.assertLess(len(got), 100)


class OfflineTest(unittest.TestCase):
    def test_connection_refused_is_friendly(self):
        c = O.Client('http://127.0.0.1:9')
        with self.assertRaises(O.OllamaError) as e:
            c.tags()
        self.assertIn('Ollama đã chạy chưa', str(e.exception))


class ChatStoreTest(unittest.TestCase):
    def test_roundtrip_title_order_and_perms(self):
        path = os.path.join(tempfile.mkdtemp(), 'chats.json')
        s = ChatStore(path)
        a, b = s.new('m'), s.new('m')
        a['messages'].append({'role': 'user', 'content': '  Xin chào   thế giới  '})
        s.touch(a)
        self.assertEqual(s.chats[0]['id'], a['id'])
        self.assertEqual(a['title'], 'Xin chào thế giới')
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), '0o600')
        s2 = ChatStore(path)
        self.assertEqual([c['id'] for c in s2.chats], [a['id'], b['id']])
        s2.delete(a['id'])
        self.assertEqual(len(ChatStore(path).chats), 1)

    def test_corrupt_file_is_ignored(self):
        path = os.path.join(tempfile.mkdtemp(), 'chats.json')
        with open(path, 'w') as f:
            f.write('{not json')
        self.assertEqual(ChatStore(path).chats, [])


def real_ollama():
    try:
        return O.Client().version()
    except O.OllamaError:
        return None


@unittest.skipUnless(real_ollama(), 'cần Ollama thật đang chạy')
class RealOllamaTest(unittest.TestCase):
    """Chỉ đọc + một lượt chat ngắn với model chat có sẵn (không tải/xoá model của người dùng)."""

    def test_real_read_and_short_chat(self):
        c = O.Client()
        tags = c.tags()
        self.assertTrue(all(m['size'] > 0 for m in tags))
        chat_models = [m['name'] for m in tags if 'completion' in c.capabilities(m['name'])]
        if not chat_models:
            self.skipTest('không có model chat')
        small = min(chat_models, key=lambda n: next(m['size'] for m in tags if m['name'] == n))
        caps = c.capabilities(small)
        ev = list(c.chat(small, [{'role': 'user', 'content': 'Reply with the single word: ok'}], options={'num_predict': 40, 'temperature': 0}, think=False if 'thinking' in caps else None))
        self.assertEqual(ev[-1][0], 'done')
        self.assertGreater(ev[-1][1]['tokens'], 0)
        self.assertTrue(any(k == 'content' for k, _ in ev))


if __name__ == '__main__':
    unittest.main()
