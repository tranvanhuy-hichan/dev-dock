"""Cửa sổ chính (libadwaita): thanh bên chọn module + nội dung, palette lệnh Ctrl+K, đổi giao diện sáng/tối."""
import importlib
import os
import sys
import traceback

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, __version__  # noqa: E402
from .store import Store  # noqa: E402
from .ui import CSS, label  # noqa: E402

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'icons')

# (id, tên, icon, lớp trang)
MODULES = [
    ('projects', 'Projects', 'folder-symbolic', 'devdock.pages.projects:ProjectsPage'),
    ('services', 'Dev Services', 'media-playback-start-symbolic', 'devdock.pages.services:ServicesPage'),
    ('env', 'Env Manager', 'dialog-password-symbolic', 'devdock.pages.envs:EnvPage'),
    ('logs', 'Log Viewer', 'utilities-terminal-symbolic', 'devdock.pages.logs:LogsPage'),
    ('sysmon', 'SysMonitor', 'utilities-system-monitor-symbolic', 'devdock.pages.sysmon:SysMonitorPage'),
    ('docker', 'DockerDesk', 'package-x-generic-symbolic', 'devdock.pages.docker:DockerPage'),
    ('cleaner', 'DiskCleaner', 'edit-clear-all-symbolic', 'devdock.pages.cleaner:CleanerPage'),
]


class Palette(Adw.Dialog):
    """Hộp lệnh nhanh: gõ để lọc, Enter để chạy."""

    def __init__(self, items):
        super().__init__(title='Lệnh nhanh', content_width=480, content_height=420)
        self.items, self.rows = items, []
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.entry = Gtk.SearchEntry(placeholder_text='Gõ lệnh hoặc tên công cụ…', margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        self.entry.connect('search-changed', lambda *_: self.list.invalidate_filter())
        self.entry.connect('activate', self._activate_first)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE, css_classes=['navigation-sidebar'])
        self.list.set_filter_func(self._filter)
        self.list.connect('row-activated', lambda _l, r: self._run(r.item))
        for it in items:
            row = Gtk.ListBoxRow()
            row.item = it
            line = Gtk.Box(spacing=8, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
            line.append(label(it['label'], hexpand=True))
            if it.get('hint'):
                line.append(label(it['hint'], ['dim-label', 'caption']))
            row.set_child(line)
            self.list.append(row)
        sw = Gtk.ScrolledWindow(child=self.list, vexpand=True)
        box.append(self.entry)
        box.append(sw)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        tv.set_content(box)
        self.set_child(tv)
        self.set_focus(self.entry)

    def _filter(self, row):
        q = self.entry.get_text().strip().lower()
        return not q or q in row.item['label'].lower()

    def _activate_first(self, *_):
        for it in self.list_rows():
            if self._filter(it):
                self._run(it.item)
                return

    def list_rows(self):
        r = self.list.get_first_child()
        while r:
            yield r
            r = r.get_next_sibling()

    def _run(self, item):
        self.close()
        GLib.idle_add(lambda: item['run']() and False)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, store):
        super().__init__(application=app, title='DevDock', default_width=1280, default_height=820)
        self.set_size_request(760, 520)
        self.store = store
        self.pages = {}
        from .lib.services import ServiceManager
        self.services = ServiceManager()  # dùng chung cho Dev Services và Log Viewer
        self.current = None
        self.toasts = Adw.ToastOverlay()

        split = Adw.NavigationSplitView(min_sidebar_width=200, max_sidebar_width=240, sidebar_width_fraction=0.2)
        # --- sidebar
        self.nav = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['navigation-sidebar'])
        for mid, name, icon, _cls in MODULES:
            row = Gtk.ListBoxRow()
            row.mid = mid
            line = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=6, margin_end=6)
            line.append(Gtk.Image(icon_name=icon))
            line.append(label(name, hexpand=True))
            line.append(label(f'Ctrl+{len(self.nav_rows()) + 1}', ['dim-label', 'caption']))
            row.set_child(line)
            self.nav.append(row)
        self.nav.connect('row-selected', lambda _l, r: r and self.show_module(r.mid))
        side_tv = Adw.ToolbarView()
        side_head = Adw.HeaderBar(show_end_title_buttons=False)
        side_head.set_title_widget(Adw.WindowTitle(title='DevDock', subtitle='Bộ công cụ cho developer'))
        side_tv.add_top_bar(side_head)
        side_tv.set_content(Gtk.ScrolledWindow(child=self.nav, vexpand=True))
        pal = Gtk.Button(label='Lệnh nhanh  (Ctrl+K)', css_classes=['flat'], margin_start=8, margin_end=8, margin_bottom=8, margin_top=4)
        pal.connect('clicked', lambda *_: self.open_palette())
        side_tv.add_bottom_bar(pal)
        split.set_sidebar(Adw.NavigationPage(title='DevDock', child=side_tv))
        # --- content
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120)
        self.content_title = Adw.WindowTitle(title='')
        head = Adw.HeaderBar()
        head.set_title_widget(self.content_title)
        menu = Gio.Menu()
        sec = Gio.Menu()
        sec.append('Giao diện: theo hệ thống', 'win.theme::system')
        sec.append('Giao diện: sáng', 'win.theme::light')
        sec.append('Giao diện: tối', 'win.theme::dark')
        menu.append_section(None, sec)
        menu.append('Lệnh nhanh', 'win.palette')
        menu.append('Giới thiệu DevDock', 'win.about')
        head.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu, primary=True, tooltip_text='Menu'))
        tv = Adw.ToolbarView()
        tv.add_top_bar(head)
        tv.set_content(self.stack)
        split.set_content(Adw.NavigationPage(title='Nội dung', child=tv))
        self.toasts.set_child(split)
        self.set_content(self.toasts)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 900sp'))
        bp.add_setter(split, 'collapsed', False)
        for name, cb, accel in (('palette', lambda *_: self.open_palette(), '<primary>k'), ('about', lambda *_: self.about(), None)):
            a = Gio.SimpleAction.new(name, None)
            a.connect('activate', cb)
            self.add_action(a)
            if accel:
                app.set_accels_for_action(f'win.{name}', [accel])
        theme = Gio.SimpleAction.new_stateful('theme', GLib.VariantType.new('s'), GLib.Variant('s', store['theme']))
        theme.connect('activate', lambda act, v: (act.set_state(v), self.set_theme(v.get_string(), save=True)))
        self.add_action(theme)
        for i, (mid, *_r) in enumerate(MODULES, 1):
            a = Gio.SimpleAction.new(f'go-{mid}', None)
            a.connect('activate', lambda *_a, m=mid: self.select_module(m))
            self.add_action(a)
            app.set_accels_for_action(f'win.go-{mid}', [f'<primary>{i}'])
        self.set_theme(store['theme'])
        self.connect('close-request', self._on_close)
        self.select_module(store['module'] if store['module'] in [m[0] for m in MODULES] else 'projects')

    def _on_close(self, *_):
        for p in self.pages.values():
            if hasattr(p, 'shutdown'):
                p.shutdown()
        self.services.shutdown()  # dừng các dev service đang chạy để không để lại tiến trình mồ côi
        return False

    def nav_rows(self):
        out, r = [], self.nav.get_first_child()
        while r:
            out.append(r)
            r = r.get_next_sibling()
        return out

    # ---- điều hướng
    def select_module(self, mid):
        for r in self.nav_rows():
            if r.mid == mid:
                self.nav.select_row(r)
                return

    def show_module(self, mid):
        if mid not in self.pages:
            spec = next(m for m in MODULES if m[0] == mid)
            mod, cls = spec[3].split(':')
            try:
                page = getattr(importlib.import_module(mod), cls)(self)
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                page = Adw.StatusPage(icon_name='dialog-error-symbolic', title=f'Không mở được {spec[1]}', description=str(e))
            self.pages[mid] = page
            self.stack.add_named(page, mid)
        self.stack.set_visible_child_name(mid)
        self.current = mid
        self.content_title.set_title(next(m[1] for m in MODULES if m[0] == mid))
        if self.store['module'] != mid:
            self.store['module'] = mid
            self.store.save()

    def open_logs(self, source):
        """Mở Log Viewer với một nguồn log, vd. ('docker', id, name)."""
        self.select_module('logs')
        self.pages['logs'].open_source(source)

    def toast(self, msg):
        self.toasts.add_toast(Adw.Toast(title=msg, timeout=4))

    def set_theme(self, name, save=False):
        Adw.StyleManager.get_default().set_color_scheme({'light': Adw.ColorScheme.FORCE_LIGHT, 'dark': Adw.ColorScheme.FORCE_DARK}.get(name, Adw.ColorScheme.DEFAULT))
        if save:
            self.store['theme'] = name
            self.store.save()

    def open_palette(self):
        items = [{'label': f'Mở {m[1]}', 'hint': f'Ctrl+{i}', 'run': lambda mid=m[0]: self.select_module(mid)} for i, m in enumerate(MODULES, 1)]
        items += [{'label': 'Giao diện sáng', 'run': lambda: self.set_theme('light', True)}, {'label': 'Giao diện tối', 'run': lambda: self.set_theme('dark', True)},
                  {'label': 'Giao diện theo hệ thống', 'run': lambda: self.set_theme('system', True)}]
        Palette(items).present(self)

    def about(self):
        Adw.AboutDialog(application_name='DevDock', application_icon=APP_ID, version=__version__, developer_name='DevDock',
                        comments='Trung tâm cho developer: quản lý project, dev services, file .env, log, giám sát hệ thống, Docker và dọn đĩa. GTK4/libadwaita.',
                        license_type=Gtk.License.MIT_X11, website='https://github.com/tranvanhuy-hichan/dev-dock').present(self)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.win = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.store = Store()
        prov = Gtk.CssProvider()
        prov.load_from_string(CSS)
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.IconTheme.get_for_display(display).add_search_path(ICON_DIR)
        Gtk.Window.set_default_icon_name(APP_ID)
        self.set_accels_for_action('app.quit', ['<primary>q'])
        q = Gio.SimpleAction.new('quit', None)
        q.connect('activate', lambda *_: self.quit())
        self.add_action(q)

    def do_activate(self):
        if not self.win:
            self.win = MainWindow(self, self.store)
        self.win.present()
        shots = os.environ.get('DEVDOCK_SHOTS')  # chế độ kiểm thử giao diện: chụp từng trang rồi thoát
        if shots:
            from .shots import run
            run(self, self.win, shots)


def main():
    return App().run(sys.argv)
