"""Kiểm tra hạ tầng chung: sidebar có đủ module, chia nhóm; thông báo desktop không lỗi."""
from uihelp import check, pump


def infra_tests(win):
    from devdock.app import MODULES
    rows = win.nav_rows()
    check('sidebar has all modules', [r.mid for r in rows] == [m[0] for m in MODULES], str([r.mid for r in rows]))
    check('sidebar groups present', {r.group for r in rows} == {'Project', 'Chạy & dữ liệu', 'Hệ thống', 'AI'})
    win.notify('Thử nghiệm', 'nội dung', 'devdock-test')
    check('notify does not raise', True)
    win.get_application().withdraw_notification('devdock-test')


TESTS = [infra_tests]
