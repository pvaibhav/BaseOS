# BaseOS

BaseOS is a small, fast operating system for Anbernic RG XX handhelds. It
handles the hardware and starts your choice of frontend:
[NextUI](https://nextui.loveretro.games), [Slot](https://slot.kowalski.io),
[spruceOS](https://spruceui.github.io), or your own. It has no menu of its own.

**[Installation guide](https://github.com/pvaibhav/BaseOS/wiki/BaseOS-Install-Guide)**

## Startup time

BaseOS v1.3.0 gets out of the way a little sooner: around **300 ms less time
loading the kernel**, plus a small improvement after Linux starts — about
**2.2 seconds**, down from **2.25 seconds in v1.2.0**.

Our latest RG SP restart tests measured about **5.6 seconds including kernel
loading**, up to the point where your frontend starts. Your frontend adds its
own time to display the menu, and results vary with the device and SD card.

The boot log now shows more accurate pre- and post-kernel timings, making it
easier to see where startup time goes.

## Features

- Display, sound, controls, Wi-Fi, HDMI, LEDs and deep sleep support.
- A small system with minimal background activity.
- Automatic expansion to use the full SD card on first boot.
- Updates by copying one file to your card. Games, saves and settings stay put.
- SSH/SFTP over Wi-Fi and adb over USB, enabled by default.
- USB storage mode to access your card from a computer.
- Optional startup sound: put `startup_sound.wav` at the root of TF1's BASEOS
  partition. Use a short WAV clip; other formats are not supported.

## Installation and updates

For a fresh install, download your model's `.img.zip`, unzip it and flash the
`.img` to TF1. Flashing erases the card. Follow the
[installation guide](https://github.com/pvaibhav/BaseOS/wiki/BaseOS-Install-Guide)
for first boot and NextUI setup with one or two cards.

To update, copy your model's `.bosupd` to the root of your frontend card and
restart. Keep power connected during the v1.3.0 update: it may show a one-time
“optimising startup” message. Let it finish without switching off.

### Other frontends

Use TF1's visible data partition or a FAT32/exFAT card in TF2. A usable TF2 card
takes priority.

- **Slot:** extract its H700 release and copy the contents of `slot-<version>`
  to the card root, including `System/slot`, `System/mgba_libretro.so` and
  `System/gpsp_libretro.so`. Put games in `Games/` and optional BIOS files in
  `BIOS/`. Update by replacing `System` with the folder from the new release.
- **spruceOS:** copy its extracted H700 release to the card root, including
  `spruce/scripts/runtime.sh`.
- **Your own frontend:** add `System/launch_frontend.sh` or a glibc AArch64
  binary at `System/frontend`. The binary takes priority; scripts run through
  `/bin/sh`. Both run from the card root and need no executable bit.

If several are installed, BaseOS chooses your custom frontend first, then
Slot, NextUI, and spruceOS. Remove higher-priority launchers to select another.
NextUI's pending `MinUI.zip` and `*.pakz` installers run before this selection.

## Settings

Edit `baseos.conf` at the root of TF1's visible partition, then restart.
Settings stay on TF1 even when your frontend is on TF2, and survive OS updates.

```ini
hostname=my-handheld
mdns=true
```

- **`hostname`**: your device's network name. Defaults to its model ID, such as
  `rg34xxsp`. Use 1–63 letters, digits or hyphens, with no hyphen at either end.
- **`mdns`**: connect using `<hostname>.local` over Wi-Fi. Enabled by default;
  set `false` to disable it.
- **`ssh_password`**: set your SSH password. Defaults to `root` if omitted or
  empty. It is stored as plain text on the card, so don't reuse an important
  password. Write it without quotes; surrounding spaces are trimmed.
- **`headphone_pop_fix`**: set `true` to try the headphone exit-pop fix. Off by
  default because it can use more battery while awake. The speaker pop fix is
  always enabled. See [audio support](docs/05-runtime-power-network.md#speaker-and-headphone-pop-repair).

Omitted settings use their defaults. Lines starting with `#` are comments;
`#` within an SSH password is part of the password.

## USB access

For adb, connect a data-capable USB-C cable before powering on. BaseOS restores
lost USB bindings when the kernel returns to device mode. If a later cable attach
selects host mode instead, restart with the computer cable connected.

For USB storage, hold **MENU** while connecting the cable. If the handheld
doesn't start automatically, hold Power for 3–4 seconds. Release Power when it
starts, but keep holding MENU until you see **“USB STORAGE: EJECT BEFORE
RESTART”**.

Eject the drive on your computer before restarting the handheld. Restart
without holding MENU to return to normal.

## Supported devices

- Anbernic RG28XX
- Anbernic RG34XX
- Anbernic RG34XX SP
- Anbernic RG35XX Plus and RG35XX 2024
- Anbernic RG35XX H
- Anbernic RG35XX Pro
- Anbernic RG35XX SP
- Anbernic RG40XX H
- Anbernic RG40XX V
- Anbernic RG CubeXX
- Anbernic RG SP

## How it works

BaseOS uses Anbernic's bootloader, kernel and hardware drivers for H700
handhelds, with a minimal BusyBox-based system in place of the stock Ubuntu
userland. Version 1.3.0 stores the kernel in compressed form to reduce loading
work, while keeping the kernel itself unchanged.

Build instructions and technical documentation are in
[CONTRIBUTING.md](CONTRIBUTING.md).
