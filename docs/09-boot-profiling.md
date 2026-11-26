# 09. Boot performance

## Build optimisation

`build-rootfs.sh` runs `tools/strip_gpu_module.py` on the harvested GPU module.
The helper removes debug data from a temporary copy. Before accepting the copy, it
verifies allocated sections, module metadata/version CRCs, runtime symbols and
relocations. It refuses signed modules. The original `stock-harvest.tar`
stays unchanged, and the helper writes validation results to
`work/<target>/gpu-strip-report.json`.

The optimised module ships in the rootfs and through normal `.bosupd` updates.
The [boot files](12-kernel-gzip.md) are a separate optimisation. They change
how the kernel is stored, drop the vendor initramfs, add three kernel arguments,
switch off serial output in boot0 and U-Boot, and leave five USB host
controllers for a module to start. The unpacked kernel stays the same.
`tests/test-strip-gpu-module.py` covers the stripping checks.

## Measuring startup

`frontend-session` writes kernel uptime to `/run/boot-frontend-exec` immediately
before its first frontend `exec`. The marker lives on tmpfs and survives frontend
respawns. Once per boot `/mnt/sdcard/baseos-boot.log` gets one
`Startup:` line (below). The log is on TF2 when it is the active frontend card,
otherwise on TF1's BASEOS partition. Open the log from a computer to read it.

From 1.5.2 each boot's entry has one header with the version and model, the
`Startup:` line, and a `Loader:` line: `fast`, or `stock` with the reason
(for example a failed health check, or the install still pending). After an
update, an install, a failed fast start or a change of loader it also has a
`--- Diagnostics ---` block. Run `baseos-diag` over adb or SSH to print and
log a block on demand. [Boot I/O](10-boot-io-audit.md#runtime-policy) lists
when each part is written. If a unit keeps showing `stock` at about 4.5 s
before Linux, it has a fast loader that cannot start it. The
[LPDDR4 fix in 1.5.2](../experiments/h700-boot-speed/DESIGN.md#zero-tuned-lpddr4-banks-152)
covers the case seen on RG40XX H and RG CubeXX units.

After the frontend starts, read the marker and boot ID:

```sh
adb -s DEVICE_SERIAL shell 'cat /run/boot-frontend-exec; cat /proc/sys/kernel/random/boot_id'
```

The marker has 10 ms resolution on the vendor kernel. It excludes bootloader
time and frontend rendering. For user-facing startup time, measure power-on to
a usable frontend separately. USB discovery and host polling delays are not
boot timings.

### Standard counter-based stages

Every supported H700 target installs `boot-clock`. At the first handoff it reads the
24 MHz ARM generic counter and `CLOCK_MONOTONIC_RAW` together and logs:

```text
Startup: 1.45 s before Linux, 1.42 s Linux to menu, 2.87 s total (NextUI)
```

Releases before 1.5.2 logged the same three values as `BaseOS boot stages:
pre-kernel ... s; post-kernel ... s; combined ... s`, plus a separate `BaseOS
boot time:` line. The log rounds to 10 ms, and `/run/boot-clock-handoff` keeps
milliseconds. Without a usable counter the line is `Startup: ... s Linux to
menu`.

"Before Linux" (`pre-kernel`) is the counter value at Linux's initial timekeeping
origin, calculated as counter seconds minus raw monotonic seconds. It includes
kernel loading and decompression and the earliest kernel work before timekeeping
starts. "Linux to menu" (`post-kernel`) is raw monotonic time at frontend handoff.
It covers the rest of kernel initialization, the vendor initramfs on a card that
still has one, and rootfs startup. "Total" (`combined`) is their sum, the raw
counter value at that handoff. The split point is Linux's clock origin, not the
first kernel instruction. Reading the counter does not change or reset it.

The raw clock avoids NTP adjustments. The existing `/run/boot-frontend-exec`
marker and 2.50-second budget keep `CLOCK_BOOTTIME` semantics and centisecond
precision. The additional stages are rounded to milliseconds. The four values
in `/run/boot-clock-handoff` are legacy handoff, pre-kernel, raw post-kernel and
combined seconds. The records survive frontend respawns.

Counter reset and within-boot stability have been measured with NextUI warm
reboots on RG SP. Counter zero is **not calibrated to LED-on**, and handoff is
not the first rendered frame. Cold starts and suspend/resume still need separate
validation. Other targets use the same interface, but their reset behavior still
needs physical-device validation. If the counter is missing, unavailable, at an
unexpected frequency or imprecisely sampled, `frontend-session` falls back to
the existing uptime-only path. See the [measurement evidence](../experiments/rgsp-boot-timing/README.md).

For performance comparisons:

- Use the same device, SD card, firmware, frontend and cable state.
- Measure at least three clean boots per variant, record distinct boot IDs,
  and compare the median and range.
- Keep cold starts and warm reboots separate. Exclude filesystem-recovery boots.
- Use the normal shutdown path so persistent filesystems are clean.

`validate-on-device.sh` enforces a 2.50-second kernel-to-frontend handoff ceiling
on all devices. See [runtime power handling](05-runtime-power-network.md) for
shutdown integration and [boot I/O](10-boot-io-audit.md) for persistence policy.
