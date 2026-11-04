# 06. Hardware validation and constraints

## 1. Validation coverage

Hardware validation is specific to each device. A successful image build or
QEMU smoke test does not prove hardware support on every target.

| Capability | Validated coverage | Remaining checks |
| --- | --- | --- |
| Boot, storage expansion and NextUI launch | RG40XX V | Per-target first-boot and upgrade checks |
| Display, input, audio and GLES | NextUI on RG40XX V | Per-target validation |
| Static boot logo and status pills | RG40XX V; rotated panel on RG28XX | Other panel profiles |
| Suspend-to-RAM | RG40XX V | Long-duration standby current |
| Wi-Fi | RG40XX V association; RG34XX SP network access | Per-target startup and reconnect checks |
| SSH/SFTP | RG40XX V | Per-target validation |
| USB adb | RG40XX V cable-connected boot; shell and file transfer | Per-target validation |
| USB mass storage | RG40XX V TF1 data partition | Whole TF2 with real cards |
| HDMI | RG40XX V hotplug in both directions | Per-target validation |
| Bluetooth audio | Daemon startup | Pairing and playback |
| A/B updates | RG40XX V slot flip, slot B boot, `/data` persistence and trial confirmation | Hardware rollback after failed boots |
| GPT visibility attributes | RG40XX V boot | Windows drive-letter and format-prompt behavior |
| Charger-only boot and PMIC shutdown | RG SP; POWER-hold startup on RG34XX SP | Per-target charging and power behavior |
| Hostname and mDNS | RG34XX SP network validation | Per-target and host-network compatibility |
| Boot files without the initramfs, and their migration | RG SP: update from 1.3.0, migration on first boot, warm restarts, a cold power-on, an empty TF2 slot, sleep and wake. RG40XX V: update from 1.3.0, migration on first boot, warm restarts with Slot on TF2, sleep and wake. RG34XX SP: the same | The other eight targets |
| USB host controllers started by `h700_late_usb.ko` | RG SP: six root hubs present, adb over USB, sleep and wake, a USB audio adapter on the OTG port. RG40XX V and RG34XX SP: six root hubs present, adb over USB, sleep and wake | A device on one of the five controllers the module starts; the other eight targets |
| Fast native loader (1.5.x) | Users report the 1.5.1 fast start on RG SP, RG40XX V, RG28XX, RG34XX SP, RG34XX and RG35XX Plus. RG28XX (1 GiB LPDDR4): the 1.5.2 loader with the zero-tuned LPDDR4 fix, warm restarts, sleep and a cold power-on | RG40XX H and RG CubeXX with 1.5.2 (both fell back to stock on 1.5.1); RG35XX H, RG35XX Pro and RG35XX SP |
| `/usr/lib` served from a squashfs image | RG SP: update from the previous build through the updater, warm restarts, sleep and wake, all on-device checks. RG40XX V and RG34XX SP: update from 1.3.0, warm restarts, sleep and wake | The other eight targets; a normal power-off and power-on; a 32-bit program |

## 2. Boot and build constraints

- Root ext4 needs a journal and the feature mask the vendor kernel supports.
  `/init` must be a regular executable script. See
  [boot chain](00-boot-chain-and-partitions.md).
- Boot-critical scripts must be executable. Tool source hashes must match the
  build stamp before packaging. See [image build](02-image-build-and-flash.md).
- `/dev/input` must not have a modification time of zero, or SDL without udev
  finds no gamepad. A kernel that registers its input devices within the first
  second leaves it at zero, so `rcS` sets it. See
  [boot files](12-kernel-gzip.md#a-side-effect-on-devinput).
- HDMI switching needs the `debugfs` display interface, which `rcS` mounts.
  See [rootfs and init](01-rootfs-and-init.md).
- Framebuffer draws must finish before frontend handoff. Panel rotation comes
  from the device profile. See [boot splash](04-boot-splash.md).
- Keep the vendor USB role selection, and check configfs attribute contents
  rather than their synthetic file sizes. See [USB access](08-usb-adb-and-otg.md).
- Growing a partition while a sibling is mounted requires `BLKPG`, which
  `gptgrow` uses. See [storage expansion](03-first-boot-and-expand.md).

## 3. Development validation

QEMU tests cover generic userspace. Use hardware validation for vendor-kernel
interfaces, drivers, display orientation, suspend and power handling. After
changing the harvest, check the runtime library closure on the target.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the build and test commands, and
the relevant subsystem document for its acceptance criteria.

## 4. Open work

- Make the rootfs read-only after initialization to reduce damage from power loss.
- On an RG40XX V connected to a computer over USB, a plain restart (`reboot`,
  which the updater also uses) leaves the screen blank until reset. Release
  1.3.0 does the same. Holding POWER does not help, but reset does. Which
  shutdown step causes it is unknown. See
  [the restart record](../experiments/h700-boot-speed/evidence/rg40xxv-plain-restart.txt).
- Complete the remaining hardware checks above across supported H700 models.
- Define a `/data` schema migration before storing state that cannot be
  regenerated.

## 5. Frontend boundary

BaseOS owns hardware support, system services, storage, updates and the session
handoff. Frontends are installed separately on the card and are not BaseOS
build dependencies.
