"""Mục gộp ở sidebar: nhiều trang liên quan nằm chung một mục, chuyển qua tab ở thanh tiêu đề.

Các trang con vẫn là Page bình thường và vẫn được đăng ký trong window.pages theo id riêng (vd. 'git'),
nên mọi nơi gọi select_module('git') / window.pages['git'] đều chạy như cũ. Trang con chỉ được dựng khi mở lần đầu."""
import importlib
import traceback

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk  # noqa: E402


class Hub(Adw.Bin):
    def __init__(self, window, tools):
        """tools: [(id_con, tiêu_đề_tab, icon, 'module:Class')]"""
        super().__init__()
        self.window, self.tools = window, tools
        self.children = {}
        self.stack = Adw.ViewStack(vexpand=True)
        self.switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.bins = {}
        for tid, title, icon, _spec in tools:
            b = Adw.Bin()
            self.bins[tid] = b
            self.stack.add_titled_with_icon(b, tid, title, icon)
        self.set_child(self.stack)
        self.stack.connect('notify::visible-child-name', lambda *_: self._changed())
        self._ensure(tools[0][0])

    # ---- thuộc tính mà MainWindow.apply_header đọc
    hdr_subtitle = ''

    @property
    def hdr_title(self):
        return self.switcher

    @property
    def hdr_start(self):
        c = self.active_child
        return getattr(c, 'hdr_start', ()) if c is not None else ()

    @property
    def hdr_end(self):
        c = self.active_child
        return getattr(c, 'hdr_end', ()) if c is not None else ()

    @property
    def active_child(self):
        return self.children.get(self.stack.get_visible_child_name())

    # ---- trang con
    def _ensure(self, tid):
        if tid in self.children:
            return self.children[tid]
        title, spec = next((t[1], t[3]) for t in self.tools if t[0] == tid)
        mod, cls = spec.split(':')
        try:
            page = getattr(importlib.import_module(mod), cls)(self.window)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            page = Adw.StatusPage(icon_name='dialog-error-symbolic', title=f'Không mở được {title}', description=str(e))
        body = page
        sub = getattr(page, 'hdr_title', None)
        if sub is not None:
            # trang con tự có bộ chuyển phụ (tab/toggle): thanh tiêu đề đã dành cho tab của mục gộp, nên đặt bộ phụ ở đầu nội dung
            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            row = Gtk.Box(halign=Gtk.Align.CENTER, margin_top=8, margin_bottom=2)
            row.append(sub)
            body.append(row)
            page.set_vexpand(True)
            body.append(page)
        self.children[tid] = page
        self.window.pages[tid] = page
        self.bins[tid].set_child(body)
        return page

    def show_tab(self, tid):
        """Chuyển sang tab `tid` (dựng nếu chưa có) và trả về trang con."""
        page = self._ensure(tid)
        self.stack.set_visible_child_name(tid)
        return page

    def _changed(self):
        name = self.stack.get_visible_child_name()
        if name:
            self._ensure(name)
        if self.window.pages.get(self.window.current) is self:
            self.window.apply_header(self)
