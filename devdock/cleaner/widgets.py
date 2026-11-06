"""Widget tự vẽ: vòng tròn dung lượng và treemap."""
import math

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Adw, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from .layout import squarify  # noqa: E402
from .scanner import GB, MB, fmt_size  # noqa: E402

TAU = 2 * math.pi
PALETTE = [(0.06, 0.46, 0.43), (0.43, 0.16, 0.85), (0.01, 0.41, 0.63), (0.71, 0.33, 0.04), (0.75, 0.09, 0.36),
           (0.30, 0.49, 0.05), (0.73, 0.11, 0.11), (0.26, 0.22, 0.79), (0.05, 0.45, 0.55), (0.63, 0.38, 0.03)]


class Ring(Gtk.Overlay):
    """Vòng tròn: phần đã dùng, phần sẽ giải phóng (xanh), vòng quét xoay, số đếm ở giữa."""

    def __init__(self):
        super().__init__()
        self.area = Gtk.DrawingArea(content_width=250, content_height=250, halign=Gtk.Align.CENTER)
        self.area.set_draw_func(self._draw)
        self.set_child(self.area)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.lbl_label = Gtk.Label(css_classes=['caption-heading', 'dim-label'])
        self.lbl_num = Gtk.Label(css_classes=['ring-num'])
        self.lbl_sub = Gtk.Label(css_classes=['caption', 'dim-label'])
        for w in (self.lbl_label, self.lbl_num, self.lbl_sub):
            box.append(w)
        self.add_overlay(box)
        self.state = 'idle'
        self.cur = {'used': 0.0, 'clean': 0.0, 'num': 0.0}
        self.tgt = {'used': 0.0, 'clean': 0.0, 'num': 0.0}
        self.unit = '%'
        self.sweep = 0.0
        self._tick_id = 0
        self._last = 0

    def set_state(self, state, used, clean, label, num, unit, sub):
        self.state = state
        self.tgt.update(used=used, clean=clean, num=num)
        if unit != self.unit:
            self.cur['num'] = 0.0
            self.unit = unit
        self.lbl_label.set_text(label)
        self.lbl_sub.set_text(sub)
        accent = state in ('ready', 'done')
        (self.lbl_num.add_css_class if accent else self.lbl_num.remove_css_class)('accent')
        self._wake()

    def _wake(self):
        if not self._tick_id:
            self._last = 0
            self._tick_id = self.add_tick_callback(self._tick)

    def _tick(self, _w, clock):
        now = clock.get_frame_time()
        dt = (now - self._last) / 1e6 if self._last else 0.016
        self._last = now
        k = 1 - math.exp(-dt * 7)
        moving = False
        for key in ('used', 'clean', 'num'):
            diff = self.tgt[key] - self.cur[key]
            if abs(diff) > (0.0005 if key != 'num' else 0.05):
                self.cur[key] += diff * k
                moving = True
            else:
                self.cur[key] = self.tgt[key]
        scanning = self.state in ('scanning', 'cleaning')
        if scanning:
            self.sweep = (self.sweep + dt * 5.5) % TAU
            self.lbl_num.set_markup('···')
        else:
            n = self.cur['num']
            txt = f'{n:.1f}' if self.unit == 'GB' else f'{round(n)}'
            self.lbl_num.set_markup(f'{txt}<span size="x-small" weight="normal" alpha="65%"> {self.unit}</span>')
        self.area.queue_draw()
        if moving or scanning:
            return GLib.SOURCE_CONTINUE
        self._tick_id = 0
        return GLib.SOURCE_REMOVE

    def _draw(self, _area, cr, w, h):
        cx, cy = w / 2, h / 2
        r = min(w, h) / 2 - 16
        lw = 16
        dark = Adw.StyleManager.get_default().get_dark()
        cr.set_line_width(lw)
        cr.set_source_rgba(*((1, 1, 1, .08) if dark else (0, 0, 0, .08)))
        cr.arc(cx, cy, r, 0, TAU)
        cr.stroke()
        fade = .35 if self.state in ('scanning', 'cleaning') else 1
        start = -math.pi / 2
        used_end = start + TAU * min(self.cur['used'], 1)
        if used_end > start:
            cr.set_source_rgba(*((.42, .46, .55, fade) if dark else (.55, .6, .68, fade)))
            cr.arc(cx, cy, r, start, used_end)
            cr.stroke()
        clean = min(self.cur['clean'], 1)
        if clean > 0.0005:
            cr.set_line_width(lw + 10)
            cr.set_source_rgba(.2, .83, .6, .22 * fade)
            cr.arc(cx, cy, r, used_end, used_end + TAU * clean)
            cr.stroke()
            cr.set_line_width(lw)
            cr.set_source_rgba(.2, .83, .6, fade)
            cr.arc(cx, cy, r, used_end, used_end + TAU * clean)
            cr.stroke()
        if self.state in ('scanning', 'cleaning'):
            cr.set_line_cap(1)  # round
            cr.set_source_rgba(.13, .83, .93, 1)
            a = start + self.sweep
            cr.arc(cx, cy, r, a, a + 1.1)
            cr.stroke()


class Treemap(Gtk.DrawingArea):
    """Treemap thư mục. on_open(name) khi bấm trái vào thư mục, on_reveal(name) khi bấm phải."""

    def __init__(self, on_open, on_reveal):
        super().__init__(hexpand=True, vexpand=True)
        self.set_size_request(300, 260)
        self.on_open, self.on_reveal = on_open, on_reveal
        self.entries, self.total = [], 0
        self.rects = []
        self.hover = -1
        self.set_draw_func(self._draw)
        click = Gtk.GestureClick(button=0)
        click.connect('pressed', self._pressed)
        self.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect('motion', self._motion)
        motion.connect('leave', lambda *_: self._set_hover(-1))
        self.add_controller(motion)
        self.add_css_class('card')

    def set_data(self, entries, total):
        self.entries = [e for e in entries if e['size'] > 0]
        self.total = total or sum(e['size'] for e in self.entries)
        self.hover = -1
        self.queue_draw()

    def _hit(self, x, y):
        for i, rx, ry, rw, rh in self.rects:
            if rx <= x < rx + rw and ry <= y < ry + rh:
                return i
        return -1

    def _set_hover(self, i):
        if i == self.hover:
            return
        self.hover = i
        if i >= 0:
            e = self.entries[i]
            pct = e['size'] / self.total * 100 if self.total else 0
            self.set_tooltip_text(f"{e['name']} · {fmt_size(e['size'])} ({pct:.1f}%)")
            self.set_cursor_from_name('pointer' if e['dir'] else None)
        else:
            self.set_tooltip_text(None)
            self.set_cursor_from_name(None)
        self.queue_draw()

    def _motion(self, _c, x, y):
        self._set_hover(self._hit(x, y))

    def _pressed(self, gesture, _n, x, y):
        i = self._hit(x, y)
        if i < 0:
            return
        e = self.entries[i]
        if gesture.get_current_button() == 3:
            self.on_reveal(e['name'])
        elif gesture.get_current_button() == 1 and e['dir']:
            self.on_open(e['name'])

    def _draw(self, _a, cr, w, h):
        self.rects = squarify([e['size'] for e in self.entries], 0, 0, w, h)
        for i, x, y, rw, rh in self.rects:
            e = self.entries[i]
            r, g, b = PALETTE[i % len(PALETTE)] if e['dir'] else (.28, .33, .41)
            boost = .12 if i == self.hover else 0
            x0, y0, x1, y1 = x + 1.5, y + 1.5, x + rw - 1.5, y + rh - 1.5
            if x1 - x0 < 1 or y1 - y0 < 1:
                continue
            rad = min(6, (x1 - x0) / 2, (y1 - y0) / 2)
            cr.new_sub_path()
            cr.arc(x1 - rad, y0 + rad, rad, -math.pi / 2, 0)
            cr.arc(x1 - rad, y1 - rad, rad, 0, math.pi / 2)
            cr.arc(x0 + rad, y1 - rad, rad, math.pi / 2, math.pi)
            cr.arc(x0 + rad, y0 + rad, rad, math.pi, 1.5 * math.pi)
            cr.close_path()
            cr.set_source_rgb(min(r + boost, 1), min(g + boost, 1), min(b + boost, 1))
            cr.fill()
            if rw > 58 and rh > 36:
                layout = self.create_pango_layout('')
                layout.set_markup(f"<b>{GLib.markup_escape_text(e['name'])}</b>\n{fmt_size(e['size'])}", -1)
                layout.set_width(int((rw - 16) * Pango.SCALE))
                layout.set_ellipsize(Pango.EllipsizeMode.END)
                layout.set_height(-1)
                cr.set_source_rgba(1, 1, 1, .95)
                cr.move_to(x + 8, y + 6)
                PangoCairo.show_layout(cr, layout)
