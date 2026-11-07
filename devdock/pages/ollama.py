"""Ollama AI: quản lý model (tải về, xem chi tiết, dỡ khỏi RAM, xoá) và chat trực tiếp với model cục bộ."""
import re
import shutil
import threading
import time
from collections import deque
from datetime import datetime

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..lib import ollama as O  # noqa: E402
from ..lib import projects as P  # noqa: E402
from ..lib.chats import ChatStore  # noqa: E402
from ..ui import (DataTable, InfoStrip, Page, SelectionBar, bg, button, content_box, copy_button, copy_text, esc, hbox, human, icon_btn, label, linked, toolbar, ui, vbox)  # noqa: E402
from .sysmon import card, confirm  # noqa: E402

CAP_TXT = {'completion': 'chat', 'thinking': 'suy nghĩ', 'tools': 'tools', 'vision': 'hình ảnh', 'embedding': 'embedding', 'insert': 'fim', 'reasoning': 'suy luận'}
OK, DIM = '#26a269', '#9a9996'


def iso_epoch(s):
    try:
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return 0


def caps_text(caps):
    return ', '.join(CAP_TXT.get(c, c) for c in caps) or '—'


# ======================================================================================
# Hiển thị tin nhắn: markdown nhẹ + khối code + khối suy nghĩ
# ======================================================================================
def md_to_pango(text):
    """Markdown tối giản → Pango markup (an toàn: escape trước, rồi mới thêm thẻ)."""
    out = []
    for line in esc(text).split('\n'):
        m = re.match(r'^(#{1,6})\s+(.*)$', line)
        if m:
            size = {1: 'x-large', 2: 'large'}.get(len(m.group(1)), 'medium')
            line = f'<span size="{size}" weight="bold">{m.group(2)}</span>'
        else:
            line = re.sub(r'^(\s*)[-*]\s+', r'\1  • ', line)
        line = re.sub(r'`([^`\n]+)`', r'<tt>\1</tt>', line)
        line = re.sub(r'\*\*([^*\n]+)\*\*', r'<b>\1</b>', line)
        out.append(line)
    return '\n'.join(out)


def split_blocks(text):
    """[('text'|'code', nội_dung, ngôn_ngữ)] theo các hàng rào ```. Hàng rào chưa đóng (đang stream) coi là code."""
    parts = re.split(r'```', text)
    blocks = []
    for i, p in enumerate(parts):
        if i % 2 == 0:
            if p.strip('\n') != '' or not blocks and len(parts) == 1:
                blocks.append(('text', p.strip('\n'), ''))
        else:
            lang, _, body = p.partition('\n')
            if '\n' not in p and not p.endswith('\n'):  # ```code``` cùng dòng
                lang, body = '', p
            blocks.append(('code', body.rstrip('\n'), lang.strip()))
    return blocks


class MessageView(Gtk.Box):
    """Một tin nhắn (người dùng hoặc AI). set() có thể gọi liên tục khi đang stream."""

    def __init__(self, role):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.END if role == 'user' else Gtk.Align.START)
        self.role, self.blocks, self.text, self.thinking = role, [], '', ''
        self.set_margin_start(60 if role == 'assistant' else 0)
        self.set_margin_end(0 if role == 'assistant' else 0)
        bubble = vbox(6, css_classes=['user-bubble' if role == 'user' else 'ai-bubble'])
        self.think_exp = Gtk.Expander(label='Suy nghĩ', visible=False)
        self.think_lbl = Gtk.Label(xalign=0, wrap=True, selectable=True, css_classes=['thinking'], margin_top=4)
        self.think_exp.set_child(self.think_lbl)
        bubble.append(self.think_exp)
        self.body = vbox(8)
        bubble.append(self.body)
        self.append(bubble)
        self.footer = hbox(6, visible=False)
        self.stats = label('', ['dim-label', 'caption'])
        self.footer.append(self.stats)
        self.append(self.footer)
        self.spinner = Gtk.Spinner(spinning=False, visible=False, halign=Gtk.Align.START)
        bubble.append(self.spinner)

    def busy(self, on):
        self.spinner.set_visible(on)
        self.spinner.set_spinning(on)

    def set(self, text, thinking=''):
        t_in, content = O.split_think(text)
        thinking = thinking or t_in
        if thinking != self.thinking:
            self.thinking = thinking
            self.think_exp.set_visible(bool(thinking))
            self.think_lbl.set_text(thinking)
        if self.role == 'user':
            blocks = [('text', text, '')]
        else:
            blocks = split_blocks(content) if content else []
        self.text = content
        # cập nhật tại chỗ nếu cấu trúc không đổi (tránh dựng lại widget mỗi lần nhận chữ)
        if [b[0] for b in blocks] != [b[0] for b in self.blocks]:
            while w := self.body.get_first_child():
                self.body.remove(w)
            self.blocks = []
            for kind, body, lang in blocks:
                self.body.append(self._make(kind, body, lang))
            self.blocks = list(blocks)
            return
        w = self.body.get_first_child()
        for (kind, body, lang), (okind, obody, _l) in zip(blocks, self.blocks):
            if body != obody:
                self._update(w, kind, body)
            w = w.get_next_sibling()
        self.blocks = list(blocks)

    def _make(self, kind, body, lang):
        if kind == 'code':
            box = vbox(4, css_classes=['codeblock'])
            head = hbox(6)
            head.append(label(lang or 'code', ['dim-label', 'caption'], hexpand=True))
            head.append(copy_button(lambda b=box: b.code_text, 'Sao chép code'))
            box.append(head)
            lb = Gtk.Label(xalign=0, selectable=True, css_classes=['mono'], wrap=False)
            sw = Gtk.ScrolledWindow(child=lb, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
            box.append(sw)
            box.code_text, box.lb = body, lb
            lb.set_text(body)
            return box
        lb = Gtk.Label(xalign=0, wrap=True, selectable=True, wrap_mode=2, use_markup=(self.role != 'user'))
        lb.set_markup(md_to_pango(body)) if self.role != 'user' else lb.set_text(body)
        return lb

    def _update(self, w, kind, body):
        if kind == 'code':
            w.code_text = body
            w.lb.set_text(body)
        elif self.role != 'user':
            w.set_markup(md_to_pango(body))
        else:
            w.set_text(body)

    def finish(self, stats=None):
        self.busy(False)
        if self.role == 'assistant':
            self.footer.set_visible(True)
            if not getattr(self, '_copy', None):
                self._copy = copy_button(lambda: self.text, 'Sao chép câu trả lời')
                self.footer.append(self._copy)
        if stats:
            self.stats.set_text(f"{stats['tok_per_s']:.1f} token/s · {stats['tokens']} token · {stats['total_s']:.1f}s")


# ======================================================================================
class OllamaPage(Page):
    def __init__(self, window):
        super().__init__(window)
        try:
            self.client = O.Client(window.store['ollama_host'] or None)
        except O.OllamaError:
            self.client = O.Client()
        self.chats = ChatStore()
        self.tags, self.running, self.caps, self.version = [], {}, {}, ''
        self.up, self.busy = None, False
        self.pulls = {}
        self.chat, self.msgs, self.streaming, self.cancel, self.buffers = None, [], False, None, {}
        self.syncing = False

        self.stack = Adw.ViewStack(vexpand=True)
        self.hdr_title = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.hdr_end = [icon_btn('view-refresh-symbolic', 'Làm mới', lambda: self.refresh(True)), icon_btn('preferences-system-symbolic', 'Địa chỉ máy chủ Ollama…', self.host_dialog)]
        self.stack.add_titled_with_icon(self._chat(), 'chat', 'Chat', 'chat-bubble-text-symbolic')
        self.stack.add_titled_with_icon(self._models(), 'models', 'Models', 'package-x-generic-symbolic')
        self.down = Adw.StatusPage(icon_name='network-offline-symbolic', title='Không kết nối được Ollama', vexpand=True)
        bx = hbox(8, halign=Gtk.Align.CENTER)
        bx.append(button('Thử lại', icon='view-refresh-symbolic', css=['suggested-action'], on=lambda: self.refresh(True)))
        bx.append(button('Copy lệnh khởi động', icon='edit-copy-symbolic', on=lambda: (copy_text(self, 'sudo systemctl start ollama'), self.toast('Đã sao chép: sudo systemctl start ollama'))))
        self.down.set_child(bx)
        self.outer = Gtk.Stack()
        self.outer.add_named(self.stack, 'ok')
        self.outer.add_named(self.down, 'down')
        self.set_child(self.outer)
        self.new_chat()
        self.every(4, self.refresh).every(0.08, self.flush)

    def host_dialog(self):
        d = Adw.AlertDialog(heading='Địa chỉ máy chủ Ollama', body='Để trống để dùng mặc định (http://127.0.0.1:11434 hoặc biến OLLAMA_HOST).')
        entry = Gtk.Entry(text=self.window.store['ollama_host'], placeholder_text='http://127.0.0.1:11434')
        d.set_extra_child(entry)
        d.add_response('no', 'Huỷ')
        d.add_response('yes', 'Lưu')
        d.set_response_appearance('yes', Adw.ResponseAppearance.SUGGESTED)

        def resp(_d, r):
            if r != 'yes':
                return
            try:
                self.client = O.Client(entry.get_text().strip() or None)
            except O.OllamaError as e:
                self.toast(str(e))
                return
            self.window.store['ollama_host'] = entry.get_text().strip()
            self.window.store.save()
            self.refresh(True)
        d.connect('response', resp)
        d.present(self.get_root())

    # ---------------- dữ liệu máy chủ ----------------
    def refresh(self, force=False):
        if self.busy and not force:
            return
        self.busy = True
        client = self.client

        def work():
            ver = client.version()
            tags = client.tags()
            ps = {m['name']: m for m in client.ps()}
            caps = dict(self.caps)
            for m in tags:
                if m['name'] not in caps:
                    try:
                        caps[m['name']] = client.capabilities(m['name'])
                    except O.OllamaError:
                        caps[m['name']] = []
            return ver, tags, ps, caps

        def done(res, err):
            self.busy = False
            if err or not res:
                self.up = False
                self.down.set_description((err or '') + '\nKhởi động bằng: sudo systemctl start ollama  (hoặc chạy `ollama serve`)')
                self.outer.set_visible_child_name('down')
                return
            self.version, self.tags, self.running, self.caps = res[0], res[1], res[2], res[3]
            was = self.up
            self.up = True
            self.outer.set_visible_child_name('ok')
            self.render_models()
            self.render_model_dropdown()
            if was is not True:
                self.render_chat_empty()
        bg(work, done)

    def chat_models(self):
        return [m['name'] for m in self.tags if 'completion' in self.caps.get(m['name'], [])]

    # ================= Models =================
    def _models(self):
        root = content_box(10)
        root.set_margin_bottom(0)
        cards = hbox(12, homogeneous=True)
        self.c_srv = card('Máy chủ Ollama', '—', '')
        self.c_cnt = card('Model đã tải', '—', '')
        self.c_run = card('Đang nạp trong bộ nhớ', '—', 'không có')
        for c in (self.c_srv, self.c_cnt, self.c_run):
            cards.append(c[0])
        root.append(cards)
        self.pull_entry = Gtk.Entry(placeholder_text='Tên model để tải, vd. llama3.2:3b', hexpand=True)
        self.pull_entry.connect('activate', lambda *_: self.pull(self.pull_entry.get_text().strip()))
        sug = Gtk.MenuButton(label='Gợi ý', icon_name='starred-symbolic', valign=Gtk.Align.CENTER)
        pop = Gtk.Popover()
        pb = vbox(0, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        for name, desc in O.SUGGESTIONS:
            b = Gtk.Button(css_classes=['flat'])
            col = vbox(0, halign=Gtk.Align.START)
            col.append(label(name, ['heading'], xalign=0))
            col.append(label(desc, ['dim-label', 'caption'], xalign=0))
            b.set_child(col)
            b.connect('clicked', lambda _b, n=name: (pop.popdown(), self.pull_entry.set_text(n), self.pull_entry.grab_focus()))
            pb.append(b)
        pop.set_child(Gtk.ScrolledWindow(child=pb, min_content_height=320, min_content_width=380, hscrollbar_policy=Gtk.PolicyType.NEVER))
        sug.set_popover(pop)
        root.append(toolbar(self.pull_entry, sug, button('Tải về', icon='folder-download-symbolic', css=['suggested-action'], on=lambda: self.pull(self.pull_entry.get_text().strip()))))
        self.pull_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=['boxed-list'], visible=False)
        root.append(self.pull_list)
        cols = [
            {'title': 'Model', 'get': lambda d: f"<b>{esc(d['name'])}</b>", 'markup': True, 'sort': lambda d: d['name'].lower(), 'width': 220},
            {'title': 'Họ', 'get': lambda d: d['family'], 'sort': lambda d: d['family'], 'width': 80},
            {'title': 'Tham số', 'get': lambda d: d['params'], 'sort': lambda d: _params(d['params']), 'width': 90, 'align': 1.0, 'css': ['tnum']},
            {'title': 'Lượng tử', 'get': lambda d: d['quant'], 'width': 90},
            {'title': 'Khả năng', 'get': lambda d: caps_text(self.caps.get(d['name'], [])), 'width': 150},
            {'title': 'Dung lượng', 'get': lambda d: human(d['size']), 'sort': lambda d: d['size'], 'width': 100, 'align': 1.0, 'css': ['tnum']},
            {'title': 'Cập nhật', 'get': lambda d: P.ago(iso_epoch(d['modified'])), 'sort': lambda d: iso_epoch(d['modified']), 'width': 105},
            {'title': 'Bộ nhớ', 'get': self.run_text, 'markup': True, 'expand': True, 'width': 210},
        ]
        self.mt = DataTable(cols, key=lambda d: d['name'], search_text=lambda d: f"{d['name']} {d['family']}", on_select=self.on_select)
        self.mt.sort_by(5, True)
        self.empty = Adw.StatusPage(icon_name='package-x-generic-symbolic', title='Chưa có model nào', description='Gõ tên model ở trên (hoặc bấm "Gợi ý") rồi bấm "Tải về".', vexpand=True, visible=False)
        root.append(self.mt)
        root.append(self.empty)
        outer = vbox(0)
        outer.append(root)
        self.mbar = SelectionBar()
        self.mb = {}
        mk = lambda k, t, ic, fn, css=None: self.mb.setdefault(k, button(t, icon=ic, css=css, on=fn))
        self.mbar.add(mk('chat', 'Chat', 'chat-bubble-text-symbolic', self.chat_with, ['suggested-action']), linked(mk('info', 'Chi tiết', 'dialog-information-symbolic', self.details), mk('update', 'Cập nhật', 'folder-download-symbolic', self.update_model),
                                                                                                                 mk('unload', 'Dỡ khỏi RAM', 'media-eject-symbolic', self.unload)), mk('del', 'Xoá', 'user-trash-symbolic', self.delete, ['destructive-action']))
        outer.append(self.mbar)
        return outer

    def run_text(self, d):
        r = self.running.get(d['name'])
        if not r:
            return f"<span foreground='{DIM}'>—</span>"
        where = 'trên GPU' if r['vram'] >= r['size'] * 0.95 else (f"GPU {r['vram'] * 100 // max(r['size'], 1)}%" if r['vram'] else 'trên CPU')
        return f"<span foreground='{OK}'>●</span> {where} · {human(r['size'])}"

    def render_models(self):
        self.mt.set_rows(self.tags)
        self.mt.set_visible(bool(self.tags))
        self.empty.set_visible(not self.tags)
        total = sum(m['size'] for m in self.tags)
        self.c_srv[1].set_text(f'v{self.version}')
        self.c_srv[2].set_text(self.client.host)
        self.c_cnt[1].set_text(str(len(self.tags)))
        self.c_cnt[2].set_text(f'{human(total)} trên đĩa · {len(self.chat_models())} dùng để chat')
        run = list(self.running.values())
        self.c_run[1].set_text(str(len(run)))
        self.c_run[2].set_text(', '.join(m['name'] for m in run) + (f" · VRAM {human(sum(m['vram'] for m in run))}" if run else '') if run else 'không có')

    def on_select(self, d):
        self.mbar.select(d['name'] if d else None, f"{d['params']} · {d['quant']} · {human(d['size'])}" if d else '')
        if d:
            caps = self.caps.get(d['name'], [])
            self.mb['chat'].set_sensitive('completion' in caps)
            self.mb['unload'].set_sensitive(d['name'] in self.running)

    def pull(self, name):
        if not O.valid_name(name):
            self.toast('Tên model không hợp lệ (vd. llama3.2:3b)')
            return
        if name in self.pulls:
            self.toast(f'{name} đang được tải')
            return
        self.pull_entry.set_text('')
        cancel = O.Cancel()
        row = Gtk.ListBoxRow(activatable=False)
        line = hbox(10, margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        title = label(name, ['heading'], width_chars=22, xalign=0, ellipsize=3)
        bar = Gtk.ProgressBar(hexpand=True, valign=Gtk.Align.CENTER, show_text=False)
        status = label('Đang bắt đầu…', ['dim-label', 'caption'], width_chars=34, xalign=1, ellipsize=3)
        stop = icon_btn('process-stop-symbolic', 'Huỷ tải', lambda: cancel.cancel(), ['flat', 'circular'])
        for w in (title, bar, status, stop):
            line.append(w)
        row.set_child(line)
        self.pull_list.append(row)
        self.pull_list.set_visible(True)
        job = {'row': row, 't0': time.time(), 'last': (time.time(), 0), 'cancel': cancel}
        self.pulls[name] = job

        def progress(p):
            def apply():
                if p['total']:
                    bar.set_fraction(p['fraction'])
                    now = time.time()
                    lt, lc = job['last']
                    speed = ''
                    if now - lt >= 1:
                        job['rate'] = (p['completed'] - lc) / (now - lt)
                        job['last'] = (now, p['completed'])
                    if job.get('rate'):
                        speed = f" · {human(job['rate'])}/s"
                    status.set_text(f"{p['fraction'] * 100:.0f}% · {human(p['completed'])} / {human(p['total'])}{speed}")
                else:
                    bar.pulse()
                    status.set_text(p['status'][:40])
            ui(apply)

        def work():
            return self.client.pull(name, on_progress=progress, cancel=cancel)

        def done(ok, err):
            self.pulls.pop(name, None)
            self.pull_list.remove(row)
            self.pull_list.set_visible(bool(self.pulls))
            self.toast(err or (f'Đã tải {name}' if ok else f'Đã huỷ tải {name}'))
            self.refresh(True)
        bg(work, done)

    def update_model(self):
        d = self.mt.selected()
        if d:
            self.pull(d['name'])

    def details(self):
        d = self.mt.selected()
        if d:
            DetailDialog(self, d).present(self.get_root())

    def unload(self):
        d = self.mt.selected()
        if d:
            bg(lambda: self.client.unload(d['name']), lambda _r, err: (self.toast(err or f"Đã dỡ {d['name']} khỏi bộ nhớ"), self.refresh(True)))

    def delete(self):
        d = self.mt.selected()
        if d:
            confirm(self.window, f"Xoá model {d['name']}?", f"Giải phóng {human(d['size'])} trên đĩa. Muốn dùng lại phải tải về từ đầu.", 'Xoá',
                    lambda: bg(lambda: self.client.delete(d['name']), lambda _r, err: (self.toast(err or f"Đã xoá {d['name']}"), self.refresh(True))))

    def chat_with(self, *_):
        d = self.mt.selected()
        if not d:
            return
        self.stack.set_visible_child_name('chat')
        if self.chat and not self.chat['messages']:
            self.chat['model'] = d['name']
            self.render_model_dropdown()
        else:
            self.new_chat(d['name'])

    # ================= Chat =================
    def _chat(self):
        box = hbox(0)
        left = vbox(0, width_request=250)
        nb = button('Cuộc trò chuyện mới', icon='list-add-symbolic', css=['suggested-action'], on=lambda: self.new_chat())
        nb.set_margin_start(10)
        nb.set_margin_end(10)
        nb.set_margin_top(12)
        nb.set_margin_bottom(8)
        left.append(nb)
        self.clist = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['navigation-sidebar'])
        self.clist.connect('row-selected', lambda _l, r: r and not self.syncing and self.open_chat(r.cid))
        left.append(Gtk.ScrolledWindow(child=self.clist, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        box.append(left)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        right = vbox(0, hexpand=True)
        # --- thanh trên: model + tuỳ chọn
        self.model_dd = Gtk.DropDown.new_from_strings([])
        self.model_dd.set_hexpand(True)
        self.model_dd.connect('notify::selected', lambda *_: self.on_model_change())
        self.think_sw = Gtk.Switch(active=self.window.store['ollama_think'], valign=Gtk.Align.CENTER, tooltip_text='Cho model suy nghĩ trước khi trả lời (chậm hơn nhưng thường chính xác hơn)')
        self.think_sw.connect('notify::active', lambda s, _p: (self.window.store.__setitem__('ollama_think', s.get_active()), self.window.store.save()))
        self.think_box = hbox(6, valign=Gtk.Align.CENTER)
        self.think_box.append(label('Suy nghĩ'))
        self.think_box.append(self.think_sw)
        opt = Gtk.MenuButton(icon_name='emblem-system-symbolic', tooltip_text='Tuỳ chọn: system prompt, nhiệt độ, context', valign=Gtk.Align.CENTER)
        opt.set_popover(self._options_popover())
        tb = toolbar(self.model_dd, self.think_box, opt)
        tb.set_margin_top(10)
        tb.set_margin_start(12)
        tb.set_margin_end(12)
        tb.set_margin_bottom(6)
        right.append(tb)
        # --- vùng tin nhắn
        self.msgs_box = vbox(14, margin_top=8, margin_bottom=12, margin_start=16, margin_end=16)
        self.msg_sw = Gtk.ScrolledWindow(child=self.msgs_box, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.empty_chat = Adw.StatusPage(icon_name='chat-bubble-text-symbolic', title='Bắt đầu trò chuyện', vexpand=True)
        self.chat_stack = Gtk.Stack(vexpand=True)
        self.chat_stack.add_named(self.empty_chat, 'empty')
        self.chat_stack.add_named(self.msg_sw, 'msgs')
        right.append(self.chat_stack)
        # --- ô nhập
        self.input = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=10, right_margin=10, top_margin=8, bottom_margin=8, accepts_tab=False)
        isw = Gtk.ScrolledWindow(child=self.input, min_content_height=44, max_content_height=160, propagate_natural_height=True, hexpand=True)
        isw.add_css_class('card')
        key = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        key.connect('key-pressed', self.on_key)
        self.input.add_controller(key)
        self.send_btn = button('Gửi', icon='mail-send-symbolic', css=['suggested-action'], on=self.send_or_stop)
        self.send_btn.set_valign(Gtk.Align.END)
        row = hbox(8, margin_start=12, margin_end=12, margin_bottom=12, margin_top=4)
        row.append(isw)
        row.append(self.send_btn)
        right.append(row)
        box.append(right)
        return box

    def _options_popover(self):
        pop = Gtk.Popover()
        b = vbox(10, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12, width_request=340)
        b.append(label('System prompt (cho cuộc trò chuyện này)', ['heading'], xalign=0))
        self.sys_tv = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=6, right_margin=6, top_margin=6, bottom_margin=6)
        sw = Gtk.ScrolledWindow(child=self.sys_tv, min_content_height=80, max_content_height=140)
        sw.add_css_class('card')
        self.sys_tv.get_buffer().connect('changed', lambda buf: self.chat is not None and self.chat.__setitem__('system', buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)))
        b.append(sw)
        b.append(label('Nhiệt độ (độ sáng tạo)', ['heading'], xalign=0))
        self.temp = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 2, 0.05)
        self.temp.set_value(self.window.store['ollama_temp'])
        self.temp.set_draw_value(True)
        self.temp.connect('value-changed', lambda s: (self.window.store.__setitem__('ollama_temp', round(s.get_value(), 2)), self.window.store.save()))
        b.append(self.temp)
        b.append(label('Độ dài context (0 = mặc định của model)', ['heading'], xalign=0))
        self.ctx = Gtk.SpinButton.new_with_range(0, 131072, 512)
        self.ctx.set_value(self.window.store['ollama_ctx'])
        self.ctx.connect('value-changed', lambda s: (self.window.store.__setitem__('ollama_ctx', int(s.get_value())), self.window.store.save()))
        b.append(self.ctx)
        pop.set_child(b)
        return pop

    # ---- model dropdown
    def render_model_dropdown(self):
        names = self.chat_models()
        cur = (self.chat or {}).get('model') or ''
        self.syncing = True
        self.model_dd.set_model(Gtk.StringList.new(names))
        if cur in names:
            self.model_dd.set_selected(names.index(cur))
        elif names:
            pick = next((n for n in names if n == self.window.store.get('ollama_last', '')), names[0])
            self.model_dd.set_selected(names.index(pick))
            if self.chat is not None and not self.chat['messages']:
                self.chat['model'] = pick
        self.syncing = False
        self.model_dd.set_visible(bool(names))  # không có model chat thì ẩn ô chọn thay vì hiện "(None)"
        self.model_dd.set_sensitive(bool(names) and not self.streaming)
        self.update_think_visibility()
        self.render_chat_empty()

    def cur_model(self):
        m = self.model_dd.get_model()
        i = self.model_dd.get_selected()
        return m.get_string(i) if m and m.get_n_items() and i < m.get_n_items() else ''

    def on_model_change(self):
        if self.syncing:
            return
        name = self.cur_model()
        if self.chat is not None and name:
            self.chat['model'] = name
            self.window.store['ollama_last'] = name
        self.update_think_visibility()

    def update_think_visibility(self):
        self.think_box.set_visible('thinking' in self.caps.get(self.cur_model(), []))

    def render_chat_empty(self):
        names = self.chat_models()
        if self.chat and self.chat['messages']:
            self.chat_stack.set_visible_child_name('msgs')
            return
        if not names:
            self.empty_chat.set_title('Chưa có model để chat')
            self.empty_chat.set_description('Hãy tải một model ở tab Models (vd. llama3.2:3b hoặc qwen3:4b).')
            btn = button('Đi tới Models', icon='package-x-generic-symbolic', css=['suggested-action', 'pill'], on=lambda: self.stack.set_visible_child_name('models'))
            self.empty_chat.set_child(btn)
        else:
            self.empty_chat.set_title('Bắt đầu trò chuyện')
            self.empty_chat.set_description(f'Đang dùng {self.cur_model()}. Gõ câu hỏi ở ô bên dưới — Enter để gửi, Shift+Enter xuống dòng.')
            self.empty_chat.set_child(None)
        self.chat_stack.set_visible_child_name('empty')

    # ---- danh sách hội thoại
    def render_chat_list(self):
        self.syncing = True
        self.clist.remove_all()
        for c in self.chats.chats:
            row = Gtk.ListBoxRow()
            row.cid = c['id']
            line = hbox(4, margin_start=10, margin_end=4, margin_top=4, margin_bottom=4)
            col = vbox(0, hexpand=True)
            col.append(label(c['title'], ellipsize=3, xalign=0))
            col.append(label(f"{c.get('model') or '—'} · {P.ago(c['updated'])}", ['dim-label', 'caption'], ellipsize=3))
            line.append(col)
            line.append(icon_btn('user-trash-symbolic', 'Xoá cuộc trò chuyện', lambda cid=c['id']: self.delete_chat(cid), ['flat', 'circular']))
            row.set_child(line)
            self.clist.append(row)
            if self.chat and c['id'] == self.chat['id']:
                self.clist.select_row(row)
        self.syncing = False

    def new_chat(self, model=None):
        if self.streaming:
            self.stop()
        # tái dùng cuộc trò chuyện trống thay vì tạo thêm
        empty = next((c for c in self.chats.chats if not c['messages']), None)
        c = empty or self.chats.new()
        c['model'] = model or c.get('model') or self.window.store.get('ollama_last', '')
        c['system'] = '' if not c['messages'] else c['system']
        self.open_chat(c['id'], c)

    def open_chat(self, cid, chat=None):
        if self.streaming:
            self.stop()
        self.chat = chat or self.chats.get(cid)
        if not self.chat:
            return
        while w := self.msgs_box.get_first_child():
            self.msgs_box.remove(w)
        self.msgs = []
        for m in self.chat['messages']:
            mv = MessageView(m['role'])
            mv.set(m['content'], m.get('thinking', ''))
            if m['role'] == 'assistant':
                mv.finish(m.get('stats'))
            self.msgs_box.append(mv)
            self.msgs.append(mv)
        self.sys_tv.get_buffer().set_text(self.chat.get('system', ''), -1)
        self.render_model_dropdown()
        self.render_chat_list()
        self.scroll_bottom()

    def delete_chat(self, cid):
        was = self.chat and self.chat['id'] == cid
        self.chats.delete(cid)
        if was:
            self.chat = None
            self.new_chat()
        else:
            self.render_chat_list()

    # ---- gửi / dừng
    def on_key(self, _c, keyval, _code, state):
        from gi.repository import Gdk
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not (state & Gdk.ModifierType.SHIFT_MASK) and not (state & Gdk.ModifierType.CONTROL_MASK):
            buf = self.input.get_buffer()
            n0 = buf.get_char_count()
            off = buf.get_iter_at_mark(buf.get_insert()).get_offset()
            # Cho TextView/bộ gõ (IME) xử lý Enter trước. Nếu Enter chỉ chèn xuống dòng thì coi là "gửi"; nếu IME đã nuốt Enter (chốt chữ) thì không gửi.
            GLib.idle_add(lambda: self.enter_check(n0, off) and False)
        return False

    def enter_check(self, n0, off):
        buf = self.input.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        if buf.get_char_count() == n0 + 1 and 0 <= off < len(text) and text[off] == '\n':
            buf.delete(buf.get_iter_at_offset(off), buf.get_iter_at_offset(off + 1))
            self.send()

    def send_or_stop(self):
        self.stop() if self.streaming else self.send()

    def send(self):
        if self.streaming or not self.chat:
            return
        buf = self.input.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        model = self.cur_model()
        if not text:
            return
        if not model:
            self.toast('Chưa có model để chat — hãy tải một model ở tab Models')
            return
        buf.set_text('', -1)
        self.chat['model'] = model
        self.chat['messages'].append({'role': 'user', 'content': text})
        um = MessageView('user')
        um.set(text)
        am = MessageView('assistant')
        am.busy(True)
        for w in (um, am):
            self.msgs_box.append(w)
            self.msgs.append(w)
        self.chat_stack.set_visible_child_name('msgs')
        self.chats.touch(self.chat)
        self.render_chat_list()
        payload = []
        if self.chat.get('system', '').strip():
            payload.append({'role': 'system', 'content': self.chat['system'].strip()})
        payload += [{'role': m['role'], 'content': m['content']} for m in self.chat['messages']]
        st = self.window.store
        options = {'temperature': float(st['ollama_temp'])}
        if int(st['ollama_ctx']) > 0:
            options['num_ctx'] = int(st['ollama_ctx'])
        think = bool(st['ollama_think']) if 'thinking' in self.caps.get(model, []) else None
        self.stream_state = {'content': '', 'thinking': '', 'stats': None, 'error': None, 'finished': False}
        self.streaming, self.cancel, self.am = True, O.Cancel(), am
        self.send_btn.set_label('Dừng')
        self.send_btn.remove_css_class('suggested-action')
        self.send_btn.add_css_class('destructive-action')
        self.model_dd.set_sensitive(False)
        state, cancel, client = self.stream_state, self.cancel, self.client

        def work():
            try:
                for kind, val in client.chat(model, payload, options=options, think=think, cancel=cancel):
                    if kind == 'content':
                        state['content'] += val
                    elif kind == 'thinking':
                        state['thinking'] += val
                    else:
                        state['stats'] = val
            except O.OllamaError as e:
                state['error'] = str(e)
            finally:
                state['finished'] = True
        threading.Thread(target=work, daemon=True).start()
        self.scroll_bottom(force=True)

    def stop(self):
        if self.cancel:
            self.cancel.cancel()

    def flush(self):
        """Chạy mỗi 80ms: đưa chữ mới nhận từ luồng nền lên giao diện (gộp để không bị nghẽn)."""
        if not self.streaming:
            return
        s, am = self.stream_state, self.am
        was_bottom = self.at_bottom()
        if s['content'] != am.text or s['thinking'] != am.thinking:
            am.set(s['content'], s['thinking'])
            if am.think_exp.get_visible() and not s['content']:
                am.think_exp.set_expanded(True)  # đang suy nghĩ: mở để xem
            if was_bottom:
                self.scroll_bottom(force=True)
        if s['finished']:
            self.finish_stream()

    def finish_stream(self):
        s, am = self.stream_state, self.am
        self.streaming = False
        self.send_btn.set_label('Gửi')
        self.send_btn.remove_css_class('destructive-action')
        self.send_btn.add_css_class('suggested-action')
        self.model_dd.set_sensitive(True)
        am.set(s['content'], s['thinking'])
        am.think_exp.set_expanded(False)
        am.finish(s['stats'])
        content = s['content']
        if s['error']:
            content = (content + '\n\n' if content else '') + f"⚠ {s['error']}"
            am.set(content, s['thinking'])
            self.toast(s['error'])
        elif self.cancel and self.cancel.flag and content:
            content += '\n\n[đã dừng]'
            am.set(content, s['thinking'])
        if content or s['thinking']:
            msg = {'role': 'assistant', 'content': O.split_think(content)[1] if O.split_think(content)[0] else content}
            if s['thinking'] or O.split_think(content)[0]:
                msg['thinking'] = s['thinking'] or O.split_think(content)[0]
            if s['stats']:
                msg['stats'] = s['stats']
            if not s['error']:
                self.chat['messages'].append(msg)
            else:
                self.chat['messages'].append({'role': 'assistant', 'content': content})
            self.chats.touch(self.chat)
            self.render_chat_list()
        else:
            self.chat['messages'].pop() if self.chat['messages'] and self.chat['messages'][-1]['role'] == 'user' and not s['error'] else None
        self.cancel = None
        self.scroll_bottom(force=True)

    def at_bottom(self):
        a = self.msg_sw.get_vadjustment()
        return a.get_value() + a.get_page_size() >= a.get_upper() - 60

    def scroll_bottom(self, force=False):
        a = self.msg_sw.get_vadjustment()
        GLib.idle_add(lambda: a.set_value(a.get_upper()) or False)

    def show_tool(self, name):  # chế độ chụp ảnh kiểm thử
        self.stack.set_visible_child_name(name)

    def shutdown(self):
        self.stop()
        for j in self.pulls.values():
            j['cancel'].cancel()


def _params(s):
    m = re.match(r'^([\d.]+)\s*([KMB]?)', str(s or ''), re.I)
    if not m:
        return 0
    return float(m.group(1)) * {'': 1, 'K': 1e3, 'M': 1e6, 'B': 1e9}[m.group(2).upper()]


class DetailDialog(Adw.Dialog):
    def __init__(self, page, model):
        super().__init__(title=model['name'], content_width=760, content_height=640)
        self.page = page
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        box = vbox(10, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.info = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=['boxed-list'])
        box.append(self.info)
        self.tg = Adw.ToggleGroup(halign=Gtk.Align.START)
        for n, t in (('modelfile', 'Modelfile'), ('template', 'Template'), ('parameters', 'Tham số'), ('license', 'License')):
            self.tg.add(Adw.Toggle(name=n, label=t))
        self.tg.set_active_name('modelfile')
        self.tg.connect('notify::active-name', lambda *_: self.show_text())
        box.append(self.tg)
        self.tv_ = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=8, right_margin=8, top_margin=6, bottom_margin=6, cursor_visible=False)
        sw = Gtk.ScrolledWindow(child=self.tv_, vexpand=True)
        sw.add_css_class('codebox')
        box.append(sw)
        tv.set_content(box)
        self.set_child(tv)
        self.data = None
        self.model = model
        self.show_text('Đang đọc…')
        bg(lambda: page.client.show(model['name']), self.loaded)

    def loaded(self, d, err):
        if err:
            self.show_text(err)
            return
        self.data = d
        det = d['details']
        for k, v in (('Họ model', det.get('family', '')), ('Tham số', det.get('parameter_size', '')), ('Lượng tử hoá', det.get('quantization_level', '')), ('Định dạng', det.get('format', '')),
                     ('Độ dài context tối đa', f"{d['context_length']:,}" if d['context_length'] else '—'), ('Kích thước embedding', str(d['embedding_length'] or '—')),
                     ('Khả năng', caps_text(d['capabilities'])), ('Dung lượng', human(self.model['size']))):
            r = Adw.ActionRow(title=esc(k), subtitle=esc(v) or '—')
            r.add_css_class('property')
            self.info.append(r)
        self.show_text()

    def show_text(self, override=None):
        buf = self.tv_.get_buffer()
        if override is not None:
            buf.set_text(override, -1)
        elif self.data:
            buf.set_text(self.data.get(self.tg.get_active_name(), '') or '(trống)', -1)
