# 05 — Runtime: boot timing, power/sleep, network

## 1. Boot timing

`/run/boot-frontend-exec` records kernel uptime at the first frontend handoff.
See [boot performance](09-boot-profiling.md) for measurement instructions and
the acceptance ceiling.

`rcS` loads `mali_kbase.ko` in the background to overlap storage and service
startup. `frontend-session` performs a bounded wait for `/dev/mali0` before
handoff. Ordinary boots leave the bootloader logo untouched until the frontend
renders its first frame.

## 2. Deep sleep

The frontend enters suspend-to-RAM by writing `mem` to `/sys/power/state`.
`alsactl` saves and restores the mixer across sleep.

BaseOS applies the stock Super Standby value (`16`) when the SP-only
`axp2202-battery/os_sleep` attribute exists; NextUI also applies it before
suspend. This stops USB host controllers and excludes the hall sensor from
the wake set: POWER wakes the device, while opening the lid alone does not.
Non-SP kernels do not expose `os_sleep` and use the full USB-stop path.

## 3. Wi-Fi

BaseOS owns interface startup. Like stock, `rcS` loads `rtl_btlpm.ko` during
early boot. In a background task, `rcS` first asks the vendor WLAN controller
to discard any SDIO card enumerated before its supply was turned off by the
kernel's unused-regulator cleanup. It waits up to one second for that stale
card to disappear before loading `8821cs.ko`. Without this ordering, H700
devices can fail the driver probe with `-123` and never create `wlan0`; merely
delaying module insertion does not prevent the failed probe. Already powered
radios and kernels without these controls skip this step.

Some radios only reset properly after a long power-off. The kernel disables
the WLAN supply (`axp2202-cldo4`) at about 1.85 s and the driver re-powers it
about 0.6 s later; on those units the fresh card answers with a corrupt SDIO ID
(`020C:C821` instead of `024C:C821`, so no driver binds) or not at all, on
every boot. Stock's own boot log shows its driver load 3.8 s after the cut,
which is why the same units work there, and a 20 s power-off recovered such a
unit at runtime while 1 s did not. When the first load produces no `wlan0`
within 2 s, the task unloads the driver (which powers the radio off), keeps it
off for 5 s, loads again, and creates `/data/wifi-slow-radio`. Later boots on
that unit hold the first load until about 6.85 s uptime instead of failing
first. Units whose first attempt works, the common case, are unaffected;
delete the marker to return one to the fast path. Kernel messages prefixed
`baseos: wifi:` record which path ran.

The same background task waits for `wlan0`, then unblocks radio and brings
the interface up. None of these waits delay frontend handoff. Frontends manage
network credentials, association and DHCP, and must wait if they start before
the interface appears; NextUI's H700 Wi-Fi script waits up to 25 s.

The `systemctl` compatibility shim implements a synchronous
`stop wpa_supplicant` (including interface service names), with a bounded
two-second wait. This lets NextUI's existing restart script wait for the old
daemon's control-socket cleanup before launching its replacement. When no
supplicant is running, the command returns immediately.

Driver power saving uses the stock default, `rtw_power_mgnt=2`. This can add
latency to intermittent traffic. DHCP uses BusyBox `udhcpc`; its event script
writes `/run/resolv.conf`, linked from `/etc/resolv.conf`.

### Hostname and mDNS

`baseos-config` reads `baseos.conf` from TF1's visible partition, even when
TF2 holds the frontend. Unknown keys are ignored. The
[README settings reference](../README.md#settings) documents
the supported keys, defaults and value syntax.

The file is read once during normal boot. USB-storage mode uses defaults
without opening the exported card.

After Wi-Fi receives an IPv4 address, the DHCP hook starts the static Avahi
responder asynchronously through `baseos-mdns`. It publishes `<hostname>.local`
on `wlan0`, with no service advertisements or D-Bus dependency. Renewals reuse
the daemon; DHCP deconfiguration reconciles it with current interface state.
Avahi handles later interface changes without an additional polling process.

Name collisions receive an mDNS suffix, such as `rg34xxsp-2.local`, without
changing the configured hostname. Set distinct hostnames for stable addresses.
`mdns=false` disables the responder.

### Speaker and headphone pop repair

Each rootfs includes `h700_speaker_amp.ko`, built for its exact vendor kernel.
`rcS` loads it after settings are read and before the frontend starts audio.
The module gates the speaker amplifier around codec power transitions and
filters jack-worker GPIO writes so they cannot enable an idle amplifier.
Other GPIOs and the vendor's jack detection remain under vendor control.

`headphone_pop_fix` defaults to `false`. Enabling it retains the analogue
line-output buffers between streams to suppress the headphone exit pop.
Retention can increase idle power; buffers are released before suspend and
shutdown. Missing or invalid settings select `false`. Restart after changing
TF1's configuration. The speaker repair is active with either setting.

The build pins each kernel Image and configuration and checks module symbol
versions and layout. At load time, the module checks kernel identity, codec
widgets and GPIO callbacks before attaching. Addresses are resolved during
the build; initialization performs no symbol searches or settling sleeps.
The module cannot be unloaded because live kernel callbacks reference it;
reboot to replace it. A different firmware kernel requires a new reviewed
profile and hardware validation before it can be packaged.

Regression tests cover GPIO arbitration and startup order. Hardware acceptance
must also check audible start/stop behavior, headphone insertion/removal,
suspend/resume and the additional boot cost on each target. A successful
cross-build does not establish those hardware results.

## 4. Bluetooth audio

`rtk_hciattach` attaches the controller UART. The `systemctl` shim starts D-Bus
and BlueZ on demand; BlueALSA supplies the audio service. Frontend scripts
configure pairing and audio through the vendor-compatible shims. Radio files
are omitted from the harvest for profiles without Bluetooth.

See [validation coverage](06-status-and-lessons.md) for hardware checks.

## 5. Power button and shutdown

BaseOS provides `poweroff` and `reboot` wrappers. `baseos-poweroff` creates
`/run/poweroff-requested`; `baseos-reboot` clears it. Both then invoke BusyBox
to request shutdown through init.

`rcK` stops other processes, saves entropy, disables swap and flushes writes.
For power-off it calls `axp-off --now`, which requires persistent filesystems
to be read-only before cutting processor power through the PMIC. Reboot skips
the PMIC cut. If the PMIC helper refuses, shutdown falls back to BusyBox's
kernel path after filesystem cleanup.

Frontends should use the BaseOS commands so shutdown intent and cleanup are
preserved. Direct BusyBox applet calls or frontend-specific helpers can bypass
that integration. A long power-button press forces hardware power-off.

Charger-origin boots use the early charging path before normal services start;
see [charger-only boot](11-charger-only-boot.md).

## 6. USB access

BaseOS starts USB-only adb in the background through `/etc/init.d/dev`.
Connect a data cable before power-on. An event listener restores lost bindings
only while the kernel reports peripheral mode; it preserves OTG host use. If a
later attach selects host mode instead, restart with the cable connected.
`/data/no-adb` disables adb.

Holding MENU during startup selects exclusive USB-storage maintenance mode.
It exports whole TF2 when present, otherwise TF1's data partition, and keeps
the frontend stopped until restart. Eject the drive on the host before
restarting without MENU.

See [USB adb, mass storage and H700 OTG](08-usb-adb-and-otg.md) for gadget
configuration, role handling, storage safety and validation.

## Optional startup sound

Place `startup_sound.wav` at the root of TF1's BASEOS partition. The tested
format is 48 kHz stereo, signed 16-bit PCM WAV. No audio asset is bundled;
remove or rename the file for silent startup.

`rcS` starts `baseos-startup-sound` in the background after the audio repair.
It reuses TF1's existing mount, or mounts TF1 read-only under
`/run/startup-sound-tf1` when TF2 hosts the frontend, releasing that private
mount after playback. It never searches TF2. Missing and empty files leave
audio controls untouched. USB-storage boots skip the helper, and charger-only
startup does not reach it until POWER is accepted.

Playback uses full codec gain and the default ALSA speaker route, is limited
to 30 seconds, and does not wait before frontend handoff. The default PCM locks
its routing and digital-volume controls during playback; a frontend opening
the same hardware PCM during the sound may find it busy. Keep startup clips
short. Diagnostics go to `/run/startup-sound.log`.
