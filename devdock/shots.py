"""Chế độ kiểm thử: DEVDOCK_SHOTS=<thư mục> chụp ảnh từng trang rồi thoát. Plan: DEVDOCK_PLAN="module[:tab],..."."""
import os
import sys

from gi.repository import GLib, Gtk


def snapshot(win, path):
    p = Gtk.WidgetPaintable.new(win)
    s = Gtk.Snapshot()
    p.snapshot(s, win.get_width(), win.get_height())
    node = s.to_node()
    if node is None:
        print(f'[shots] không có nội dung để chụp: {path} size={win.get_width()}x{win.get_height()} mapped={win.get_mapped()} visible={win.is_visible()} active={win.is_active()}', file=sys.stderr)
        return False
    win.get_native().get_renderer().render_texture(node, None).save_to_png(path)
    return True


def run(app, win, outdir):
    os.makedirs(outdir, exist_ok=True)
    plan = [x for x in os.environ.get('DEVDOCK_PLAN', 'projects,services,env,logs,sysmon,docker,cleaner').split(',') if x]
    wait = int(os.environ.get('DEVDOCK_WAIT', '2500'))
    steps = iter(plan)
    GLib.timeout_add_seconds(max(30, len(plan) * (wait // 1000 + 3)), lambda: app.quit() or False)  # chốt chặn: luôn thoát

    def nxt():
        try:
            item = next(steps)
        except StopIteration:
            app.quit()
            return False
        mod, _, tool = item.partition(':')
        win.select_module(mod)
        if tool:
            win.pages[mod].show_tool(tool)

        tries = [0]

        def shot():
            try:
                ok = snapshot(win, os.path.join(outdir, item.replace(':', '_') + '.png'))
            except Exception as e:  # noqa: BLE001
                print(f'[shots] lỗi: {e}', file=sys.stderr)
                ok = True
            if not ok and tries[0] < 5:  # cây widget đang layout lại (vd. bảng vừa làm mới): thử lại sau chút
                tries[0] += 1
                GLib.timeout_add(300, shot)
                return False
            nxt()
            return False
        GLib.timeout_add(wait, shot)
        return False
    GLib.timeout_add(600, nxt)
