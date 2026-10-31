# Explicit USB-C host mode — build only

No handheld was accessed. Electrical behavior, enumeration and adb recovery
still need hardware testing. Use the Image, model DTB and root built together.

The H616 sun4i PHY already switches port 0 between MUSB and EHCI/OHCI.
A userspace-controlled role switch now exposes that path at
`/sys/class/usb_role/baseos-usbc-role-switch/role`. It starts as device and
rejects host if external VBUS is present. PI4 is never used as ID.
MUSB stays peripheral and ignores host notifications for this board; EHCI/OHCI
own host traffic. The shared overlay gives port 0 private compatible strings,
so generic host drivers cannot bind it automatically, including on H/Pro.
The USB service sets driver_override only during an explicit host request.
PHY power calls control boost -> PI16 -> VBUS; unbinding both controllers
releases their shared PHY power reference before routing back to MUSB.
The PHY also refuses host power-on while the explicit role is device.

`baseos-usb` is now a single boot service, with a flock against duplicates.
Commands queue atomic requests in RAM; they do not spawn workers from the adb
session. The service creates the gadget once and leaves adbd and FunctionFS
running while switching. Host modules are checked before disconnecting adb.
An ordinary service exit attempts adb restoration. A killed kernel/service or
hardware failure can still prevent recovery; the independent development boot
watchdog is the escape route for the first tests. Never attach the computer
while the handheld is sourcing VBUS. Remove the host adapter first and allow
power to turn off before reconnecting the computer.

USB audio is already selected by `baseos audio` in auto mode (Bluetooth A2DP
has priority, then USB, then headphones/speaker). Sound uevents trigger refresh
in baseos-events. Applications must reopen ALSA default after output changes.

## Test in this order

1. Use a development boot with `baseos.dev`, the existing 60-second heartbeat
   feeder and fallback to stock. First prove that stopping the host heartbeat
   returns to stock. Keep the stock boot and physical MENU-on-power fallback
   available. Do not promote this build to the normal boot yet.
2. Reboot into the development build. Verify adb and `baseos usb mode` reports
   adb. Check usb.log, role=device, an active gadget UDC and VBUS switch off.
   On H/Pro, verify the separate USB-A port still works and port 0 host
   controllers have no driver even with ehci_platform/ohci_platform loaded.
3. Request host while keeping the computer cable connected. It should refuse
   after 10 seconds and adb should stay working. Request adb twice; both should
   work without restarting adbd. Request host then immediately adb; no switch.
4. Refresh the development heartbeat once, request host, then unplug the
   computer promptly. Leave USB-C empty. After 20 seconds host mode should end;
   wait another few seconds before reconnecting the computer. Confirm adb,
   role=device, no port 0 host bindings and VBUS off. If recovery fails, leave
   the heartbeat stopped and let the watchdog return to stock (roughly 75 s
   after the last heartbeat, including shutdown and hardware timeout).
5. Repeat with one low-power HID controller through a proper USB-C host adapter.
   Inspect enumeration/input through a local terminal or a command scheduled
   in device /tmp before unplugging. Unplug the adapter, wait at least five
   seconds, reconnect the computer and confirm adb. Do not renew the heartbeat
   locally; a stuck host test must still fall back to stock.
6. Repeat with USB audio. Check `baseos audio`, select auto, reopen the player
   and test at low volume. Remove it; verify built-in audio returns and adb
   returns. Then try storage: inspect /dev/sd*, mount read-only in /tmp for the
   first test, unmount before unplugging. No disk contents should be changed.
7. To test explicit return with a device still attached, schedule
   `sleep 15; baseos usb mode adb` in a detached local job before requesting
   host. Confirm VBUS drops and adb returns after changing cables. Test reboot
   from host similarly; the next boot must always expose adb.
8. Repeat the empty-port recovery test on every model before longer use.
   Check both old and boe hardware where available. Only then test hubs,
   higher-power devices and suspend/resume. A hub keeps host mode alive even
   without downstream devices; explicit adb or removing the hub ends it.

Use only /tmp for any test jobs/logs. Leave no staging files on an SD card.

Offline checks: `docker exec v2-kbuild python3 /repo/v2/os/verify-usb.py`.
The service policy tests use the actual loop with simulated hardware calls;
they do not establish real USB or electrical behavior.

## Build result

Kernel commit: `b747b2a81` (Prashant Vaibhav <prashant@vaibhav.de>).
`sh v2/kernel/build.sh` and `python3 v2/os/build.py root` completed.
Artifacts: `work/v2/kernel/Image`, the 11 model DTBs and `modules/`, and
`work/v2/os/rootfs.sqfs`. The kernel export includes the resolved config and
patch 0041. Existing patch subjects change only to reflect the series length.
Policy simulations and merged DT checks pass for all models; the exported
modules resolve with both container kmod and the packaged BusyBox modprobe.
The SquashFS scripts were extracted, compared to source and syntax checked.
No BaseOS commit, push, device install or host package installation was made.
