# RG SP radio test handoff

Build-only work. No handheld, adb, card, or NextUI files were changed. Wi-Fi,
Bluetooth, coexistence, boot impact and audio playback remain device untested.
The root is `work/v2/os/rootfs.sqfs`. Use it with the matching RG SP kernel and
modules from this worktree; this task does not build or install a boot payload.

## What changed

The image gains mainline Realtek Wi-Fi/Bluetooth firmware and the signed regdb,
with licences and pinned hashes in `v2/os/radio/manifest.json`. The root already
had rfkill, udhcpc, BlueZ 5.66, BlueALSA 4.2 and its ALSA plugins. Added Debian
Wi-Fi tools, iw and the PC/SC library; replaced the inherited Bluetooth client
with standard bluetoothctl. See `v2/os/radio/README.md` for package pins.

`baseos-radio` starts after the frontend process is launched, sleeps three
seconds, and retries late interfaces in the background. It uses model flags
from baseos-release. Supplicant is explicitly nl80211 and exposes its normal
control socket in `/run/wpa_supplicant`. Credentials and enable/disable state
are on `/data/wifi`; pairings and BlueALSA state use the existing persistent
links. A wpa_cli action worker starts udhcpc on association and clears the
address, route and DNS on disconnect. The DHCP hook handles supplied netmasks.
No radio command is on the synchronous frontend startup path.

The worker samples Wi-Fi and BlueZ state, including connections made directly
through standard tools. baseos-events publishes changed snapshots and updates
ALSA default when an A2DP playback mixer appears or disappears. Bluetooth has
first priority in auto mode; controllers do not select Bluetooth audio.
Volume and mute use the selected device's BlueALSA mixer. bluealsa-aplay is
already present but is not needed: this handheld sends audio as an A2DP source.
Applications must reopen ALSA default after audio_output changes.

Wi-Fi command support is open and WPA/WPA2 PSK (hidden SSIDs included).
`connect` accepts configuration asynchronously; association/DHCP errors appear
in status and `/run/baseos/wpa.log` or `dhcp.log`. BT pair uses a Just Works
agent. PIN entry and confirmation require an interactive frontend agent.
Discovery lasts ten seconds. State events may coalesce changes; they are not
an event history. The contract describes output fields and exit codes.

## Minimal NextUI changes (not made here)

1. In H700 wifi_init.sh, on BASEOS=2 use `baseos wifi on/off` for start/stop.
   Do not kill/restart supplicant, create an SD config, or run a second DHCP
   client. An on command can report not-ready early in boot; invoke it in the
   existing background path and let the OS retry service startup.
2. H700 platform.c defines WIFI_SOCK_DIR as `/tmp/wifi/sockets`. For BaseOS 2
   choose `/run/wpa_supplicant` (or adapt its wpa_cli invocation at runtime).
   Existing scan, network editing and save_config calls can stay on wpa_cli.
3. In bt_init.sh use `baseos bt on/off` for BaseOS 2. Skip vendor module/UART
   attach scripts, systemctl, daemon startup and daemon kills: mainline serdev
   owns the UART. Keep existing bluetoothctl/D-Bus discovery and agent logic.
4. For BaseOS 2, let `baseos audio` own routing and mixer selection; audiomon
   and libmsettings currently manage their own ALSA settings. Use ALSA default,
   reopen on audio_output events, and delegate volume/mute/output to baseos.
   An application-specific .asoundrc must not override the OS default route.

Existing NextUI networks on SD are not migrated automatically. Import them
explicitly into the persistent OS config if desired; do not overwrite either
store as part of startup. No games, saves, credentials or settings were moved.

## Ordered RG SP checks

1. Boot matching kernel, DTB and this root. Confirm model flags are 1, first
   frame arrives without waiting for radios, and driver loading completes.
   Inspect dmesg for firmware/regdb signature errors and hci0/wlan interface.
2. Check `/lib/firmware` requests resolve, D-Bus runs, and only one each of
   supplicant, wpa_cli action worker, bluetoothd and bluealsa runs. Confirm
   persistent Bluetooth links and mode 0700 wifi directory / 0600 config.
3. Listen with `baseos events`. Run wifi status, scan, connect an open AP,
   then connect a WPA2 AP with a spaced SSID. Check DHCP address, actual DNS
   and traffic, including an AP with a non-/24 subnet. Test a wrong password,
   an absent SSID, saved reconnect without a password, networks and forget.
4. Restart AP, move out of range and return. Check events, renewed DHCP,
   cleared stale address/DNS on disconnect and one DHCP client after recovery.
   Test wifi off/on and reboot persistence, including a cold saved reconnect.
5. Run bt status, scan, pair, devices, connect, disconnect and forget with a
   controller. Test invalid addresses and unresponsive devices. Confirm a
   controller alone leaves audio on speaker/headphones. Use an interactive
   frontend agent for devices needing a PIN/confirmation.
6. Pair an A2DP headset. Check audio outputs includes bluetooth, auto selects
   it, playback through ALSA default works, and volume/mute act on the headset.
   Start quietly. Disconnect/reconnect while playing; reopen ALSA on events.
   Confirm fallback to USB if present, otherwise jack/speaker, including after
   explicit bluetooth selection. Test two paired headsets one at a time.
7. Reboot and update the root without clearing /data. Verify saved Wi-Fi,
   Bluetooth pairing, user settings and on/off choices survive. Check pairing
   reconnect behavior; manual connect may be needed after a cold boot.
8. Run Wi-Fi traffic with Bluetooth audio and a controller. Test lid suspend,
   wake, AP/headset loss and recovery. Check first-frame timing separately;
   no boot-performance claim has been measured by this build-only task.
9. On a model without radios, all wifi/bt commands return 3 and no radio
   services run. Complete frontend integration testing after the NextUI changes.

No SD staging, rollback copies or backups were created.

## Offline validation completed

`python3 v2/os/build.py root` completed. The squashfs is 38,572,032 bytes;
SHA-256 `81c8476718e3bbe3a18c6fbfa2ed18b787a8a0a27d4d413cf5862988059e6814`.
`docker exec v2-kbuild python3 /repo/v2/os/radio/check.py` passed:
firmware hashes in the extracted image, runtime symlinks, script syntax,
unsupported-model exit 3, real Wi-Fi tool and BlueZ/BlueALSA startup/ABI checks,
and fixture tests of saved/open Wi-Fi commands, Bluetooth command failures,
DHCP netmask/DNS, ALSA routing/volume/fallback and the compiled radio events.
The detached regdb signature verifies with OpenSSL's binary CMS check. This
checks signature/content consistency; the kernel's certificate trust and actual
radio operation still need the device run. `git diff --check` passed.
