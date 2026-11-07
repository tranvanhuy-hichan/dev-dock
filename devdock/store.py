"""Cài đặt người dùng, lưu ở ~/.config/devdock/settings.json (ghi nguyên tử)."""
import json
import os
import time

DEFAULTS = {
    'theme': 'system', 'module': 'projects', 'project_roots': [], 'services': [],
    # DiskCleaner
    'trash_large': True, 'min_mb': 5, 'large_mb': 200, 'scan_large': True, 'dup_scan': True, 'dup_mb': 10,
    'ignored': [], 'ignored_titles': {}, 'total_freed': 0, 'clean_count': 0, 'last_scan': None, 'first_run': None,
    # Docker compose
    'compose_files': [],
    # Log Viewer
    'log_files': [],
    # Ollama
    'ollama_host': '', 'ollama_temp': 0.7, 'ollama_ctx': 0, 'ollama_think': True,
}


def _path():
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(base, 'devdock', 'settings.json')


class Store(dict):
    def __init__(self, path=None):
        super().__init__({k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()})
        self.path = path or _path()
        try:
            with open(self.path, encoding='utf-8') as f:
                self.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
        if not self['first_run']:
            self['first_run'] = time.time()
            self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self, f, ensure_ascii=False)
            os.replace(tmp, self.path)  # ghi nguyên tử để không hỏng file nếu bị tắt giữa chừng
        except OSError:
            pass
