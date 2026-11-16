"""Test mục gộp ở sidebar: 7 mục, 14 trang con mở được bằng id cũ, tab ở thanh tiêu đề, nút header theo trang con."""
from uihelp import check, pump, wait_for


def hubs_tests(win):
    from gi.repository import Adw, Gtk
    from devdock.app import MODULES, TOOL_HUB, TOOLS
    rows = win.nav_rows()
    check('sidebar has 7 entries', len(rows) == 7 and [r.mid for r in rows] == ['dashboard', 'project', 'run', 'docker', 'data', 'system', 'ollama'], str([r.mid for r in rows]))
    check('every old module id still resolves', len(TOOL_HUB) == 15 and set(TOOL_HUB.values()) == {'project', 'run', 'data', 'system'})
    # mọi trang con mở được bằng id cũ, được đăng ký trong window.pages và hiện ra
    for hub, tools in TOOLS.items():
        for tid, title, _icon, _spec in tools:
            win.select_module(tid)
            pump(80)
            pg = win.pages.get(tid)
            check(f'open {tid} via old id', pg is not None and not isinstance(pg, Adw.StatusPage) and win.current == hub and win.pages[hub].active_child is pg, f'{win.current}')
            check(f'{tid} page is mapped and active', pg.get_mapped() and win.is_active(pg))
    # thanh tiêu đề: bộ chuyển tab của mục gộp, nút của trang con thay đổi theo tab
    win.select_module('project')
    hub = win.pages['project']
    check('header title is the hub tab switcher', win.head.get_title_widget() is hub.switcher and isinstance(hub.switcher, Adw.ViewSwitcher))
    tabs = [hub.stack.get_pages().get_item(i).get_name() for i in range(hub.stack.get_pages().get_n_items())]
    check('project hub tabs', tabs == ['projects', 'git', 'deps', 'secrets', 'env'], str(tabs))
    win.select_module('git')
    git = win.pages['git']
    check('git header buttons shown', all(w.get_mapped() and w.get_root() is win for w in git.hdr_end) and len(git.hdr_end) == 2)
    win.select_module('secrets')
    check('switching tab swaps header buttons', all(w.get_parent() is None for w in git.hdr_end) and all(w.get_mapped() and w.get_root() is win for w in win.pages['secrets'].hdr_end))
    # trang con có bộ chuyển phụ: nằm ở đầu nội dung chứ không chen vào header
    sub = git.hdr_title
    check('git sub-switcher moved into content', sub is not None and sub.get_parent() is not win.head and sub.get_root() is win)
    win.select_module('sysmon')
    sysm = win.pages['sysmon']
    check('sysmon inner tabs still work', sysm.hdr_title.get_parent() is not win.head and sysm.hdr_title.get_root() is win)
    inner = [sysm.stack.get_pages().get_item(i).get_name() for i in range(sysm.stack.get_pages().get_n_items())]
    check('sysmon keeps only non-duplicate tabs', inner == ['overview', 'disks', 'net'], str(inner))
    # chỉ trang đang hiện mới chạy timer: tab khác không bị chạy ngầm
    win.select_module('ports')
    pg_ports = win.pages['ports']
    pump(200)
    win.select_module('logs')
    pump(200)
    check('hidden tab is unmapped', not pg_ports.get_mapped() and win.pages['logs'].get_mapped())
    # chuyển giữa các mục gộp rồi quay lại giữ được tab đã chọn
    win.select_module('memory')
    win.select_module('dashboard')
    win.select_module('system')
    check('hub remembers its last tab', win.pages['system'].stack.get_visible_child_name() == 'memory')
    # palette liệt kê cả trang con
    from devdock.app import Palette  # noqa: F401
    labels = []
    orig = Palette.__init__
    Palette.__init__ = lambda self, items, *a, **k: (labels.extend(i['label'] for i in items), orig(self, items, *a, **k))[1]
    try:
        win.open_palette()
        pump(100)
        d = win.get_visible_dialog()
        if d:
            d.force_close()
    finally:
        Palette.__init__ = orig
    check('palette lists hubs and their tools', 'Mở Dự án' in labels and 'Mở Git' in labels and 'Mở Port Manager'.replace('Port Manager', 'Cổng mạng') in labels, str(labels[:8]))
    check('Ctrl+1..7 actions exist', all(win.lookup_action(f'go-{m[0]}') is not None for m in MODULES))
    win.select_module('dashboard')


TESTS = [hubs_tests]
