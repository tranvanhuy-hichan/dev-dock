"""Lưu lịch sử hội thoại Ollama (JSON, ghi nguyên tử) ở ~/.local/share/devdock/ollama_chats.json."""
import json
import os
import tempfile
import time
import uuid

MAX_CHATS = 100


def default_path():
    return os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'), 'devdock', 'ollama_chats.json')


class ChatStore:
    def __init__(self, path=None):
        self.path = path or default_path()
        self.chats = []
        try:
            with open(self.path, encoding='utf-8') as f:
                data = json.load(f)
            self.chats = [c for c in data if isinstance(c, dict) and c.get('id') and isinstance(c.get('messages'), list)]
        except (OSError, ValueError):
            pass

    def new(self, model=''):
        c = {'id': uuid.uuid4().hex[:10], 'title': 'Cuộc trò chuyện mới', 'model': model, 'system': '', 'messages': [], 'updated': time.time()}
        self.chats.insert(0, c)
        return c

    def get(self, cid):
        return next((c for c in self.chats if c['id'] == cid), None)

    def delete(self, cid):
        self.chats = [c for c in self.chats if c['id'] != cid]
        self.save()

    def touch(self, chat):
        """Cập nhật tiêu đề (từ câu hỏi đầu) và thời gian, đưa lên đầu danh sách, rồi lưu."""
        chat['updated'] = time.time()
        if chat['title'] == 'Cuộc trò chuyện mới':
            first = next((m['content'] for m in chat['messages'] if m['role'] == 'user'), '')
            if first.strip():
                chat['title'] = ' '.join(first.split())[:48] + ('…' if len(' '.join(first.split())) > 48 else '')
        if chat in self.chats:
            self.chats.remove(chat)
        self.chats.insert(0, chat)
        self.save()

    def save(self):
        self.chats = self.chats[:MAX_CHATS]
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix='.chats-')
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.chats, f, ensure_ascii=False)
            os.chmod(tmp, 0o600)  # nội dung hội thoại là riêng tư
            os.replace(tmp, self.path)
        except OSError:
            pass
