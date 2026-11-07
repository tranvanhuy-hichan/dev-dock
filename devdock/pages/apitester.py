"""API Tester: gửi request HTTP (Params/Headers/Body/Auth, biến {{x}}), bộ sưu tập theo project, nhập OpenAPI, lịch sử, copy curl."""
import copy
import json
import os
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..lib import httpclient as H  # noqa: E402
from ..lib import openapi as OA  # noqa: E402
from ..lib import projects as P  # noqa: E402
from ..ui import (DataTable, InfoStrip, Page, bg, button, content_box, copy_text, esc, get_text, hbox, human, icon_btn, label, linked, set_text, toolbar, vbox)  # noqa: E402

BODY_TYPES = [('none', 'Không có body'), ('json', 'JSON'), ('form', 'Form (urlencoded)'), ('raw', 'Raw')]
AUTH_TYPES = [('none', 'Không xác thực'), ('bearer', 'Bearer token'), ('basic', 'Basic (user/mật khẩu)'), ('apikey', 'API key (header)')]
METHOD_CSS = {'GET': 'info', 'POST': 'ok', 'PUT': 'warn', 'PATCH': 'warn', 'DELETE': 'bad'}
MAX_SPEC = 20 * 1024 * 1024
DISPLAY_CAP = 512 * 1024     # TextView rất chậm với văn bản khổng lồ: chỉ hiển thị phần đầu, phần còn lại lấy bằng "Lưu body…"
WRAP_LINE = 2000            # ngắt dòng cực dài (chỉ khi hiển thị) để GTK không phải dàn trang một dòng hàng triệu ký tự


def status_css(code):
    return 'ok' if code < 300 else 'info' if code < 400 else 'warn' if code < 500 else 'bad'


def method_badge(m):
    return label(m[:7], ['badge', METHOD_CSS.get(m, 'info')], valign=Gtk.Align.CENTER, width_chars=8, xalign=0.5)


def wrap_long_lines(text, width=WRAP_LINE):
    if len(text) <= width or ('\n' in text and max(map(len, text.split('\n'))) <= width):
        return text
    out = []
    for line in text.split('\n'):
        out += [line[i:i + width] for i in range(0, len(line), width)] or ['']
    return '\n'.join(out)


def seconds(s):
    return f'{s * 1000:.0f} ms' if s < 1 else f'{s:.2f} s'


class KVEditor(Gtk.Box):
    """Bảng khoá/giá trị sửa được (bật/tắt từng dòng)."""

    def __init__(self, key_hint='Tên', val_hint='Giá trị', checkable=True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.rows, self.key_hint, self.val_hint, self.checkable = [], key_hint, val_hint, checkable
        add = button('Thêm dòng', icon='list-add-symbolic', css=['flat'], on=lambda: self.add_row())
        add.set_halign(Gtk.Align.START)
        self.append(add)
        self.box = vbox(4)
        self.append(Gtk.ScrolledWindow(child=self.box, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=70))

    def add_row(self, on=True, k='', v=''):
        row = hbox(6)
        chk = Gtk.CheckButton(active=on, valign=Gtk.Align.CENTER, visible=self.checkable)
        ke = Gtk.Entry(placeholder_text=self.key_hint, text=k, hexpand=True, width_chars=14)
        ve = Gtk.Entry(placeholder_text=self.val_hint, text=v, hexpand=True, width_chars=24)
        rm = icon_btn('user-trash-symbolic', 'Xoá dòng', None, ['flat', 'circular'])
        item = {'row': row, 'chk': chk, 'k': ke, 'v': ve}
        rm.connect('clicked', lambda *_: self.remove(item))
        for w in (chk, ke, ve, rm):
            row.append(w)
        self.box.append(row)
        self.rows.append(item)
        return item

    def remove(self, item):
        self.box.remove(item['row'])
        self.rows.remove(item)

    def set(self, rows):
        for it in list(self.rows):
            self.remove(it)
        for r in rows or []:
            self.add_row(r.get('on', True), r.get('k', ''), r.get('v', ''))

    def get(self):
        return [{'on': it['chk'].get_active(), 'k': it['k'].get_text(), 'v': it['v'].get_text()} for it in self.rows if it['k'].get_text().strip() or it['v'].get_text().strip()]


class VarsDialog(Adw.Dialog):
    """Biến môi trường của bộ sưu tập (dùng dạng {{ten}} trong URL, header, body…)."""

    def __init__(self, page):
        super().__init__(title='Biến của bộ sưu tập', content_width=520, content_height=460)
        self.page = page
        tv = Adw.ToolbarView()
        hdr = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label='Huỷ')
        cancel.connect('clicked', lambda *_: self.close())
        ok = Gtk.Button(label='Lưu', css_classes=['suggested-action'])
        ok.connect('clicked', lambda *_: self.save())
        hdr.pack_start(cancel)
        hdr.pack_end(ok)
        tv.add_top_bar(hdr)
        box = vbox(8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box.append(label('Dùng trong request dạng {{base_url}}. Biến chưa đặt sẽ chặn việc gửi.', ['dim-label'], xalign=0, wrap=True))
        self.kv = KVEditor('Tên biến', 'Giá trị', checkable=False)
        self.kv.set([{'k': k, 'v': str(v)} for k, v in page.coll()['vars'].items()])
        box.append(self.kv)
        svc = page.service_bases()
        if svc:
            box.append(label('Đặt base_url từ Dev Service:', ['dim-label', 'caption'], xalign=0))
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, column_spacing=6, row_spacing=6)
            for name, url in svc:
                flow.append(button(f'{name}  ({url})', css=['pill'], on=lambda u=url: self.set_base(u)))
            box.append(flow)
        tv.set_content(box)
        self.set_child(tv)

    def set_base(self, url):
        for it in self.kv.rows:
            if it['k'].get_text() == 'base_url':
                it['v'].set_text(url)
                return
        self.kv.add_row(True, 'base_url', url)

    def save(self):
        vars_ = {r['k'].strip(): r['v'] for r in self.kv.get() if r['k'].strip()}
        self.page.coll()['vars'] = vars_
        self.page.store.save()
        self.page.toast('Đã lưu biến')
        self.close()


class ApiPage(Page):
    def __init__(self, window):
        super().__init__(window)
        self.store = H.ApiStore()
        self.coll_id = self.store.data.get('current') or self.store.collections[0]['id']
        if self.coll_id not in [c['id'] for c in self.store.collections]:
            self.coll_id = self.store.collections[0]['id']
        self.req = H.new_request()
        self.syncing, self.sending, self.cancel, self.last = False, False, None, None
        self.spec, self.eps, self.spec_origin = None, [], None

        self.hdr_start = [icon_btn('list-add-symbolic', 'Request mới', lambda: self.new_request())]
        self.hdr_end = [self._options_button()]

        root = hbox(0)
        root.append(self._left())
        root.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True, wide_handle=False, position=330, shrink_start_child=False, shrink_end_child=False)
        paned.set_start_child(self._request_pane())
        paned.set_end_child(self._response_pane())
        root.append(paned)
        self.set_child(root)
        key = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        key.connect('key-pressed', self.on_key)
        self.add_controller(key)
        self.refresh_collections()
        self.refresh_history()
        self.load_request(self.req)

    # ================= trái =================
    def _left(self):
        left = vbox(6, width_request=300, margin_top=12, margin_bottom=8, margin_start=10, margin_end=10, hexpand=False)
        self.ltabs = Adw.ToggleGroup(halign=Gtk.Align.FILL)
        for n, t in (('saved', 'Đã lưu'), ('openapi', 'OpenAPI'), ('history', 'Lịch sử')):
            self.ltabs.add(Adw.Toggle(name=n, label=t))
        self.ltabs.set_active_name('saved')
        self.ltabs.connect('notify::active-name', lambda g, _p: self.lstack.set_visible_child_name(g.get_active_name()))
        left.append(self.ltabs)
        self.lstack = Gtk.Stack(vexpand=True)
        self.lstack.add_named(self._saved_tab(), 'saved')
        self.lstack.add_named(self._openapi_tab(), 'openapi')
        self.lstack.add_named(self._history_tab(), 'history')
        left.append(self.lstack)
        return left

    def _saved_tab(self):
        box = vbox(6)
        self.coll_dd = Gtk.DropDown.new_from_strings([])
        self.coll_dd.set_hexpand(True)
        self.coll_dd.connect('notify::selected', lambda *_: self.on_coll_change())
        more = Gtk.MenuButton(icon_name='view-more-symbolic', tooltip_text='Bộ sưu tập', valign=Gtk.Align.CENTER)
        pop = Gtk.Popover()
        pb = vbox(2, margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
        for text, fn, css in (('Bộ sưu tập mới…', lambda: self.coll_dialog(False), ['flat']), ('Đổi tên…', lambda: self.coll_dialog(True), ['flat']),
                              ('Biến môi trường…', lambda: VarsDialog(self).present(self.get_root()), ['flat']), ('Xoá bộ sưu tập', self.delete_coll, ['flat', 'destructive-action'])):
            b = button(text, css=css, on=lambda f=fn: (pop.popdown(), f()))
            pb.append(b)
        pop.set_child(pb)
        more.set_popover(pop)
        box.append(toolbar(self.coll_dd, more))
        self.saved_empty = label('Chưa có request nào.\nSoạn một request rồi bấm "Lưu", hoặc nhập từ tab OpenAPI.', ['dim-label'], wrap=True, xalign=0, margin_top=6)
        box.append(self.saved_empty)
        self.saved_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['navigation-sidebar'])
        self.saved_list.connect('row-selected', lambda _l, r: r and not self.syncing and self.open_saved(r.rid))
        box.append(Gtk.ScrolledWindow(child=self.saved_list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        return box

    def _openapi_tab(self):
        box = vbox(6)
        self.spec_url = Gtk.Entry(placeholder_text='http://localhost:8000/openapi.json', hexpand=True)
        self.spec_url.connect('activate', lambda *_: self.load_spec_url())
        box.append(self.spec_url)
        svc = Gtk.MenuButton(valign=Gtk.Align.CENTER, tooltip_text='Lấy URL từ Dev Service đang khai báo')
        svc.set_child(Adw.ButtonContent(label='Dev Service', icon_name='media-playback-start-symbolic'))
        self.svc_pop = Gtk.Popover()
        svc.set_popover(self.svc_pop)
        svc.connect('notify::active', lambda *_: self.fill_svc_menu())
        box.append(toolbar(button('Tải', icon='folder-download-symbolic', css=['suggested-action'], on=self.load_spec_url), button('Mở file…', icon='document-open-symbolic', on=self.open_spec_file), svc))
        self.spec_info = label('Nhập URL tài liệu OpenAPI (vd. FastAPI: /openapi.json) hoặc mở file JSON/YAML.', ['dim-label', 'caption'], wrap=True, xalign=0)
        box.append(self.spec_info)
        self.ep_search = Gtk.SearchEntry(placeholder_text='Lọc endpoint…')
        self.ep_search.connect('search-changed', lambda *_: self.ep_list.invalidate_filter())
        box.append(self.ep_search)
        self.ep_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=['navigation-sidebar'])
        self.ep_list.set_header_func(lambda row, before: row.set_header(label(row.ep['tag'], ['dim-label', 'caption'], margin_top=8, margin_bottom=2, margin_start=8)
                                                                        if before is None or before.ep['tag'] != row.ep['tag'] else None))
        self.ep_list.set_filter_func(self.ep_filter)
        self.ep_list.connect('row-activated', lambda _l, r: self.use_endpoint(r.ep))
        box.append(Gtk.ScrolledWindow(child=self.ep_list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        return box

    def _history_tab(self):
        self.hist_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=['navigation-sidebar'])
        self.hist_list.connect('row-activated', lambda _l, r: self.use_history(r.h))
        box = vbox(6)
        box.append(Gtk.ScrolledWindow(child=self.hist_list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        box.append(label('Lịch sử không lưu token/mật khẩu.', ['dim-label', 'caption'], xalign=0))
        return box

    # ================= request =================
    def _request_pane(self):
        box = content_box(8)
        self.method_dd = Gtk.DropDown.new_from_strings(H.METHODS)
        self.url = Gtk.Entry(placeholder_text='URL, vd. {{base_url}}/api/users', hexpand=True)
        self.url.connect('activate', lambda *_: self.send())
        self.send_btn = button('Gửi', icon='mail-send-symbolic', css=['suggested-action'], on=self.send_or_cancel)
        self.send_btn.set_tooltip_text('Gửi (Ctrl+Enter)')
        box.append(toolbar(self.method_dd, self.url, self.send_btn))
        self.name = Gtk.Entry(placeholder_text='Tên request', hexpand=True)
        box.append(toolbar(self.name, button('Lưu', icon='document-save-symbolic', on=self.save_request), button('Nhân bản', icon='edit-copy-symbolic', on=self.dup_request),
                           button('Copy curl', icon='utilities-terminal-symbolic', on=self.copy_curl)))
        self.err = InfoStrip('error')
        box.append(self.err)
        self.rtabs = Adw.ToggleGroup(halign=Gtk.Align.START)
        for n, t in (('params', 'Params'), ('headers', 'Headers'), ('body', 'Body'), ('auth', 'Auth')):
            self.rtabs.add(Adw.Toggle(name=n, label=t))
        self.rtabs.set_active_name('params')
        self.rtabs.connect('notify::active-name', lambda g, _p: self.rstack.set_visible_child_name(g.get_active_name()))
        box.append(self.rtabs)
        self.rstack = Gtk.Stack(vexpand=True)
        self.kv_params = KVEditor('Tham số', 'Giá trị')
        self.kv_headers = KVEditor('Header', 'Giá trị')
        self.rstack.add_named(self.kv_params, 'params')
        self.rstack.add_named(self.kv_headers, 'headers')
        self.rstack.add_named(self._body_tab(), 'body')
        self.rstack.add_named(self._auth_tab(), 'auth')
        box.append(self.rstack)
        return box

    def _body_tab(self):
        box = vbox(6)
        self.body_dd = Gtk.DropDown.new_from_strings([t for _k, t in BODY_TYPES])
        self.body_dd.set_halign(Gtk.Align.START)
        self.body_dd.connect('notify::selected', lambda *_: self.on_body_type())
        box.append(self.body_dd)
        self.body_stack = Gtk.Stack(vexpand=True)
        self.body_stack.add_named(label('Request này không gửi body.', ['dim-label'], xalign=0), 'none')
        tv = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
        tv.add_css_class('mono')
        self.body_buf = tv.get_buffer()
        sw = Gtk.ScrolledWindow(child=tv, vexpand=True, min_content_height=70)
        sw.add_css_class('codebox')
        self.body_stack.add_named(sw, 'text')
        self.kv_form = KVEditor('Trường', 'Giá trị')
        self.body_stack.add_named(self.kv_form, 'form')
        box.append(self.body_stack)
        return box

    def _auth_tab(self):
        box = vbox(8)
        self.auth_dd = Gtk.DropDown.new_from_strings([t for _k, t in AUTH_TYPES])
        self.auth_dd.set_halign(Gtk.Align.START)
        self.auth_dd.connect('notify::selected', lambda *_: self.on_auth_type())
        box.append(self.auth_dd)
        self.auth_stack = Gtk.Stack(vexpand=True)
        self.auth_stack.add_named(label('Không thêm header xác thực.', ['dim-label'], xalign=0), 'none')
        self.a_token = Gtk.PasswordEntry(placeholder_text='Token', show_peek_icon=True, hexpand=True)
        self.auth_stack.add_named(self._form_box([('Token', self.a_token)]), 'bearer')
        self.a_user, self.a_pass = Gtk.Entry(placeholder_text='Tên đăng nhập', hexpand=True), Gtk.PasswordEntry(placeholder_text='Mật khẩu', show_peek_icon=True, hexpand=True)
        self.auth_stack.add_named(self._form_box([('Tên đăng nhập', self.a_user), ('Mật khẩu', self.a_pass)]), 'basic')
        self.a_kname, self.a_kval = Gtk.Entry(placeholder_text='X-API-Key', hexpand=True), Gtk.PasswordEntry(placeholder_text='Giá trị key', show_peek_icon=True, hexpand=True)
        self.auth_stack.add_named(self._form_box([('Tên header', self.a_kname), ('Giá trị', self.a_kval)]), 'apikey')
        box.append(self.auth_stack)
        box.append(label('Token/mật khẩu được lưu trong file chỉ chủ sở hữu đọc được và không ghi vào lịch sử.', ['dim-label', 'caption'], wrap=True, xalign=0))
        return box

    @staticmethod
    def _form_box(rows):
        grp = Adw.PreferencesGroup()
        for title, w in rows:
            r = Adw.ActionRow(title=esc(title))
            w.set_valign(Gtk.Align.CENTER)
            w.set_size_request(260, -1)
            r.add_suffix(w)
            grp.add(r)
        return grp

    # ================= phản hồi =================
    def _response_pane(self):
        box = content_box(8)
        box.set_margin_bottom(12)
        self.status = label('', ['badge'], visible=False, valign=Gtk.Align.CENTER)
        self.meta = label('', ['dim-label'], hexpand=True, xalign=0, ellipsize=3)
        self.pretty_sw = Gtk.Switch(active=True, valign=Gtk.Align.CENTER, tooltip_text='Hiển thị JSON dạng đẹp')
        self.pretty_sw.connect('notify::active', lambda *_: self.render_body())
        self.pretabs = Adw.ToggleGroup()
        for n, t in (('body', 'Body'), ('headers', 'Headers')):
            self.pretabs.add(Adw.Toggle(name=n, label=t))
        self.pretabs.set_active_name('body')
        self.pretabs.connect('notify::active-name', lambda g, _p: self.resp_stack.set_visible_child_name(g.get_active_name()))
        box.append(toolbar(self.status, self.meta, icon_btn('document-save-symbolic', 'Lưu body ra file…', self.save_body), label('Pretty'), self.pretty_sw, self.pretabs))
        self.resp_strip = InfoStrip('warning')
        box.append(self.resp_strip)
        self.resp_stack = Gtk.Stack(vexpand=True)
        self.resp_empty = Adw.StatusPage(icon_name='send-to-symbolic', title='Chưa có phản hồi', description='Bấm "Gửi" (hoặc Ctrl+Enter) để gọi API. Không có request nào được gửi tự động.')
        self.resp_stack.add_named(self.resp_empty, 'empty')
        tv = Gtk.TextView(editable=False, monospace=True, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
        tv.add_css_class('mono')
        self.resp_buf = tv.get_buffer()
        sw = Gtk.ScrolledWindow(child=tv, vexpand=True)
        sw.add_css_class('codebox')
        self.resp_stack.add_named(sw, 'body')
        self.hdr_table = DataTable([{'title': 'Header', 'get': lambda d: d['k'], 'sort': lambda d: d['k'].lower(), 'width': 220, 'css': ['mono']},
                                    {'title': 'Giá trị', 'get': lambda d: d['v'], 'expand': True, 'css': ['mono']}], key=lambda d: (d['k'], d['v']))
        self.resp_stack.add_named(self.hdr_table, 'headers')
        self.resp_stack.set_visible_child_name('empty')
        box.append(self.resp_stack)
        return box

    # ================= tuỳ chọn =================
    def _options_button(self):
        o = self.store.data['options']
        mb = Gtk.MenuButton(icon_name='emblem-system-symbolic', tooltip_text='Tuỳ chọn gửi', valign=Gtk.Align.CENTER)
        pop = Gtk.Popover()
        b = vbox(8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12, width_request=300)
        grp = Adw.PreferencesGroup()
        self.opt_follow = Adw.SwitchRow(title='Theo redirect (tối đa 5)', subtitle='Không bao giờ sang scheme khác', active=bool(o.get('follow', True)))
        self.opt_verify = Adw.SwitchRow(title='Kiểm tra chứng chỉ TLS', subtitle='Tắt nếu dùng chứng chỉ tự ký', active=bool(o.get('verify', True)))
        self.opt_timeout = Adw.SpinRow.new_with_range(1, 300, 1)
        self.opt_timeout.set_title('Timeout (giây)')
        self.opt_timeout.set_value(int(o.get('timeout', 30)))
        for w in (self.opt_follow, self.opt_verify, self.opt_timeout):
            grp.add(w)
        self.opt_follow.connect('notify::active', lambda *_: self.save_options())
        self.opt_verify.connect('notify::active', lambda *_: self.save_options())
        self.opt_timeout.connect('notify::value', lambda *_: self.save_options())
        b.append(grp)
        pop.set_child(b)
        mb.set_popover(pop)
        return mb

    def save_options(self):
        self.store.data['options'] = {'follow': self.opt_follow.get_active(), 'verify': self.opt_verify.get_active(), 'timeout': int(self.opt_timeout.get_value())}
        self.store.save()

    # ================= bộ sưu tập =================
    def coll(self):
        return self.store.coll(self.coll_id)

    def refresh_collections(self):
        self.syncing = True
        names = [c['name'] for c in self.store.collections]
        self.coll_dd.set_model(Gtk.StringList.new(names))
        self.coll_dd.set_selected([c['id'] for c in self.store.collections].index(self.coll_id))
        self.syncing = False
        self.refresh_saved()

    def on_coll_change(self):
        if self.syncing:
            return
        i = self.coll_dd.get_selected()
        if i < len(self.store.collections):
            self.coll_id = self.store.collections[i]['id']
            self.store.data['current'] = self.coll_id
            self.store.save()
            self.refresh_saved()

    def refresh_saved(self):
        self.syncing = True
        self.saved_list.remove_all()
        for r in self.coll()['requests']:
            row = Gtk.ListBoxRow()
            row.rid = r['id']
            line = hbox(8, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
            line.append(method_badge(r['method']))
            col = vbox(0, hexpand=True)
            col.append(label(r['name'], ellipsize=3, xalign=0))
            col.append(label(r['url'], ['dim-label', 'caption'], ellipsize=3, xalign=0))
            line.append(col)
            row.set_child(line)
            self.saved_list.append(row)
            if r['id'] == self.req['id']:
                self.saved_list.select_row(row)
        self.saved_empty.set_visible(not self.coll()['requests'])
        self.syncing = False

    def coll_dialog(self, rename):
        d = Adw.AlertDialog(heading='Đổi tên bộ sưu tập' if rename else 'Bộ sưu tập mới', body='Nên đặt theo tên project (vd. ai-meeting).')
        entry = Gtk.Entry(text=self.coll()['name'] if rename else '', activates_default=True)
        d.set_extra_child(entry)
        d.add_response('no', 'Huỷ')
        d.add_response('yes', 'Lưu')
        d.set_response_appearance('yes', Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response('yes')

        def resp(_d, r):
            if r != 'yes' or not entry.get_text().strip():
                return
            if rename:
                self.store.rename_collection(self.coll_id, entry.get_text())
            else:
                self.coll_id = self.store.add_collection(entry.get_text())['id']
                self.store.data['current'] = self.coll_id
            self.refresh_collections()
        d.connect('response', resp)
        d.present(self.get_root())

    def delete_coll(self):
        if len(self.store.collections) <= 1:
            self.toast('Phải còn ít nhất một bộ sưu tập')
            return
        from .sysmon import confirm
        c = self.coll()
        confirm(self.window, f"Xoá bộ sưu tập {c['name']}?", f"{len(c['requests'])} request trong đó sẽ bị xoá.", 'Xoá', lambda: (self.store.delete_collection(c['id']),
                setattr(self, 'coll_id', self.store.collections[0]['id']), self.refresh_collections()))

    def service_bases(self):
        out = []
        for s in self.window.store['services']:
            if s.get('port'):
                out.append((s['name'].split('/')[-1][:24], f"http://localhost:{s['port']}"))
        return out

    # ================= nạp / đọc request =================
    def load_request(self, req):
        self.syncing = True
        self.req = copy.deepcopy(req)
        self.method_dd.set_selected(H.METHODS.index(req['method']) if req['method'] in H.METHODS else 0)
        self.url.set_text(req['url'])
        self.name.set_text(req['name'])
        self.kv_params.set(req['params'])
        self.kv_headers.set(req['headers'])
        self.kv_form.set(req.get('form', []))
        set_text(self.body_buf, req.get('body', ''))
        self.body_dd.set_selected([k for k, _t in BODY_TYPES].index(req.get('body_type', 'none')))
        a = req.get('auth') or {}
        self.auth_dd.set_selected([k for k, _t in AUTH_TYPES].index(a.get('type', 'none')))
        self.a_token.set_text(a.get('token', ''))
        self.a_user.set_text(a.get('user', ''))
        self.a_pass.set_text(a.get('password', ''))
        self.a_kname.set_text(a.get('key_name', 'X-API-Key'))
        self.a_kval.set_text(a.get('key_value', ''))
        self.syncing = False
        self.on_body_type()
        self.on_auth_type()
        self.err.hide()

    def read_request(self):
        r = self.req
        r['method'] = H.METHODS[self.method_dd.get_selected()]
        r['url'] = self.url.get_text().strip()
        r['name'] = self.name.get_text().strip() or f"{r['method']} {r['url']}"[:60] or 'Request'
        r['params'], r['headers'], r['form'] = self.kv_params.get(), self.kv_headers.get(), self.kv_form.get()
        r['body'] = get_text(self.body_buf)
        r['body_type'] = BODY_TYPES[self.body_dd.get_selected()][0]
        r['auth'] = {'type': AUTH_TYPES[self.auth_dd.get_selected()][0], 'token': self.a_token.get_text(), 'user': self.a_user.get_text(), 'password': self.a_pass.get_text(),
                     'key_name': self.a_kname.get_text(), 'key_value': self.a_kval.get_text()}
        return r

    def on_body_type(self):
        k = BODY_TYPES[self.body_dd.get_selected()][0]
        self.body_stack.set_visible_child_name({'none': 'none', 'json': 'text', 'raw': 'text', 'form': 'form'}[k])

    def on_auth_type(self):
        self.auth_stack.set_visible_child_name(AUTH_TYPES[self.auth_dd.get_selected()][0])

    def new_request(self):
        self.load_request(H.new_request())
        self.refresh_saved()
        self.url.grab_focus()

    def open_saved(self, rid):
        r = next((x for x in self.coll()['requests'] if x['id'] == rid), None)
        if r:
            self.load_request(r)

    def save_request(self):
        r = self.read_request()
        self.store.save_request(self.coll_id, r)
        self.refresh_saved()
        self.toast(f"Đã lưu \"{r['name']}\" vào {self.coll()['name']}")

    def dup_request(self):
        r = self.read_request()
        c = copy.deepcopy(r)
        c['id'], c['name'] = H.new_request()['id'], r['name'] + ' (bản sao)'
        self.store.save_request(self.coll_id, c)
        self.load_request(c)
        self.refresh_saved()

    def copy_curl(self):
        try:
            prep = H.build(self.read_request(), self.coll()['vars'])
        except H.HttpError as e:
            self.err.show(str(e))
            return
        o = self.store.data['options']
        copy_text(self, H.to_curl(prep, o.get('follow', True), o.get('verify', True)))
        self.toast('Đã sao chép lệnh curl (có chứa token nếu bạn đã nhập)')

    # ================= gửi =================
    def on_key(self, _c, keyval, _code, state):
        from gi.repository import Gdk
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.CONTROL_MASK:
            self.send_or_cancel()
            return True
        return False

    def send_or_cancel(self):
        if self.sending:
            if self.cancel:
                self.cancel.cancel()
        else:
            self.send()

    def send(self):
        if self.sending:
            return
        req = self.read_request()
        try:
            prep = H.build(req, self.coll()['vars'])
        except H.HttpError as e:
            self.err.show(str(e))
            return
        self.err.hide()
        o = self.store.data['options']
        self.cancel = H.Cancel()
        cancel = self.cancel
        self.set_sending(True)
        self.resp_strip.hide()

        def work():
            return H.send(prep, follow=o.get('follow', True), verify=o.get('verify', True), timeout=int(o.get('timeout', 30)), cancel=cancel)

        def done(res, err):
            self.set_sending(False)
            if err:
                self.show_error(err)
                self.store.add_history(req, 0, 0.0, err)
            else:
                self.show_response(res)
                self.store.add_history(req, res['status'], res['elapsed'])
            self.refresh_history()
        bg(work, done)

    def set_sending(self, on):
        self.sending = on
        self.send_btn.set_label('Huỷ' if on else 'Gửi')
        for c, add in (('suggested-action', not on), ('destructive-action', on)):
            (self.send_btn.add_css_class if add else self.send_btn.remove_css_class)(c)
        if on:
            self.status.set_visible(True)
            self.set_status('Đang gửi…', 'info')
            self.meta.set_text('')

    def set_status(self, text, css):
        for c in ('ok', 'info', 'warn', 'bad'):
            self.status.remove_css_class(c)
        self.status.add_css_class(css)
        self.status.set_text(text)
        self.status.set_visible(True)

    def show_error(self, err):
        cancelled = err == 'Đã huỷ'
        self.set_status('Đã huỷ' if cancelled else 'Lỗi', 'info' if cancelled else 'bad')
        self.meta.set_text('' if cancelled else err)
        self.last = None
        self.resp_empty.set_title('Đã huỷ request' if cancelled else 'Không gửi được request')
        self.resp_empty.set_description('' if cancelled else err)
        self.resp_stack.set_visible_child_name('empty')

    def show_response(self, res):
        self.last = res
        self.set_status(f"{res['status']} {res['reason']}", status_css(res['status']))
        self.meta.set_text(f"{seconds(res['elapsed'])}  ·  {human(res['size'])}  ·  {res['url']}")
        self.hdr_table.set_rows([{'k': k, 'v': v} for k, v in res['headers']])
        notes = []
        if res['truncated']:
            notes.append(f'Phản hồi lớn hơn {H.MAX_BODY // 1048576} MB — chỉ nhận {H.MAX_BODY // 1048576} MB đầu.')
        if len(res['body']) > DISPLAY_CAP:
            notes.append(f'Chỉ hiển thị {DISPLAY_CAP // 1024} KB đầu; dùng nút lưu để lấy toàn bộ phần đã nhận.')
        if res['blocked']:
            notes.append(res['blocked'])
        if res['redirects']:
            notes.append(f"Đã theo {len(res['redirects'])} redirect.")
        if notes:
            self.resp_strip.show('  '.join(notes))
        else:
            self.resp_strip.hide()
        self.render_body()
        self.resp_stack.set_visible_child_name(self.pretabs.get_active_name())

    def render_body(self):
        res = self.last
        if not res:
            return
        ctype = next((v for k, v in res['headers'] if k.lower() == 'content-type'), '')
        body = res['body']
        if len(body) > DISPLAY_CAP:
            body = body[:DISPLAY_CAP]
            pretty = False                      # JSON bị cắt không format được
        else:
            pretty = self.pretty_sw.get_active() and not res['truncated']
        text = H.pretty(body, ctype) if pretty else body.decode('utf-8', errors='replace')
        set_text(self.resp_buf, wrap_long_lines(text) if body else '(phản hồi không có body)')

    def save_body(self):
        if not self.last:
            self.toast('Chưa có phản hồi để lưu')
            return
        dlg = Gtk.FileDialog(title='Lưu body phản hồi', initial_name='response.txt')
        data = self.last['body']

        def done(d, res):
            try:
                path = d.save_finish(res).get_path()
            except GLib.Error:
                return
            try:
                with open(path, 'wb') as f:
                    f.write(data)
                self.toast(f'Đã lưu {human(len(data))} vào {path}')
            except OSError as e:
                self.toast(f'Lỗi: {e}')
        dlg.save(self.get_root(), None, done)

    # ================= lịch sử =================
    def refresh_history(self):
        self.hist_list.remove_all()
        for h in self.store.data['history']:
            row = Gtk.ListBoxRow()
            row.h = h
            line = hbox(8, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
            line.append(method_badge(h['req']['method']))
            col = vbox(0, hexpand=True)
            col.append(label(h['req']['url'] or '—', ellipsize=3, xalign=0))
            st = f"{h['status']}  ·  {seconds(h['elapsed'])}" if h['status'] else (h.get('error') or 'lỗi')[:40]
            col.append(label(f"{st}  ·  {P.ago(h['ts'])}", ['dim-label', 'caption'], ellipsize=3, xalign=0))
            line.append(col)
            row.set_child(line)
            self.hist_list.append(row)

    def use_history(self, h):
        r = copy.deepcopy(h['req'])
        r['id'] = H.new_request()['id']
        self.load_request(r)
        self.refresh_saved()
        self.toast('Đã nạp request từ lịch sử (token/mật khẩu không được lưu trong lịch sử)')

    # ================= OpenAPI =================
    def fill_svc_menu(self):
        box = vbox(2, margin_top=4, margin_bottom=4, margin_start=4, margin_end=4)
        bases = self.service_bases()
        if not bases:
            box.append(label('Chưa có Dev Service nào có cổng.', ['dim-label'], margin_top=6, margin_bottom=6, margin_start=8, margin_end=8))
        for name, url in bases:
            box.append(button(f'{name}  —  {url}', css=['flat'], on=lambda u=url: (self.svc_pop.popdown(), self.spec_url.set_text(u + '/openapi.json'), self.load_spec_url())))
        self.svc_pop.set_child(box)

    def load_spec_url(self):
        try:
            url = H.check_url(self.spec_url.get_text())
        except H.HttpError as e:
            self.toast(str(e))
            return
        self.spec_info.set_text('Đang tải…')
        from urllib.parse import urlsplit
        p = urlsplit(url)
        origin = f'{p.scheme}://{p.netloc}'
        prep = {'method': 'GET', 'url': url, 'body': None, 'headers': [('Accept', 'application/json, application/yaml;q=0.9, */*;q=0.5'), ('User-Agent', 'DevDock-API-Tester/1.0')]}
        o = self.store.data['options']

        def work():
            res = H.send(prep, follow=True, verify=o.get('verify', True), timeout=int(o.get('timeout', 30)))
            if res['status'] != 200:
                raise H.HttpError(f"Máy chủ trả về {res['status']} {res['reason']}")
            return res['body'].decode('utf-8', errors='replace')
        bg(work, lambda text, err: self.set_spec(text, origin) if not err else self.spec_info.set_text(err))

    def open_spec_file(self):
        dlg = Gtk.FileDialog(title='Chọn file OpenAPI/Swagger (JSON hoặc YAML)')

        def done(d, res):
            try:
                path = d.open_finish(res).get_path()
            except GLib.Error:
                return
            try:
                if os.path.getsize(path) > MAX_SPEC:
                    raise OSError('File quá lớn (>20 MB)')
                with open(path, encoding='utf-8') as f:
                    self.set_spec(f.read(), None)
            except (OSError, UnicodeDecodeError) as e:
                self.spec_info.set_text(f'Không đọc được file: {e}')
        dlg.open(self.get_root(), None, done)

    def set_spec(self, text, origin):
        try:
            spec = OA.load_spec(text)
        except OA.SpecError as e:
            self.spec_info.set_text(str(e))
            return
        self.spec, self.eps, self.spec_origin = spec, OA.endpoints(spec), origin
        self.ep_list.remove_all()
        for ep in self.eps:
            row = Gtk.ListBoxRow()
            row.ep = ep
            line = hbox(8, margin_top=5, margin_bottom=5, margin_start=8, margin_end=8)
            line.append(method_badge(ep['method']))
            col = vbox(0, hexpand=True)
            col.append(label(ep['path'], ['mono'], ellipsize=3, xalign=0))
            if ep['summary']:
                col.append(label(ep['summary'], ['dim-label', 'caption'], ellipsize=3, xalign=0))
            line.append(col)
            row.set_child(line)
            self.ep_list.append(row)
        info = spec.get('info') or {}
        self.spec_info.set_text(f"{info.get('title', 'API')} {info.get('version', '')} — {len(self.eps)} endpoint, {len({e['tag'] for e in self.eps})} nhóm. Bấm một endpoint để tạo request.")
        base = self.suggest_base(origin)
        if base and 'base_url' not in self.coll()['vars']:
            self.coll()['vars']['base_url'] = base
            self.store.save()
            self.toast(f'Đã đặt biến base_url = {base} cho bộ sưu tập "{self.coll()["name"]}"')

    def suggest_base(self, origin):
        for s in OA.servers(self.spec):
            if s.startswith(('http://', 'https://')):
                return s.rstrip('/')
            if s.startswith('/') and origin:
                return origin + s.rstrip('/')
        return origin

    def ep_filter(self, row):
        q = self.ep_search.get_text().strip().lower()
        return not q or q in (row.ep['path'] + ' ' + row.ep['summary'] + ' ' + row.ep['tag'] + ' ' + row.ep['method']).lower()

    def use_endpoint(self, ep):
        base = '{{base_url}}'
        r = OA.request_from(self.spec, ep, base)
        self.load_request(r)
        self.refresh_saved()
        self.rtabs.set_active_name('body' if r['body_type'] != 'none' else 'params')
        if 'base_url' not in self.coll()['vars']:
            self.err.show('Biến {{base_url}} chưa được đặt — mở "Biến môi trường…" của bộ sưu tập hoặc sửa URL.')

    def show_tool(self, name):  # chế độ chụp ảnh kiểm thử
        if name in ('saved', 'openapi', 'history'):
            self.ltabs.set_active_name(name)
