#!/usr/bin/env python3
"""Offline USB service checks. Run in v2-kbuild; no real sysfs writes."""
from pathlib import Path
import subprocess
import tempfile

source = Path('/repo/v2/os/overlay/usr/sbin/baseos-usb').read_text()
subprocess.run(['sh', '-n', '/repo/v2/os/overlay/usr/sbin/baseos-usb'], check=True)
loop = source[source.index('state=adb\n'):]

def policy(name, online=0, attached='false', fail=False, requests=None):
    requests = requests or {0: 'host'}
    with tempfile.TemporaryDirectory() as tmp:
        r = Path(tmp)
        (r/'online').write_text(str(online))
        (r/'UDC').touch()
        cases = '\n'.join(f'{t}) echo {mode} > "$r/usb-request" ;;' for t, mode in requests.items())
        script = f'''set -eu
r={tmp}
g=$r
tick=0
adb() {{ echo "$tick adb"; }}
host() {{ echo "$tick host"; {'return 1' if fail else 'return 0'}; }}
attached() {{ {attached}; }}
request() {{ case "$tick" in {cases} esac; }}
sleep() {{ tick=$((tick+1)); [ "$tick" -lt 40 ] || exit 0; request; }}
request
''' + loop.replace('/sys/class/power_supply/axp2202-usb/online', str(r/'online'))
        result = subprocess.run(['sh', '-c', script], text=True, capture_output=True, check=True)
        events = [(int(t), m) for t, m in (line.split() for line in result.stdout.splitlines())]
        print(name, events)
        return events

e = policy('empty port')
assert [m for _, m in e] == ['host', 'adb'] and e[1][0] - e[0][0] == 20
e = policy('external power cable', online=1)
assert e == [(9, 'adb')]
e = policy('device then unplug', attached='[ "$tick" -ge 4 ] && [ "$tick" -lt 27 ]')
assert e == [(2, 'host'), (29, 'adb')]
e = policy('failed host binding', fail=True)
assert e == [(2, 'host'), (2, 'adb')]
e = policy('cancel before disconnect', requests={0:'host', 1:'adb'})
assert e == [(1, 'adb')]
e = policy('explicit adb', attached='true', requests={0:'host', 10:'adb'})
assert e == [(2, 'host'), (10, 'adb')]
e = policy('repeated host does not postpone timeout', requests={0:'host', 10:'host'})
assert e == [(2, 'host'), (22, 'adb')]
# Exercise the real device ancestry filter, including the separate host port.
with tempfile.TemporaryDirectory() as tmp:
    r = Path(tmp)
    devices = r/'devices'; devices.mkdir()
    function = source[source.index('attached() {'):source.index('host() {')]
    function = function.replace('/sys/bus/usb/devices', str(devices))
    for controller, expected in [('5200000.usb', 1), ('5101000.usb', 0), ('5101400.usb', 0)]:
        target = r/controller/'usb1'/'1-1'; target.mkdir(parents=True)
        (target/'idVendor').write_text('1234')
        link = devices/'1-1'; link.symlink_to(target)
        result = subprocess.run(['sh','-c', 'ehci=5101000.usb; ohci=5101400.usb; '+function+'\nattached'])
        assert result.returncode == expected, controller
        link.unlink()
print('USB timeout, cancellation, failure recovery and port ancestry checks passed')

# Inspect every built model after its runtime overlay is applied.
import json
import runpy
helpers = runpy.run_path('/repo/v2/kernel/verify-models.py')
read_fdt = helpers['read_fdt']
for model in json.loads(Path('/repo/devices.json').read_text())['targets']:
    ident = model['id']
    with tempfile.TemporaryDirectory() as tmp:
        merged = Path(tmp)/'merged.dtb'
        subprocess.run(['fdtoverlay', '-i', f'/repo/work/v2/kernel/{ident}.dtb', '-o', str(merged),
                        f'/k/build/drivers/of/baseos-{ident}.dtbo'], check=True)
        tree = read_fdt(merged)
        otg = tree['/soc/usb@5100000']
        assert otg['dr_mode'] == b'peripheral\0' and 'baseos,manual-host' in otg
        phy = tree['/soc/phy@5100400']
        assert 'baseos,manual-role-switch' in phy and 'usb0_id_det-gpios' not in phy
        for address, compatible in [('5101000', 'baseos,h700-manual-ehci'), ('5101400', 'baseos,h700-manual-ohci')]:
            node = tree[f'/soc/usb@{address}']
            assert node['status'] == b'okay\0'
            assert node['compatible'] == compatible.encode()+b'\0'
        if ident in ('rg35xxh', 'rg35xxpro'):
            assert tree['/soc/usb@5200000']['compatible'].endswith(b'generic-ehci\0')
            assert tree['/soc/usb@5200400']['compatible'].endswith(b'generic-ohci\0')
            assert tree['/soc/usb@5200000']['status'] == b'okay\0'
        boost = next(p for p in tree.values() if p.get('regulator-name') == b'usb-boost\0')
        vbus = tree['/regulator-usb-vbus']
        assert 'regulator-boot-on' not in vbus and 'regulator-always-on' not in boost
        assert helpers['cells'](vbus['gpio'])[1:3] == (8, 16)
release = next(Path('/repo/work/v2/kernel/modules/lib/modules').iterdir()).name
for module in ('ehci_platform', 'ohci_platform', 'snd_usb_audio', 'usb_storage', 'uas', 'sd_mod', 'usbhid', 'hid_generic', 'xpad'):
    result = subprocess.run(['modprobe', '-d', '/repo/work/v2/kernel/modules', '-S', release,
                             '--show-depends', module], text=True, capture_output=True, check=True)
    for line in result.stdout.splitlines():
        if line.startswith('insmod '):
            assert Path(line.split()[1]).is_file(), line
aliases = Path(f'/repo/work/v2/kernel/modules/lib/modules/{release}/modules.alias').read_text()
assert 'baseos,h700-manual-' not in aliases
print('All 11 DTs: peripheral default, explicit-only host binding, PI16 VBUS; host modules and dependencies present')
