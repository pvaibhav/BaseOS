# 08. USB adb, mass storage, and H700 OTG

`/usr/sbin/usb-gadget-adb` provides cable-based USB access on H700 devices.

## Supported behavior

BaseOS enables USB-only adb by default alongside its SSH/SFTP service. For reliable adb,
connect the data cable before powering on the handheld. If the cable comes
out, restart with it connected. BaseOS does not run a reconnect watcher,
poll the port or force a USB role. The boot files hold back five USB host
controllers so the kernel reaches init sooner, and `h700_late_usb.ko` starts
them during startup. EHCI 2 is not held back, because the OTG port does not
enumerate as a device without it. See [boot files](12-kernel-gzip.md).

A maintenance boot that BaseOS owns exposes user storage as writable
USB mass storage. Connect the cable, then hold MENU from power-on. A one-shot state
query selects the mode before BaseOS mounts frontend storage. Whole TF2 wins when
present; otherwise BaseOS exports TF1 p7. The frontend is not involved.

On normal boots this leaves the only USB-C port free for OTG host devices, and
each of the two peripheral workflows needs a deliberate action at power-on.
`/data/no-adb` remains the persistent adb opt-out. A cable-only startup may
enter charger handling; use POWER for normal startup or hold MENU for storage.
See [charger-only boot](11-charger-only-boot.md).

## Verified H700 hardware contract

The RG40XXV uses the vendor Linux 4.9.170 kernel. Its relevant built-in facilities
are:

- configfs USB gadgets and FunctionFS (`CONFIG_USB_CONFIGFS_F_FS=y`);
- mass-storage configfs support (`CONFIG_USB_CONFIGFS_MASS_STORAGE=y`);
- UDC `5100000.udc-controller`;
- sunxi OTG manager at `/sys/devices/platform/soc/usbc0`, also exposed through
  `/sys/bus/platform/devices/usbc0`;
- dual-role USB-C wiring with PMU VBUS detection and no useful ID GPIO.

The prepared H700 targets configure the sunxi manager as dual-role OTG, and BaseOS
keeps that policy. Linux does not receive the DTB inside Android boot partition
p4. The live tree comes from the checksummed `sunxi-package` beside U-Boot.
BaseOS changes neither copy.

At runtime, bind the configfs gadget to the always-present UDC and leave the
sunxi manager in auto mode. Do not use its role-control sysfs files:

- writing `usbc0/otg_role` wedges the writer permanently in uninterruptible D-state;
- `usb_device`, `usb_host`, and `usb_null` are read-trigger controls, not status
  files, and reading them can switch or wedge the port;
- reading `otg_role` itself is safe for diagnosis, but BaseOS does not need it for
  normal operation.

With the cable present during power-on, the manager reliably selects peripheral
mode. A later attach can instead select host mode and source VBUS to the computer.
Userspace cannot safely override that kernel decision.

## Gadget and daemon

The configfs gadget contains one FunctionFS adb interface (`ff/42/01`) with Google
VID/PID `18d1:4e42`. The build uses a pinned, static Android 4.2.2 `adbd` with the
Debian and Buildroot non-Android patches plus two local policies:

- stay root, matching the current root/root development-access setup;
- remove the TCP 5555 transport fallback, leaving USB as the only externally
  reachable transport.

The daemon still owns its private smart socket on `127.0.0.1:5037`, so
`usb-gadget-adb` brings up loopback before launching it. FunctionFS cannot bind until
`adbd` has supplied its descriptors, so the script runs one bounded initial UDC
bind loop. That loop handles boot ordering. It does not handle reconnects.

## Disconnect and reconnect

On VBUS loss the vendor manager clears `usb_gadget/g1/UDC`. Rebinding cannot
reliably restore access because a later attach may select host role. Restart
with the cable connected to restore peripheral access. BaseOS keeps stock
OTG support and does not run a resident reconnect watcher.

Configfs attribute sizes are synthetic. To check whether the gadget is bound,
read the contents of `g1/UDC`.

## Mass-storage maintenance mode

The gadget supports adb and writable mass storage together. Root adb stays
usable during storage mode unless `/data/no-adb` disables it.

The real frontend volume cannot be shared live. BaseOS normally mounts it and runs
the frontend from it, and simultaneous writable access by Linux and a USB host risks
filesystem corruption. Storage mode is therefore an exclusive maintenance boot:

1. connect the USB cable, then hold MENU from power-on;
2. `boot-menu-held` reads the DT-labelled physical GPIO level, because the vendor
   driver advertises `BTN_MODE` but does not maintain the evdev current-state bitmap;
3. before mounting frontend storage, choose whole TF2 when present, otherwise TF1 p7;
4. publish only an existing, completely unmounted block device to configfs;
5. keep the frontend stopped while the host owns the device;
6. eject on the host and restart without MENU.

Exporting whole TF2 exposes its real partition table, so the host can access every
filesystem it supports and deliberately repartition or format the removable card.
TF1 stays partition-backed. The running root and `/data` live on it, so exporting
whole TF1 is unsafe without a separate RAM-root recovery system.

Every unsafe state fails closed. A missing block device does nothing, and both the
selector and gadget reject a whole device when it or any `pN` child remains mounted.
`/data/no-adb` removes FunctionFS but leaves requested mass storage available.

## Validation

Automated checks cover gadget composition, repeat setup, missing UDC, composite
ADB+storage and storage-only layouts, storage selection, mounted-child rejection, and
the production AArch64 build. `validate-on-device.sh` checks `adbd`, FunctionFS, the
bound UDC, and absence of a TCP 5555 listener.

RG40XX V hardware validation covers cable-connected startup, root shell and
checksum-matched file transfer, and TF1 export to macOS with eject/restart.
Whole-card TF2 behavior still needs real-card validation for enumeration,
write/eject/restart and partition-table operations.
