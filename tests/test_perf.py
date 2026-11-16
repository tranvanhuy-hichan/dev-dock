import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from devdock.lib import perf as F  # noqa: E402


def w(root, rel, text):
    p = os.path.join(root, rel.lstrip('/'))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f:
        f.write(text + '\n')
    return p


def fake_root(msi=True, battery=True, ncpu=2, battery_limit=True):
    r = tempfile.mkdtemp(prefix='ddperf-')
    w(r, '/sys/devices/system/cpu/intel_pstate/no_turbo', '0')
    w(r, '/sys/devices/system/cpu/intel_pstate/max_perf_pct', '100')
    w(r, '/sys/devices/system/cpu/intel_pstate/min_perf_pct', '17')
    for i in range(ncpu):
        w(r, f'/sys/devices/system/cpu/cpu{i}/cpufreq/energy_performance_preference', 'performance')
    w(r, '/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_available_preferences', 'default performance balance_performance balance_power power')
    w(r, '/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor', 'powersave')
    w(r, '/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver', 'intel_pstate')
    w(r, '/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq', '3200000')
    w(r, '/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq', '4600000')
    w(r, '/sys/class/hwmon/hwmon6/name', 'coretemp')
    w(r, '/sys/class/hwmon/hwmon6/temp1_input', '96000')
    w(r, '/sys/class/hwmon/hwmon6/temp1_label', 'Package id 0')
    w(r, '/sys/class/hwmon/hwmon6/temp2_input', '61000')
    w(r, '/sys/class/hwmon/hwmon4/name', 'msi_wmi_platform')
    w(r, '/sys/class/hwmon/hwmon4/fan1_input', '3582')
    w(r, '/sys/class/hwmon/hwmon4/fan2_input', '0')
    if msi:
        for k, v in {'fw_version': '16R6EMS1.104', 'fan_mode': 'auto', 'available_fan_modes': 'auto\nsilent\nbasic\nadvanced', 'shift_mode': 'comfort',
                     'available_shift_modes': 'eco\ncomfort\nsport\nturbo', 'cooler_boost': 'off', 'super_battery': 'off'}.items():
            w(r, f'/sys/devices/platform/msi-ec/{k}', v)
        w(r, '/sys/devices/platform/msi-ec/cpu/realtime_fan_speed', '47')
    if battery:
        if battery_limit:
            w(r, '/sys/class/power_supply/BAT1/charge_control_end_threshold', '100')
        w(r, '/sys/class/power_supply/BAT1/capacity', '97')
        w(r, '/sys/class/power_supply/BAT1/status', 'Not charging')
    return r


def priv_runner(root):
    """Giả lập pkexec: chạy lệnh sh đã dựng nhưng trỏ vào cây sysfs giả."""
    import subprocess

    def run(argv):
        assert argv[:3] == ['pkexec', '/bin/sh', '-c']
        cmd = argv[3].replace(' /sys/', f' {root}/sys/').replace('> /sys/', f'> {root}/sys/')
        r = subprocess.run(['/bin/sh', '-c', cmd], capture_output=True, text=True)
        return r.returncode, r.stderr
    return run


class ReadTest(unittest.TestCase):
    def test_read_everything(self):
        r = fake_root()
        s = F.read_state(r)
        self.assertEqual((s['cpu']['turbo'], s['cpu']['max_pct'], s['cpu']['epp'], s['cpu']['governor']), (True, 100, 'performance', 'powersave'))
        self.assertEqual(s['cpu']['epp_choices'], ['default', 'performance', 'balance_performance', 'balance_power', 'power'])
        self.assertEqual(len(s['cpu']['epp_paths']), 2)
        self.assertEqual((s['cpu']['cur_mhz'], s['cpu']['max_mhz']), (3200.0, 4600.0))
        self.assertEqual(s['temps']['cpu'], 96.0)           # ưu tiên nhãn Package
        self.assertEqual([(f['name'], f['rpm']) for f in s['fans']], [('msi_wmi_platform', 3582), ('msi_wmi_platform', 0)])
        m = s['msi']
        self.assertEqual((m['fw'], m['fan_mode'], m['fan_modes'], m['shift_modes'], m['cooler_boost'], m['cpu_fan_pct']), ('16R6EMS1.104', 'auto', ['auto', 'silent', 'basic', 'advanced'], ['eco', 'comfort', 'sport', 'turbo'], False, 47))
        self.assertEqual((s['battery']['end'], s['battery']['capacity'], s['battery']['status']), (100, 97, 'Not charging'))

    def test_machine_without_msi_ec_or_battery_limit(self):
        s = F.read_state(fake_root(msi=False, battery=False))
        self.assertIsNone(s['msi'])
        self.assertIsNone(s['battery'])
        self.assertIsNotNone(s['cpu']['turbo'])

    def test_battery_without_charge_limit_still_reports_capacity(self):
        b = F.read_battery(fake_root(battery_limit=False))
        self.assertEqual((b['capacity'], b['status'], b['path'], b['end']), (97, 'Not charging', None, None))
        s = F.read_state(fake_root(battery_limit=False))
        writes, skipped, _c = F.plan({'charge_end': 80}, s, '/')
        self.assertEqual((writes, len(skipped)), ([], 1))

    def test_empty_root_is_all_none(self):
        s = F.read_state(tempfile.mkdtemp())
        self.assertEqual((s['cpu']['turbo'], s['msi'], s['battery'], s['fans'], s['temps']['cpu']), (None, None, None, [], None))


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.r = fake_root()
        self.s = F.read_state(self.r)

    def test_only_differences_are_written(self):
        writes, skipped, changes = F.plan({'turbo': True, 'max_pct': 100, 'epp': 'performance', 'fan_mode': 'auto'}, self.s, self.r)
        self.assertEqual((writes, changes), ([], []))
        writes, skipped, changes = F.plan({'turbo': False, 'max_pct': 70, 'fan_mode': 'silent', 'cooler_boost': True, 'charge_end': 80}, self.s, self.r)
        self.assertEqual(sorted(os.path.basename(p) for p, _ in writes), ['charge_control_end_threshold', 'cooler_boost', 'fan_mode', 'max_perf_pct', 'no_turbo'])
        self.assertEqual(dict((os.path.basename(p), v) for p, v in writes)['no_turbo'], '1')
        self.assertEqual(len(changes), 5)
        self.assertIn('Xung CPU tối đa: 70%', changes)

    def test_epp_written_to_every_cpu_once_described(self):
        writes, _s, changes = F.plan({'epp': 'power'}, self.s, self.r)
        self.assertEqual(len(writes), 2)
        self.assertEqual(changes, ['Ưu tiên năng lượng CPU: power'])

    def test_unsupported_or_invalid_are_skipped_not_written(self):
        s = F.read_state(fake_root(msi=False, battery=False))
        writes, skipped, _c = F.plan({'fan_mode': 'silent', 'cooler_boost': True, 'charge_end': 80, 'epp': 'nonsense', 'bogus': 1}, s, '/')
        self.assertEqual(writes, [])
        self.assertEqual(len(skipped), 5)
        writes, skipped, _c = F.plan({'fan_mode': 'turbo-max'}, self.s, self.r)   # chế độ không có trong danh sách của driver
        self.assertEqual((writes, len(skipped)), ([], 1))

    def test_validate_rejects_paths_and_values(self):
        ok = F.validate_write
        self.assertTrue(ok('/sys/devices/system/cpu/intel_pstate/no_turbo', '1')[0])
        self.assertFalse(ok('/sys/devices/system/cpu/intel_pstate/no_turbo', '2')[0])
        self.assertFalse(ok('/sys/devices/system/cpu/intel_pstate/max_perf_pct', '5')[0])
        self.assertFalse(ok('/sys/devices/system/cpu/intel_pstate/max_perf_pct', '101')[0])
        self.assertFalse(ok('/sys/devices/platform/msi-ec/fan_mode', 'a; rm -rf /')[0])
        self.assertFalse(ok('/sys/devices/platform/msi-ec/fan_mode', "x'y")[0])
        self.assertFalse(ok('/sys/devices/platform/msi-ec/cooler_boost', 'maybe')[0])
        self.assertFalse(ok('/sys/class/power_supply/BAT1/charge_control_end_threshold', '10')[0])   # dưới 50%
        for bad in ('/etc/passwd', '/sys/class/power_supply/BAT1/status', '/sys/devices/system/cpu/intel_pstate/../../../../etc/shadow',
                    '/sys/devices/platform/msi-ec/../../../etc/x', '/sys/devices/platform/msi-ec/ec_raw'):
            self.assertFalse(ok(bad, '1')[0], bad)


class ApplyTest(unittest.TestCase):
    def test_single_command_and_apply_roundtrip(self):
        r = fake_root()
        s = F.read_state(r)
        writes, _s, _c = F.plan({'turbo': False, 'max_pct': 70, 'epp': 'power', 'fan_mode': 'silent', 'shift_mode': 'eco', 'cooler_boost': True, 'charge_end': 80}, s, r)
        cmd = F.build_command(writes, r)
        self.assertEqual(cmd.count('&&'), len(writes) - 1)
        self.assertNotIn(r, cmd)                              # trong lệnh thật là đường dẫn /sys/..., không có root giả
        calls = []
        runner = priv_runner(r)
        ok, msg = F.run_privileged(writes, r, runner=lambda argv: (calls.append(argv), runner(argv))[1])
        self.assertTrue(ok, msg)
        self.assertEqual(len(calls), 1)                       # CHỈ một lần xin quyền cho tất cả thay đổi
        after = F.read_state(r)
        self.assertEqual((after['cpu']['turbo'], after['cpu']['max_pct'], after['cpu']['epp']), (False, 70, 'power'))
        self.assertEqual((after['msi']['fan_mode'], after['msi']['shift_mode'], after['msi']['cooler_boost']), ('silent', 'eco', True))
        self.assertEqual(after['battery']['end'], 80)
        self.assertEqual(F.verify(writes), [])

    def test_polkit_cancel_and_failure_messages(self):
        r = fake_root()
        writes, _s, _c = F.plan({'turbo': False}, F.read_state(r), r)
        self.assertEqual(F.run_privileged(writes, r, runner=lambda a: (126, '')), (False, 'Bạn đã huỷ hoặc nhập sai mật khẩu'))
        self.assertFalse(F.run_privileged(writes, r, runner=lambda a: (127, ''))[0])
        self.assertEqual(F.run_privileged(writes, r, runner=lambda a: (1, 'boom')), (False, 'boom'))
        self.assertEqual(F.run_privileged([], r), (True, 'Không có gì thay đổi'))

    def test_build_command_refuses_bad_write(self):
        with self.assertRaises(ValueError):
            F.build_command([('/etc/passwd', 'x')])
        with self.assertRaises(ValueError):
            F.build_command([('/sys/devices/platform/msi-ec/fan_mode', '$(reboot)')])

    def test_verify_detects_silent_rejection(self):
        r = fake_root()
        writes, _s, _c = F.plan({'fan_mode': 'silent'}, F.read_state(r), r)
        self.assertEqual(len(F.verify(writes)), 1)            # chưa ghi gì: giá trị thực tế còn là 'auto'
        w(r, '/sys/devices/platform/msi-ec/fan_mode', 'silent')
        self.assertEqual(F.verify(writes), [])


class ProfilesTest(unittest.TestCase):
    OUT = '* performance:\n    CpuDriver:\tintel_pstate\n\n  balanced:\n    CpuDriver:\tintel_pstate\n\n  power-saver:\n    CpuDriver:\tintel_pstate\n'

    def test_parse_and_set(self):
        self.assertEqual(F.profiles(lambda a: (True, self.OUT)), ('performance', ['performance', 'balanced', 'power-saver']))
        self.assertEqual(F.profiles(lambda a: (False, 'x')), (None, []))
        calls = []
        self.assertTrue(F.set_profile('balanced', lambda a: (calls.append(a), (True, ''))[1])[0])
        self.assertEqual(calls, [['powerprofilesctl', 'set', 'balanced']])
        self.assertFalse(F.set_profile('x; reboot', lambda a: (True, ''))[0])
        self.assertFalse(F.set_profile('', lambda a: (True, ''))[0])


class PresetTest(unittest.TestCase):
    def test_presets_filter_by_capability(self):
        full = F.read_state(fake_root())
        prof, s = F.preset_settings('quiet', full, ['performance', 'balanced', 'power-saver'])
        self.assertEqual((prof, s['turbo'], s['max_pct'], s['fan_mode'], s['shift_mode']), ('power-saver', False, 70, 'silent', 'eco'))
        bare = F.read_state(fake_root(msi=False, battery=False))
        prof, s = F.preset_settings('quiet', bare, ['balanced'])
        self.assertEqual((prof, sorted(s)), (None, ['epp', 'max_pct', 'turbo']))      # không có msi-ec: không đụng tới quạt
        self.assertEqual(F.preset_settings('cool', full, [])[1]['cooler_boost'], True)
        self.assertNotIn('cooler_boost', F.preset_settings('cool', bare, [])[1])
        empty = F.read_state(tempfile.mkdtemp())
        self.assertEqual(F.preset_settings('max', empty, [])[1], {})

    def test_all_presets_validate(self):
        root = fake_root()
        state = F.read_state(root)
        for k in F.PRESETS:
            _p, s = F.preset_settings(k, state, [])
            writes, skipped, _c = F.plan(s, state, root)
            self.assertEqual(skipped, [], k)

    def test_thermal_advice(self):
        self.assertEqual(F.thermal_advice(70, {'turbo': True}), '')
        self.assertIn('Turbo', F.thermal_advice(96, {'turbo': True, 'max_pct': 100}))
        self.assertIn('quạt êm', F.thermal_advice(90, {'fan_mode': 'silent'}))
        self.assertEqual(F.thermal_advice(96, {'turbo': False, 'max_pct': 60}), '')
        self.assertEqual(F.thermal_advice(None, {'turbo': True}), '')


if __name__ == '__main__':
    unittest.main()
