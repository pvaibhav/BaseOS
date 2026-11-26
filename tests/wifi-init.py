"""Exercise rcS's actual asynchronous WiFi block with simulated kernel events.

A fake /proc/uptime advances with every sleep, so the power-off hold logic is
exercised deterministically while the stale-card timing tests keep real time.
"""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
rcs = (ROOT / 'overlay/etc/init.d/rcS').read_text()
# The Bluetooth wake driver loads at the head of the same background block,
# ahead of the WiFi driver.
start = rcs.index('# Radio initialization')
end = rcs.index('# machine-id', start)
assert rcs.index('# Stock loads the Bluetooth') > start and rcs.index('rtl_btlpm.ko') < rcs.index('8821cs.ko')
SCRIPT = rcs[start:end] + '\necho ready > /run/frontend-handoff\n'


class Radio:
    def __init__(self, power=None, stale=False, fail_first=False, slow_marker=False,
                 uptime='3.90', wlan0=True):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.process = None
        self.write('bin/busybox', Path('/bin/busybox.static').read_bytes(), True)
        for name in ('sh', 'mkdir', 'awk', 'cat', 'mv', 'rm', 'rmdir', 'cut'):
            (self.root / 'bin' / name).symlink_to('/bin/busybox')
        for directory in ('run', 'dev', 'sys/class/net', 'data', 'proc'):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.write('dev/null', '')
        self.write('dev/kmsg', '')
        if uptime is not None:
            self.write('proc/uptime', uptime + ' 0.00\n')
        if wlan0:
            (self.root / 'sys/class/net/wlan0').mkdir()
        if fail_first:
            self.write('run/fail-first', '')
        if slow_marker:
            self.write('data/wifi-slow-radio', '')
        self.write('lib/modules/rtl_btlpm.ko', '')
        self.card = self.root / 'sys/bus/sdio/devices/mmc2:0001:1'
        if stale:
            self.card.mkdir(parents=True)
        self.scan = self.root / 'sys/class/misc/sunxi-wlan/rf-ctrl/scan_device'
        if power is not None:
            self.write('sys/class/misc/sunxi-wlan/rf-ctrl/power_state', str(power) + '\n')
            self.write(str(self.scan.relative_to(self.root)), '')
        # Real sleep, plus the simulated clock advances by the same amount.
        self.write('bin/sleep', '''#!/bin/sh
if [ -f /proc/uptime ]; then
    awk -v d="$1" '{ printf "%.2f 0.00\\n", $1 + d }' /proc/uptime > /proc/uptime.new
    mv /proc/uptime.new /proc/uptime
fi
exec /bin/busybox sleep "$1"
''', True)
        self.write('sbin/insmod', '''#!/bin/sh
case "$1" in */rtl_btlpm.ko) echo btlpm >> /run/events; exit 0 ;; esac
echo "wifi-driver $(cat /proc/uptime 2>/dev/null | cut -d" " -f1)" >> /run/events
if [ -d /sys/bus/sdio/devices/mmc2:0001:1 ]; then
    echo stale > /run/driver-state
else
    echo clean > /run/driver-state
fi
if [ -e /run/fail-first ]; then
    rm /run/fail-first          # the first load enumerates nothing usable
    exit 0
fi
[ -e /run/no-wlan0 ] || mkdir -p /sys/class/net/wlan0
''', True)
        self.write('sbin/rmmod', '''#!/bin/sh
echo "rmmod $(cat /proc/uptime 2>/dev/null | cut -d" " -f1)" >> /run/events
rmdir /sys/class/net/wlan0 2>/dev/null
''', True)
        self.write('bin/ip', '#!/bin/sh\necho "ip $*" >> /run/events\n', True)
        self.write('bin/rfkill', '#!/bin/sh\necho "rfkill $*" >> /run/events\n', True)
        self.write('test.sh', SCRIPT)

    def write(self, path, contents, executable=False):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, bytes):
            target.write_bytes(contents)
        else:
            target.write_text(contents)
        if executable:
            target.chmod(0o755)

    def run(self):
        self.process = subprocess.Popen(['/bin/busybox.static', 'chroot', str(self.root), '/bin/sh', '/test.sh'],
                                        env={'PATH': '/bin:/sbin'}, start_new_session=True,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError('Timed out waiting for simulated boot event')

    def driver(self):
        path = self.root / 'run/driver-state'
        return path.read_text().strip() if path.exists() else None

    def events(self):
        path = self.root / 'run/events'
        return path.read_text().split('\n')[:-1] if path.exists() else []

    def marker(self):
        return (self.root / 'data/wifi-slow-radio').exists()

    def close(self):
        if self.process:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait()
        self.tmp.cleanup()


def uptime_of(event):
    return float(event.split()[1])


# A slow removal must delay driver registration but never frontend handoff.
r = Radio(power=0, stale=True, wlan0=False)
try:
    r.run()
    r.wait(lambda: r.scan.read_text().strip() == '1')
    assert r.process.wait(timeout=0.3) == 0, 'WiFi wait blocked frontend handoff'
    assert (r.root / 'run/frontend-handoff').exists()
    time.sleep(0.12)
    assert r.driver() is None, 'Driver probed before the stale card disappeared'
    r.card.rmdir()  # completion of the kernel's asynchronous rescan
    r.wait(lambda: r.driver() == 'clean')
    r.wait(lambda: 'ip link set wlan0 up' in r.events())
    events = r.events()
    assert events[0] == 'btlpm' and events[1].startswith('wifi-driver'), events
    assert 'rmmod' not in ''.join(events) and not r.marker()
    print('PASS: stale card removed before driver; frontend does not wait; no retry when wlan0 appears')
finally:
    r.close()

for name, power, stale in [('no radio controls', None, False),
                            ('already powered', 1, True),
                            ('nothing enumerated', 0, False)]:
    r = Radio(power=power, stale=stale, wlan0=False)
    try:
        r.run()
        r.wait(lambda: r.driver() is not None, timeout=0.5)
        if power == 1:
            assert r.scan.read_text() == '', 'Rescanned an already powered radio'
        assert r.driver() == ('stale' if stale else 'clean')
        print('PASS:', name)
    finally:
        r.close()

# An unresponsive kernel must not leave an unbounded background boot process.
r = Radio(power=0, stale=True, wlan0=False)
try:
    r.run()
    r.wait(lambda: r.scan.read_text().strip() == '1')
    assert r.process.wait(timeout=0.3) == 0
    r.wait(lambda: r.driver() == 'stale', timeout=2)
    print('PASS: failed removal has a bounded wait')
finally:
    r.close()

# A fast boot reaches the radio block early. The first load still waits for the
# point startup used to reach, so the radio's power-off interval is not cut
# short, and the frontend handoff does not wait for it.
r = Radio(power=0, stale=False, uptime='1.50')
try:
    r.run()
    assert r.process.wait(timeout=0.5) == 0, 'Hold blocked frontend handoff'
    r.wait(lambda: 'ip link set wlan0 up' in r.events(), timeout=5)
    events = r.events()
    assert [e.split()[0] for e in events] == ['btlpm', 'wifi-driver', 'rfkill', 'ip'], events
    assert 3.80 <= uptime_of(events[1]) <= 4.15, events[1]
    assert not r.marker()
    print('PASS: an early start holds the first load until uptime %.2f s' % uptime_of(events[1]))
finally:
    r.close()

# Targets without Wi-Fi (e.g. rg28xx, BASEOS_WIFI=0) skip radio loading.
r = Radio(power=0, stale=False, wlan0=False)
try:
    r.write('test.sh', 'export BASEOS_WIFI=0 BASEOS_BLUETOOTH=0\n' + SCRIPT)
    r.run()
    assert r.process.wait(timeout=0.5) == 0
    time.sleep(0.05)
    assert not r.events()
    print('PASS: disabled radio skips rtl_btlpm and 8821cs completely')
finally:
    r.close()

# First enumeration fails: unload, hold the radio off for a stock-length
# interval, load again, and remember the unit as slow on /data.
r = Radio(power=0, stale=False, fail_first=True, wlan0=False)
try:
    r.run()
    r.wait(lambda: 'ip link set wlan0 up' in r.events(), timeout=12)
    events = r.events()
    kinds = [e.split()[0] for e in events]
    assert kinds == ['btlpm', 'wifi-driver', 'rmmod', 'wifi-driver', 'rfkill', 'ip'], events
    off = uptime_of(events[3]) - uptime_of(events[2])
    assert 5.0 <= off <= 5.6, off
    assert r.marker(), 'Slow radio was not remembered'
    print('PASS: failed first load retries after a %.2f s power-off and remembers it' % off)
finally:
    r.close()

# A remembered slow radio holds the first load until stock's point, no retry.
r = Radio(power=0, stale=False, slow_marker=True, wlan0=False)
try:
    r.run()
    assert r.process.wait(timeout=0.5) == 0, 'Hold blocked frontend handoff'
    r.wait(lambda: 'ip link set wlan0 up' in r.events(), timeout=10)
    events = r.events()
    kinds = [e.split()[0] for e in events]
    assert kinds == ['btlpm', 'wifi-driver', 'rfkill', 'ip'], events
    assert uptime_of(events[1]) >= 6.85, events[1]
    print('PASS: remembered slow radio loads at uptime %.2f s without failing first' % uptime_of(events[1]))
finally:
    r.close()

# Without a readable clock the hold is skipped rather than stuck.
r = Radio(power=0, stale=False, slow_marker=True, uptime=None, wlan0=False)
try:
    r.run()
    r.wait(lambda: r.driver() == 'clean', timeout=1)
    print('PASS: missing uptime clock does not block the driver')
finally:
    r.close()
