"""Hiệu năng & làm mát: đọc nhiệt độ/quạt/xung CPU và đổi chế độ máy.

Tự phát hiện năng lực của máy (chỉ hiện điều khiển nào dùng được):
  - chế độ nguồn (power-profiles-daemon, không cần root)
  - CPU Intel P-state: Turbo Boost, xung tối đa, ưu tiên năng lượng (EPP)
  - MSI Embedded Controller (driver msi-ec): chế độ quạt, Cooler Boost, shift mode, super battery
  - ngưỡng sạc pin (charge_control_end_threshold)

An toàn: chỉ ghi vào một danh sách file sysfs cố định với giá trị đã kiểm tra; mọi thay đổi cần root gom vào MỘT lệnh pkexec
(người dùng thấy hộp thoại xin mật khẩu). Không ghi thanh ghi EC thô, không ép nạp driver. `root` cho phép test trên cây sysfs giả."""
import glob
import os
import re
import shlex
import subprocess

SYS_CPU = '/sys/devices/system/cpu'
SYS_PSTATE = SYS_CPU + '/intel_pstate'
SYS_MSI = '/sys/devices/platform/msi-ec'

# Danh sách file được phép ghi (đường dẫn tuyệt đối, không gồm root giả của test) và kiểu giá trị.
WRITABLE = [
    (re.compile(r'^/sys/devices/system/cpu/intel_pstate/no_turbo$'), 'bool01'),
    (re.compile(r'^/sys/devices/system/cpu/intel_pstate/max_perf_pct$'), 'pct'),
    (re.compile(r'^/sys/devices/system/cpu/cpufreq/policy\d+/energy_performance_preference$'), 'word'),
    (re.compile(r'^/sys/devices/system/cpu/cpu\d+/cpufreq/energy_performance_preference$'), 'word'),
    (re.compile(r'^/sys/devices/platform/msi-ec/(fan_mode|shift_mode)$'), 'word'),
    (re.compile(r'^/sys/devices/platform/msi-ec/(cooler_boost|super_battery)$'), 'onoff'),
    (re.compile(r'^/sys/class/power_supply/[\w.:-]+/charge_control_end_threshold$'), 'charge'),
]
_WORD = re.compile(r'^[A-Za-z0-9_ -]{1,24}$')


def _p(root, path):
    return os.path.join(root, path.lstrip('/')) if root not in ('', '/') else path


def _read(path, default=None):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read().strip()
    except OSError:
        return default


def _int(s, default=None):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return default


# ======================================================================================
# Đọc trạng thái
# ======================================================================================
def read_temps(root='/'):
    """{'cpu': °C|None} từ hwmon (coretemp/k10temp/zenpower; gói CPU nếu có)."""
    best = None
    for h in sorted(glob.glob(_p(root, '/sys/class/hwmon/hwmon*'))):
        name = _read(os.path.join(h, 'name'), '')
        if name not in ('coretemp', 'k10temp', 'zenpower', 'cpu_thermal'):
            continue
        vals = []
        for f in glob.glob(os.path.join(h, 'temp*_input')):
            v = _int(_read(f))
            lab = _read(f.replace('_input', '_label'), '')
            if v is not None:
                vals.append((0 if lab.startswith(('Package', 'Tctl', 'Tdie')) else 1, -v))
        if vals:
            v = -sorted(vals)[0][1] / 1000.0
            best = v if best is None else max(best, v)
    return {'cpu': best}


def read_fans(root='/'):
    """[{'name','label','rpm'}] từ mọi hwmon có fan*_input."""
    out = []
    for h in sorted(glob.glob(_p(root, '/sys/class/hwmon/hwmon*'))):
        name = _read(os.path.join(h, 'name'), os.path.basename(h))
        for f in sorted(glob.glob(os.path.join(h, 'fan*_input')), key=lambda s: int(re.sub(r'\D', '', os.path.basename(s)) or 0)):
            n = re.sub(r'\D', '', os.path.basename(f))
            rpm = _int(_read(f))
            if rpm is not None:
                out.append({'name': name, 'label': _read(f.replace('_input', '_label')) or f'Quạt {n}', 'rpm': rpm})
    return out


def read_cpu(root='/'):
    ps = _p(root, SYS_PSTATE)
    no_turbo = _read(ps + '/no_turbo')
    epp_path = _p(root, SYS_CPU + '/cpu0/cpufreq/energy_performance_preference')
    return {
        'driver': _read(_p(root, SYS_CPU + '/cpu0/cpufreq/scaling_driver')),
        'governor': _read(_p(root, SYS_CPU + '/cpu0/cpufreq/scaling_governor')),
        'turbo': None if no_turbo is None else no_turbo == '0',
        'max_pct': _int(_read(ps + '/max_perf_pct')), 'min_pct': _int(_read(ps + '/min_perf_pct')),
        'epp': _read(epp_path), 'epp_choices': (_read(_p(root, SYS_CPU + '/cpu0/cpufreq/energy_performance_available_preferences')) or '').split(),
        'epp_paths': sorted(glob.glob(_p(root, SYS_CPU + '/cpu[0-9]*/cpufreq/energy_performance_preference'))),
        'cur_mhz': (_int(_read(_p(root, SYS_CPU + '/cpu0/cpufreq/scaling_cur_freq'))) or 0) / 1000.0 or None,
        'max_mhz': (_int(_read(_p(root, SYS_CPU + '/cpu0/cpufreq/cpuinfo_max_freq'))) or 0) / 1000.0 or None,
    }


def read_msi(root='/'):
    """Trạng thái driver msi-ec, hoặc None nếu chưa nạp / máy không hỗ trợ."""
    d = _p(root, SYS_MSI)
    if not os.path.isdir(d):
        return None

    def onoff(name):
        v = _read(os.path.join(d, name))
        return None if v is None else v.lower() in ('on', '1', 'true')
    return {'fw': _read(d + '/fw_version'), 'fw_date': _read(d + '/fw_release_date'),
            'fan_mode': _read(d + '/fan_mode'), 'fan_modes': (_read(d + '/available_fan_modes') or '').split(),
            'shift_mode': _read(d + '/shift_mode'), 'shift_modes': (_read(d + '/available_shift_modes') or '').split(),
            'cooler_boost': onoff('cooler_boost'), 'super_battery': onoff('super_battery'),
            'cpu_fan_pct': _int(_read(d + '/cpu/realtime_fan_speed')), 'gpu_fan_pct': _int(_read(d + '/gpu/realtime_fan_speed'))}


def read_battery(root='/'):
    """Pin đầu tiên: {'name','capacity','status','end','path'}; 'path'/'end' là None nếu pin không hỗ trợ giới hạn sạc. None nếu máy không có pin."""
    for d in sorted(glob.glob(_p(root, '/sys/class/power_supply/BAT*'))):
        thr = d + '/charge_control_end_threshold'
        has = os.path.exists(thr)
        return {'name': os.path.basename(d), 'end': _int(_read(thr)) if has else None, 'capacity': _int(_read(d + '/capacity')), 'status': _read(d + '/status'),
                'path': thr if has else None}
    return None


def read_state(root='/'):
    return {'cpu': read_cpu(root), 'msi': read_msi(root), 'battery': read_battery(root), 'fans': read_fans(root), 'temps': read_temps(root)}


# ======================================================================================
# Lên kế hoạch ghi (kiểm tra giá trị, chỉ ghi phần khác hiện tại) + áp dụng
# ======================================================================================
def _strip(root, path):
    return path[len(root.rstrip('/')):] if root not in ('', '/') and path.startswith(root.rstrip('/')) else path


def validate_write(path, value, root='/'):
    """(ok, lý do). Chỉ cho ghi vào WRITABLE với giá trị đúng kiểu."""
    path = _strip(root, path)
    value = str(value)
    for rx, kind in WRITABLE:
        if not rx.match(path):
            continue
        if kind == 'bool01' and value in ('0', '1'):
            return True, ''
        if kind == 'pct' and value.isdigit() and 10 <= int(value) <= 100:
            return True, ''
        if kind == 'charge' and value.isdigit() and 50 <= int(value) <= 100:
            return True, ''
        if kind == 'onoff' and value in ('on', 'off'):
            return True, ''
        if kind == 'word' and _WORD.match(value):
            return True, ''
        return False, f'Giá trị không hợp lệ cho {path}: {value!r}'
    return False, f'Không được phép ghi vào {path}'


def plan(settings, state, root='/'):
    """settings: {'turbo': bool, 'max_pct': int, 'epp': str, 'fan_mode': str, 'shift_mode': str, 'cooler_boost': bool, 'super_battery': bool, 'charge_end': int}.
    Trả về (writes [(path, value)], skipped [(khoá, lý do)], changes [mô tả cho người dùng]). Chỉ gồm những mục KHÁC hiện tại và máy hỗ trợ."""
    cpu, msi, bat = state['cpu'], state['msi'], state['battery']
    writes, skipped, changes = [], [], []

    def add(path, value, text):
        ok, why = validate_write(path, value, root)
        if ok:
            writes.append((path, str(value)))
            changes.append(text)
        else:
            skipped.append((text, why))

    def has(key, avail, reason):
        if not avail:
            skipped.append((key, reason))
        return bool(avail)
    for key, val in settings.items():
        if key == 'turbo' and has(key, cpu['turbo'] is not None, 'CPU không hỗ trợ Turbo qua intel_pstate'):
            if val != cpu['turbo']:
                add(_p(root, SYS_PSTATE + '/no_turbo'), '0' if val else '1', f"Turbo Boost: {'bật' if val else 'tắt'}")
        elif key == 'max_pct' and has(key, cpu['max_pct'] is not None, 'CPU không hỗ trợ giới hạn xung'):
            if int(val) != cpu['max_pct']:
                add(_p(root, SYS_PSTATE + '/max_perf_pct'), int(val), f'Xung CPU tối đa: {int(val)}%')
        elif key == 'epp' and has(key, cpu['epp'] is not None and val in cpu['epp_choices'], 'Chế độ ưu tiên năng lượng không có trên máy này'):
            if val != cpu['epp']:
                for ep in cpu['epp_paths']:
                    add(ep, val, f'Ưu tiên năng lượng CPU: {val}' if ep == cpu['epp_paths'][0] else '')
        elif key in ('fan_mode', 'shift_mode'):
            modes = (msi or {}).get(key + 's', [])
            if has(key, msi is not None and val in modes, 'Cần driver msi-ec và chế độ phải nằm trong danh sách hỗ trợ'):
                if val != msi[key]:
                    add(_p(root, f'{SYS_MSI}/{key}'), val, ('Chế độ quạt: ' if key == 'fan_mode' else 'Shift mode: ') + val)
        elif key in ('cooler_boost', 'super_battery'):
            if has(key, msi is not None and msi.get(key) is not None, 'Cần driver msi-ec'):
                if bool(val) != msi[key]:
                    add(_p(root, f'{SYS_MSI}/{key}'), 'on' if val else 'off', ('Cooler Boost: ' if key == 'cooler_boost' else 'Super battery: ') + ('bật' if val else 'tắt'))
        elif key == 'charge_end' and has(key, bat is not None and bat['path'] is not None, 'Pin không hỗ trợ giới hạn sạc (cần driver msi-ec)'):
            if int(val) != bat['end']:
                add(bat['path'], int(val), f'Dừng sạc ở {int(val)}%')
        elif key not in ('turbo', 'max_pct', 'epp', 'fan_mode', 'shift_mode', 'cooler_boost', 'super_battery', 'charge_end'):
            skipped.append((key, 'Khoá không hợp lệ'))
    return writes, skipped, [c for c in changes if c]


def build_command(writes, root='/'):
    """Một lệnh sh duy nhất ghi mọi giá trị (đường dẫn/giá trị đã qua validate_write, được shlex.quote)."""
    parts = []
    for path, value in writes:
        ok, why = validate_write(path, value, root)
        if not ok:
            raise ValueError(why)
        parts.append(f'printf %s {shlex.quote(str(value))} > {shlex.quote(_strip(root, path))}')
    return ' && '.join(parts)


def run_privileged(writes, root='/', runner=None):
    """Thực hiện các lệnh ghi bằng quyền root qua pkexec (hiện hộp thoại mật khẩu). `runner(argv)->(rc, err)` để test. Trả về (ok, thông báo)."""
    if not writes:
        return True, 'Không có gì thay đổi'
    cmd = build_command(writes, root)
    argv = ['pkexec', '/bin/sh', '-c', cmd]
    if runner:
        rc, err = runner(argv)
    else:
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=120)
            rc, err = r.returncode, (r.stderr or r.stdout).strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f'Không chạy được pkexec: {e}'
    if rc == 126:
        return False, 'Bạn đã huỷ hoặc nhập sai mật khẩu'
    if rc == 127:
        return False, 'Không có quyền quản trị để thay đổi'
    if rc != 0:
        return False, err or f'Lệnh lỗi (mã {rc})'
    return True, 'Đã áp dụng'


def verify(writes, root='/'):
    """Đọc lại từng file để chắc giá trị đã vào (driver có thể âm thầm từ chối). Trả về danh sách (path, mong_muốn, thực_tế) bị lệch."""
    bad = []
    for path, value in writes:
        got = _read(path)
        want = str(value)
        if got is None or (got != want and not (path.endswith(('fan_mode', 'shift_mode')) and want in got.split())):
            bad.append((path, want, got))
    return bad


# ======================================================================================
# Chế độ nguồn (power-profiles-daemon) — không cần root
# ======================================================================================
def profiles(run=None):
    """(đang dùng, [các chế độ]) từ powerprofilesctl; ([], None) nếu không có daemon."""
    run = run or _run
    ok, out = run(['powerprofilesctl', 'list'])
    if not ok:
        return None, []
    names = re.findall(r'^\s*\*?\s*([a-z][a-z-]+):\s*$', out, re.M)
    cur = re.search(r'^\*\s*([a-z][a-z-]+):', out, re.M)
    return (cur.group(1) if cur else None), names


def set_profile(name, run=None):
    run = run or _run
    if not re.match(r'^[a-z][a-z-]{2,20}$', name or ''):
        return False, 'Tên chế độ không hợp lệ'
    ok, out = run(['powerprofilesctl', 'set', name])
    return ok, ('Đã đổi chế độ nguồn' if ok else out)


def _run(argv, timeout=10):
    """(thành_công, đầu_ra hoặc lỗi)."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return (True, r.stdout) if r.returncode == 0 else (False, (r.stderr or r.stdout).strip())


PROFILE_TXT = {'performance': 'Hiệu năng', 'balanced': 'Cân bằng', 'power-saver': 'Tiết kiệm điện'}


# ======================================================================================
# Chế độ nhanh (gói cài đặt): chỉ áp dụng phần máy hỗ trợ
# ======================================================================================
PRESETS = {
    'max': {'title': 'Hiệu năng tối đa', 'icon': 'power-profile-performance-symbolic', 'desc': 'Turbo bật, xung 100%. Máy nóng và ồn hơn.',
            'profile': 'performance', 'settings': {'turbo': True, 'max_pct': 100, 'epp': 'performance', 'shift_mode': 'turbo', 'fan_mode': 'auto', 'cooler_boost': False}},
    'balanced': {'title': 'Cân bằng', 'icon': 'power-profile-balanced-symbolic', 'desc': 'Dùng hằng ngày: đủ nhanh, mát vừa phải.',
                 'profile': 'balanced', 'settings': {'turbo': True, 'max_pct': 100, 'epp': 'balance_performance', 'shift_mode': 'comfort', 'fan_mode': 'auto', 'cooler_boost': False}},
    'quiet': {'title': 'Êm & mát', 'icon': 'power-profile-power-saver-symbolic', 'desc': 'Tắt Turbo, xung 70%, quạt êm. Phù hợp việc nhẹ.',
              'profile': 'power-saver', 'settings': {'turbo': False, 'max_pct': 70, 'epp': 'power', 'shift_mode': 'eco', 'fan_mode': 'silent', 'cooler_boost': False}},
    'cool': {'title': 'Làm mát khẩn cấp', 'icon': 'weather-snow-symbolic', 'desc': 'Hạ nhiệt nhanh: tắt Turbo, xung 60%, Cooler Boost (quạt tối đa).',
             'profile': 'balanced', 'settings': {'turbo': False, 'max_pct': 60, 'epp': 'balance_power', 'cooler_boost': True}},
}


def preset_settings(key, state, avail_profiles=()):
    """(profile|None, settings) của một chế độ nhanh, đã lọc theo năng lực máy (loại mục không hỗ trợ)."""
    pr = PRESETS[key]
    msi, cpu = state['msi'], state['cpu']
    s = {}
    for k, v in pr['settings'].items():
        if k in ('turbo',) and cpu['turbo'] is None:
            continue
        if k == 'max_pct' and cpu['max_pct'] is None:
            continue
        if k == 'epp' and (cpu['epp'] is None or v not in cpu['epp_choices']):
            continue
        if k in ('fan_mode', 'shift_mode') and (not msi or v not in msi.get(k + 's', [])):
            continue
        if k == 'cooler_boost' and (not msi or msi.get('cooler_boost') is None):
            continue
        s[k] = v
    prof = pr['profile'] if pr['profile'] in avail_profiles else None
    return prof, s


def thermal_advice(temp, settings, msi=None):
    """Cảnh báo khi chọn cấu hình có thể làm nóng thêm lúc CPU đang nóng. Trả về chuỗi hoặc ''."""
    if temp is None or temp < 85:
        return ''
    risky = []
    if settings.get('turbo') is True:
        risky.append('bật Turbo')
    if settings.get('max_pct', 0) >= 100:
        risky.append('xung tối đa 100%')
    if settings.get('fan_mode') == 'silent':
        risky.append('quạt êm')
    if settings.get('cooler_boost') is False and msi and msi.get('cooler_boost'):
        risky.append('tắt Cooler Boost')
    return f'CPU đang {temp:.0f}°C; {", ".join(risky)} có thể làm nóng thêm.' if risky else ''
