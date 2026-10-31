#!/usr/bin/env python3
"""Offline image checks. Run in v2-kbuild, after build.py root. No hardware used."""
import hashlib, json, os, shutil, socket, stat, subprocess, time
from pathlib import Path
root = Path('/k/radio-check')
shutil.rmtree(root, ignore_errors=True)
subprocess.run(['unsquashfs', '-quiet', '-d', str(root), '/repo/work/v2/os/rootfs.sqfs'], check=True, stdout=subprocess.DEVNULL)
def write(path, text, executable=False):
    p = root / path.lstrip('/')
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_symlink(): p.unlink()
    p.write_text(text)
    if executable: p.chmod(0o755)
def run(*args, code=0):
    p = subprocess.run(['chroot', str(root), *args], capture_output=True, text=True, timeout=15)
    assert p.returncode == code, (args, p.returncode, p.stdout, p.stderr)
    return p.stdout
for e in json.loads((root / 'usr/share/baseos/radio-firmware.json').read_text())['files']:
    assert hashlib.sha256((root / 'usr/lib/firmware' / e['path']).read_bytes()).hexdigest() == e['sha256']
for path, target in [('lib','usr/lib'), ('etc/asound.conf','/run/baseos/asound.conf'), ('etc/baseos-release','/run/baseos/release'), ('etc/resolv.conf','/run/resolv.conf')]:
    assert os.readlink(root / path) == target
for name in ['baseos', '../sbin/baseos-radio', '../sbin/baseos-wifi-action']:
    run('/bin/sh', '-n', '/usr/bin/' + name)
for name, minor in [('null',3), ('urandom',9)]:
    p = root / 'dev' / name
    if not p.exists(): os.mknod(p, stat.S_IFCHR | 0o666, os.makedev(1,minor))
write('/run/baseos/release', 'BASEOS_WIFI=0\nBASEOS_BLUETOOTH=0\n')
for area in ['wifi','bt']:
    for command in ['on','off','status','scan','devices','networks']:
        run('/usr/bin/baseos',area,command,code=3)
for tool, args in [('wpa_supplicant',['-v']), ('wpa_cli',['-v']), ('iw',['--version'])]:
    assert run('/usr/sbin/'+tool,*args)
assert 'psk=' in run('/usr/bin/wpa_passphrase','offline-test','password123')
assert '5.66' in run('/usr/libexec/bluetooth/bluetoothd','--version')
assert '4.2' in run('/usr/bin/bluealsa','--version')
# Exercise real clients against a private system bus, without /sys or radios.
write('/run/machine-id','0123456789abcdef0123456789abcdef\n')
(root / 'run/dbus').mkdir(exist_ok=True)
(root / 'var/run').symlink_to('/run')
write('/run/test-bus.conf', '<busconfig><type>system</type><listen>unix:path=/run/dbus/system_bus_socket</listen><policy context="default"><allow user="*"/><allow own="*"/><allow send_destination="*"/></policy></busconfig>')
bus = subprocess.Popen(['chroot',str(root),'/usr/bin/dbus-daemon','--config-file=/run/test-bus.conf','--nofork'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(.3)
    assert '5.66' in run('/usr/bin/bluetoothctl','--version')
    assert 'profile' in run('/usr/bin/bluealsa','--help')
finally:
    bus.terminate(); bus.wait(timeout=5)
print('PASS: image hashes, links, scripts, radio gating, Wi-Fi tools and BlueZ/BlueALSA ABI')
# Replace only tools in this disposable extraction to exercise commands offline.
write('/run/baseos/release','BASEOS_WIFI=1\nBASEOS_BLUETOOTH=1\n')
write('/run/baseos/wifi-interface','wlan0\n')
write('/proc/device-tree/compatible','anbernic,rgsp\0')
write('/usr/sbin/wpa_cli', '''#!/bin/sh
shift 4
printf '%s\\n' "$*" >> /run/wpa-calls
case "$1" in
ping) echo PONG;;
status) printf 'wpa_state=COMPLETED\\nssid=Test Net\\nip_address=192.0.2.5\\n';;
list_networks) printf 'network id\\tssid\\tbssid\\tflags\\n0\\tTest Net\\tany\\t[CURRENT]\\n';;
add_network) echo 1;;
scan_results) printf 'bssid / frequency / signal level / flags / ssid\\n00:11:22:33:44:55\\t2412\\t-50\\t[WPA2]\\tTest Net\\n';;
*) echo OK;;
esac
''',True)
assert 'ssid=Test Net' in run('/usr/bin/baseos','wifi','status')
assert 'network=0 ssid=Test Net' in run('/usr/bin/baseos','wifi','networks')
run('/usr/bin/baseos','wifi','connect','Test Net','password123')
run('/usr/bin/baseos','wifi','connect','Open Net')
run('/usr/bin/baseos','wifi','forget','Test Net')
run('/usr/bin/baseos','wifi','forget','missing',code=1)
run('/usr/bin/baseos','wifi','connect','Test Net','short',code=2)
assert 'ssid=Test Net' in run('/usr/bin/baseos','wifi','scan')
calls=(root / 'run/wpa-calls').read_text()
assert 'set_network 0 ssid 54657374204e6574' in calls and 'set_network 1 key_mgmt NONE' in calls and 'save_config' in calls
assert 'password123' not in calls
write('/usr/bin/bluetoothctl', """#!/bin/sh
case "$*" in
show) echo 'Powered: yes';;
devices) echo 'Device AA:BB:CC:DD:EE:FF Test Headset';;
'info AA:BB:CC:DD:EE:FF') printf 'Connected: yes\\nUUID: Audio Sink (0000110b-0000-1000-8000-00805f9b34fb)\\n';;
*'11:22:33:44:55:66'*) echo 'Failed to connect: org.bluez.Error.Failed';;
*) echo 'Changing state succeeded';;
esac
""",True)
assert 'connected=AA:BB:CC:DD:EE:FF' in run('/usr/bin/baseos','bt','status')
assert 'name=Test Headset' in run('/usr/bin/baseos','bt','devices')
for command in ['pair','connect','disconnect','forget']:
    run('/usr/bin/baseos','bt',command,'AA:BB:CC:DD:EE:FF')
run('/usr/bin/baseos','bt','connect','11:22:33:44:55:66',code=1)
run('/usr/bin/baseos','bt','pair','bad-address',code=2)
write('/usr/sbin/ip', '#!/bin/sh\nprintf "%s\\n" "$*" >> /run/ip-calls\n',True)
write('/run/dhcp-test', '#!/bin/sh\ninterface=wlan0 ip=192.0.2.5 subnet=255.255.255.128 router=192.0.2.1 dns=192.0.2.53 /usr/share/udhcpc/default.script bound\n',True)
run('/bin/sh','/run/dhcp-test')
assert 'addr add 192.0.2.5/255.255.255.128 dev wlan0' in (root / 'run/ip-calls').read_text()
assert 'nameserver 192.0.2.53' in (root / 'run/resolv.conf').read_text()
write('/run/baseos/bt-audio','AA:BB:CC:DD:EE:FF\n')
write('/usr/bin/amixer', '''#!/bin/sh
case "$*" in
*'scontrols'*) echo "Simple mixer control 'Test A2DP Playback',0";;
*'sget'*) echo 'Playback 50 [50%]';;
esac
''',True)
run('/usr/bin/baseos','audio','output','bluetooth')
assert 'device "AA:BB:CC:DD:EE:FF"' in (root / 'run/baseos/asound.conf').read_text()
assert 'output=bluetooth' in run('/usr/bin/baseos','audio','status')
run('/usr/bin/baseos','audio','volume','42')
write('/run/baseos/bt-audio','')
write('/proc/asound/cards',' 0 [Codec ]: H616 Audio Codec\n')
run('/usr/bin/baseos','audio','refresh')
assert 'hw:0,0' in (root / 'run/baseos/asound.conf').read_text()
assert (root / 'run/baseos/audio-output').read_text().strip() == 'auto'
# Check the compiled event daemon against changing worker snapshots.
write('/run/baseos/wifi-state','wifi=disconnected\n')
daemon=subprocess.Popen(['chroot',str(root),'/usr/sbin/baseos-events','--daemon'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    time.sleep(.3)
    client=socket.socket(socket.AF_UNIX); client.settimeout(5)
    client.connect(str(root / 'run/baseos/events.sock'))
    time.sleep(.2)
    write('/run/baseos/wifi-state','wifi=connected\nssid=Test Net\n')
    write('/run/baseos/bt-state','bluetooth=on\nconnected=AA:BB:CC:DD:EE:FF\n')
    found=b''
    while b'wifi=connected ssid=Test Net' not in found or b'bluetooth=on connected=AA:BB:CC:DD:EE:FF' not in found: found += client.recv(2048)
    client.close()
finally:
    daemon.terminate(); daemon.wait(timeout=5)
shutil.rmtree(root)
print('PASS: saved/open Wi-Fi commands, errors, Bluetooth commands, DHCP netmask/DNS, ALSA routing/volume/fallback and compiled radio events with fixtures')
