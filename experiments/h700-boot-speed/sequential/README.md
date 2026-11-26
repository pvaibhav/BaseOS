# Disk versus RAM library fixture

This host builder creates one rcS file for both comparison variants. It changes
only the exact production `/usr/lib` mount block and inserts the existing
`first-frame.py` `SAMPLER` literally at the end. Production native-health,
charger, update and frontend hooks retain their original bytes and order.

```sh
python3 experiments/h700-boot-speed/sequential/build-fixture.py \
  overlay/etc/init.d/rcS /tmp/sequential-rcS
python3 experiments/h700-boot-speed/sequential/test-fixture.py
```

The output must be a new path. JSON on stdout records source, sampler and fixture
hashes. Building never imports `lab.py`, contacts a device or installs the output.
An unknown or duplicated library mount block, existing instrumentation or an
unexpected rcS ending is refused. The callable interface is
`build_fixture(source_text, sampler_from_source(first_frame_source_text))`.

The lab runner owns backups, installation, recovery and alternating trials.
For the next boot it writes either `baseline\n` or `ram\n` to the rootfs file
`/etc/baseos-lab-ram-libs`. The fixture removes the marker and calls `/bin/sync`
before it performs any RAM work. Without a marker, or if removing/flushing it
fails, the fixture uses the original disk image. Invalid/symlink markers are
consumed and use disk. The following reboot defaults to disk.

For `ram`, the fixture mounts a dedicated 64 MiB tmpfs at
`/usr/share/baseos/lab-ram-libs`, synchronously copies the whole existing
`/usr/share/baseos/usr-lib.sqfs`, then loop-mounts that copy at `/usr/lib` with the
same squashfs options. The tmpfs remains mounted while its loop device is in use.
`/lib` is a symlink to `/usr/lib`; placing the tmpfs there would hide it when the
library squashfs mounts. `/run` and `/tmp` are also mounted later in rcS.

Directory creation, tmpfs mount, copy or RAM squashfs mount failure falls back
to the original disk image. After a failure with tmpfs mounted, the partial copy
is removed and tmpfs unmount is attempted before disk mounting. A disk-mount
failure retains the production diagnostic. A missing image keeps the existing
bare-directory behavior. These paths do not bypass native health acceptance.

Two newline-terminated kernel messages bracket copy and mount:

```text
baseos-lab-ram-libs phase=start mode=ram status=selected backing=disk uptime=... card_read_sectors=... card_busy_ms=...
baseos-lab-ram-libs phase=end mode=ram status=ram-mounted backing=ram uptime=... card_read_sectors=... card_busy_ms=...
```

`card_read_sectors` is field 3 and `card_busy_ms` field 10 of
`/sys/block/mmcblk0/stat`. Busy time includes all card I/O, including writes.
Start occurs after marker removal/flush; end-to-end boot timing must also include
that common setup cost. No hash or loop-backing inspection is on the critical
path. The runner verifies the source/copy hashes, actual tmpfs and loop backing,
health, valid DE writeback and first-frame trace after sampling ends. Accept a
RAM trial only with `status=ram-mounted backing=ram`; fallback is a failed RAM
trial, not a RAM timing result.

The 16 host tests execute the generated shell block with real shell builtins,
isolated files and fake external commands. They cover exact copied bytes,
remove/flush-before-copy order, next-boot defaults, RAM failures and disk fallback,
missing images, invalid markers, real newline-delimited logs/counters, strict
source refusal, CLI output and preservation of every other production byte.
They do not validate a real mount, loop device, hardware recovery or performance.

Boot storage and update findings are in
`work/h700-loader/sequential-v1/boot-contract.md`. The saved current library
image cannot fit two full native payloads plus stock recovery in p4's 64 MiB.
This fixture measures whether RAM libraries help before that architecture changes.
