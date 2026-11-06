"""Tiện ích giao diện dùng chung: luồng nền, định dạng, widget biểu đồ, bảng dữ liệu, CSS."""
import threading
import traceback

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, GObject, Gdk, Gio, Gtk  # noqa: E402

esc = GLib.markup_escape_text

CSS = """
.mono { font-family: monospace; }
.big-num { font-size: 30px; font-weight: 800; font-feature-settings: "tnum"; }
.tnum { font-feature-settings: "tnum"; }
.badge { font-size: 10px; font-weight: 700; padding: 1px 8px; border-radius: 99px; }
.badge.safe, .badge.ok { background: alpha(#34d399, .18); color: #0f9d74; }
.badge.review, .badge.warn { background: alpha(#fbbf24, .22); color: #b7791f; }
.badge.bad { background: alpha(#f87171, .2); color: #dc2626; }
.badge.info { background: alpha(@accent_bg_color, .18); color: @accent_color; }
.size { font-weight: 700; font-feature-settings: "tnum"; }
.big { padding: 10px 18px; font-weight: 700; }
.ring-num { font-size: 50px; font-weight: 800; font-feature-settings: "tnum"; }
.ring-num.accent { color: #34d399; }
progressbar.thin trough, progressbar.thin progress { min-height: 4px; }
progressbar.warn progress { background: #fbbf24; }
progressbar.bad progress { background: #f87171; }
progressbar.g-project progress { background: #2dd4bf; }
progressbar.g-cache progress { background: #a78bfa; }
progressbar.g-docker progress { background: #38bdf8; }
progressbar.g-dupe progress { background: #f472b6; }
progressbar.g-large progress { background: #fb7185; }
progressbar.g-system progress { background: #fbbf24; }
.sidebar-hero { background: alpha(@accent_bg_color, .06); }
.codebox { border-radius: 8px; border: 1px solid alpha(currentColor, .15); }
.codebox textview, .codebox text { background: transparent; }
textview.mono { font-family: monospace; font-size: 11pt; }
.metric { padding: 14px; }
"""


def ui(fn, *args):
    """Chạy hàm ở luồng giao diện (gọi từ luồng nền)."""
    GLib.idle_add(lambda: fn(*args) and False)


def bg(fn, done=None):
    """Chạy fn ở luồng nền; done(result, error) được gọi ở luồng giao diện."""
    def work():
        try:
            res, err = fn(), None
        except Exception as e:  # noqa: BLE001
            if not isinstance(e, (RuntimeError, ValueError, OSError)):
                traceback.print_exc()
            res, err = None, str(e) or e.__class__.__name__
        if done:
            ui(done, res, err)
    threading.Thread(target=work, daemon=True).start()


def human(b):
    """Dung lượng dạng 1.5 GB."""
    try:
        b = float(b)
    except (TypeError, ValueError):
        return '—'
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(b) < 1024 or u == 'TB':
            return f'{b:.0f} {u}' if u == 'B' else f'{b:.1f} {u}'
        b /= 1024


def rate(b):
    return human(b) + '/s'


def duration(sec):
    d, h, m = int(sec // 86400), int(sec % 86400 // 3600), int(sec % 3600 // 60)
    return ' '.join(x for x in (f'{d}d' if d else '', f'{h}h' if d or h else '', f'{m}m') if x)


def clear(box):
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def copy_text(widget, text):
    widget.get_clipboard().set(text)


def label(text='', css=None, xalign=0.0, **kw):
    lb = Gtk.Label(label=text, xalign=xalign, **kw)
    for c in css or []:
        lb.add_css_class(c)
    return lb


def vbox(spacing=8, **kw):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, **kw)


def hbox(spacing=8, **kw):
    return Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing, **kw)


def button(text=None, icon=None, css=None, on=None, tip=None):
    b = Gtk.Button(label=text) if text else Gtk.Button(icon_name=icon)
    if text and icon:
        b = Gtk.Button(child=hbox(6))
        b.get_child().append(Gtk.Image(icon_name=icon))
        b.get_child().append(Gtk.Label(label=text))
    for c in css or []:
        b.add_css_class(c)
    if on:
        b.connect('clicked', lambda *_: on())
    if tip:
        b.set_tooltip_text(tip)
    return b


def copy_button(get_text, tip='Sao chép'):
    b = Gtk.Button(icon_name='edit-copy-symbolic', css_classes=['flat'], tooltip_text=tip, valign=Gtk.Align.CENTER)

    def clicked(btn):
        copy_text(btn, get_text() if callable(get_text) else get_text)
        btn.set_icon_name('object-select-symbolic')
        GLib.timeout_add(1200, lambda: btn.set_icon_name('edit-copy-symbolic') or False)
    b.connect('clicked', clicked)
    return b


def text_box(editable=True, min_height=120, placeholder=None, wrap=True):
    """TextView đơn sắc trong khung cuộn. Trả (widget, buffer, textview)."""
    tv = Gtk.TextView(editable=editable, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR if wrap else Gtk.WrapMode.NONE,
                      left_margin=8, right_margin=8, top_margin=8, bottom_margin=8, cursor_visible=editable)
    tv.add_css_class('mono')
    sw = Gtk.ScrolledWindow(child=tv, min_content_height=min_height, vexpand=True, hexpand=True)
    sw.add_css_class('codebox')
    return sw, tv.get_buffer(), tv


def set_text(buf, text):
    buf.set_text(text, -1)


def get_text(buf):
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


def titled(title, child, actions=None):
    """Khối có tiêu đề nhỏ phía trên (và nút hành động bên phải)."""
    box = vbox(4)
    head = hbox(4, height_request=30)
    head.append(label(title, ['dim-label', 'caption'], hexpand=True, valign=Gtk.Align.CENTER))
    for a in actions or []:
        head.append(a)
    box.append(head)
    box.append(child)
    return box


def error_label():
    return Gtk.Label(xalign=0, wrap=True, selectable=True, css_classes=['error'], visible=False)


def show_error(lbl, msg):
    lbl.set_visible(bool(msg))
    lbl.set_text(msg or '')


def debounce(fn, ms=150):
    state = {'id': 0}

    def call(*_):
        if state['id']:
            GLib.source_remove(state['id'])

        def fire():
            state['id'] = 0
            fn()
            return False
        state['id'] = GLib.timeout_add(ms, fire)
    return call


# ======================================================================================
class Sparkline(Gtk.DrawingArea):
    """Biểu đồ đường nhỏ có tô nền."""

    def __init__(self, color=(0.35, 0.5, 1.0), height=56, maximum=None):
        super().__init__(hexpand=True, content_height=height)
        self.color, self.max, self.data = color, maximum, []
        self.set_draw_func(self._draw)

    def set_data(self, data):
        self.data = list(data)
        self.queue_draw()

    def _draw(self, _a, cr, w, h):
        d = self.data
        if len(d) < 2:
            return
        m = self.max or max(max(d), 1e-9)
        pts = [(i / (len(d) - 1) * w, h - 2 - min(v, m) / m * (h - 4)) for i, v in enumerate(d)]
        r, g, b = self.color
        cr.move_to(0, h)
        for x, y in pts:
            cr.line_to(x, y)
        cr.line_to(w, h)
        cr.close_path()
        cr.set_source_rgba(r, g, b, .18)
        cr.fill()
        cr.set_line_width(1.6)
        cr.set_source_rgba(r, g, b, 1)
        cr.move_to(*pts[0])
        for x, y in pts[1:]:
            cr.line_to(x, y)
        cr.stroke()


def bar(fraction, css=None):
    pb = Gtk.ProgressBar(fraction=max(0, min(1, fraction)))
    pb.add_css_class('thin')
    pb.add_css_class(css or ('bad' if fraction > .85 else 'warn' if fraction > .65 else 'x'))
    return pb


# ======================================================================================
class Row(GObject.Object):
    def __init__(self, d):
        super().__init__()
        self.d = d


class DataTable(Gtk.Box):
    """Bảng dữ liệu (Gtk.ColumnView): cột có thể sắp xếp, lọc theo chuỗi, giữ lại dòng đang chọn khi làm mới."""

    def __init__(self, columns, key, search_text=None, on_select=None):
        """columns: [{'title','get'(row dict -> str), 'sort'(row dict -> khoá), 'width','expand','align','css','markup'}]"""
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.key, self.search_text, self.query = key, search_text, ''
        self.store = Gio.ListStore(item_type=Row)
        self.filter = Gtk.CustomFilter.new(self._match)
        self.view = Gtk.ColumnView(show_row_separators=True, show_column_separators=False, reorderable=False)
        self.view.add_css_class('data-table')
        for spec in columns:
            self.view.append_column(self._column(spec))
        self.sorted = Gtk.SortListModel(model=Gtk.FilterListModel(model=self.store, filter=self.filter), sorter=self.view.get_sorter())
        self.sel = Gtk.SingleSelection(model=self.sorted, autoselect=False, can_unselect=True)
        self.view.set_model(self.sel)
        if on_select:
            self.sel.connect('notify::selected-item', lambda *_: on_select(self.selected()))
        self.scroller = Gtk.ScrolledWindow(child=self.view, vexpand=True, hexpand=True)
        self.scroller.add_css_class('codebox')
        self.append(self.scroller)
        self._default_sorted = False

    def _column(self, spec):
        f = Gtk.SignalListItemFactory()
        align = spec.get('align', 0.0)
        markup = spec.get('markup', False)

        def setup(_f, li):
            lb = Gtk.Label(xalign=align, ellipsize=3, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4, use_markup=markup)
            for c in spec.get('css', []):
                lb.add_css_class(c)
            li.set_child(lb)

        def bind(_f, li):
            d = li.get_item().d
            lb = li.get_child()
            lb.set_markup(spec['get'](d)) if markup else lb.set_text(spec['get'](d))
            tip = spec.get('tip')
            lb.set_tooltip_text(tip(d) if tip else None)
        f.connect('setup', setup)
        f.connect('bind', bind)
        col = Gtk.ColumnViewColumn(title=spec['title'], factory=f, resizable=True, expand=spec.get('expand', False))
        if spec.get('width'):
            col.set_fixed_width(spec['width'])
        if spec.get('sort'):
            sk = spec['sort']
            col.set_sorter(Gtk.CustomSorter.new(lambda a, b, _u: (sk(a.d) > sk(b.d)) - (sk(a.d) < sk(b.d)), None))
        return col

    def _match(self, row, *_):
        q = self.query
        return not q or not self.search_text or q in self.search_text(row.d).lower()

    def set_query(self, q):
        self.query = (q or '').strip().lower()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    def sort_by(self, index, descending=True):
        col = self.view.get_columns().get_item(index)
        self.view.sort_by_column(col, Gtk.SortType.DESCENDING if descending else Gtk.SortType.ASCENDING)

    def selected(self):
        it = self.sel.get_selected_item()
        return it.d if it else None

    def set_rows(self, rows):
        keep = self.selected()
        keep_key = self.key(keep) if keep else None
        adj = self.scroller.get_vadjustment()
        pos = adj.get_value()
        self.store.splice(0, self.store.get_n_items(), [Row(r) for r in rows])
        if keep_key is not None:
            for i in range(self.sorted.get_n_items()):
                if self.key(self.sorted.get_item(i).d) == keep_key:
                    self.sel.set_selected(i)
                    break
        GLib.idle_add(lambda: adj.set_value(min(pos, max(0, adj.get_upper() - adj.get_page_size()))) or False)


class Page(Adw.Bin):
    """Trang của một module. Tự bật/tắt tác vụ định kỳ theo việc có đang hiển thị hay không."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self._timers = []
        self.connect('map', lambda *_: self.on_show())
        self.connect('unmap', lambda *_: self.on_hide())

    def toast(self, msg):
        self.window.toast(msg)

    def every(self, seconds, fn):
        """Gọi fn ngay và lặp lại mỗi `seconds` khi trang đang hiện."""
        self._timers.append([seconds, fn, 0])
        return self

    def on_show(self):
        for t in self._timers:
            t[1]()
            t[2] = GLib.timeout_add(int(t[0] * 1000), lambda f=t[1]: f() or True)

    def on_hide(self):
        for t in self._timers:
            if t[2]:
                GLib.source_remove(t[2])
                t[2] = 0
