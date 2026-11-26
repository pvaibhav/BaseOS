# 10. Boot SD-card I/O

`/run`, `/tmp`, `/var` and `/dev` are RAM-backed; `/proc` and `/sys` are kernel
interfaces. The root filesystem, `/data` and frontend card are persistent.
The vendor initramfs mounts the root filesystem read-write on a card that still
has one, and `rcS` does so otherwise. `/usr/lib` is a read-only squashfs
image stored in the root filesystem.

## Runtime policy

- Boot, session/installer, ADB and system-update diagnostics append to a single
  `/mnt/sdcard/baseos-boot.log` on the active frontend card (TF2, otherwise TF1).
  Each boot starts a new heading that names the BaseOS version and model.
  Past 256 KiB, `baseos-diag --boot` drops the oldest whole boots, down to about
  192 KiB, and keeps the file name.
- After the first handoff, `baseos-diag --boot` runs once in the background
  (`nice`, `ionice -c 3`). It adds one `Loader:` line: fast, or stock and why
  (from `/run/baseos-native-result`, the install step's files and the RTC
  latch). It adds a read-only `--- Diagnostics ---` block on the first boot of a
  build, when the install step ran, after a fast start failed its health check,
  when the loader line changed, and every 20th boot while the fast loader is
  installed but stock keeps starting. The block reads about 17 MB of boot areas,
  20 s after the handoff. Running `baseos-diag` by hand prints a block and appends
  it. Nothing runs or is written while a computer owns USB storage.
- A fast start that fails its health check writes its reason to
  `/data/boot-native-v1/install.log` and the early buffer before it waits for
  the watchdog. Healthy starts write nothing there.
- The log records the first kernel-to-frontend handoff time, and
  `/run/boot-frontend-exec` keeps it across session respawns. Verbose profiling is off.
- Update checks and storage expansion run before the card is mounted. They use
  `/data/baseos-boot.log` as an early buffer (persistent when p6 mounted).
  rcS or the next successful session mount appends it to the card log and removes
  it only after success.
  The routine "already expanded" check logs nothing; only growth and errors do.
- Session and ADB logs fall back to `/tmp/baseos-boot.log` for missing or
  unwritable cards. System-update diagnostics use the persistent `/data` buffer
  in that case. USB storage mode uses RAM and never writes to the host-owned
  frontend volume. The early `/data` buffer stays available for recovery if
  no card can be mounted.
- Frontend-owned logs, Slot's `/tmp/slot.log`, and the NTP daemon's RAM log keep
  their own destinations. BaseOS does not delete or migrate existing separate log files.
- `/mnt/SDCARD` is baked into the rootfs; compatibility repair runs only when needed.
- Frontend mounts use `noatime`, including fallback mounts.

Keep the expansion geometry check, entropy restoration and shutdown saving,
machine identity, SSH keys, preferences, update verification/rollback and
shutdown flushing. Persistence and recovery depend on them.

## Reviewing I/O changes

Keep useful boot diagnostics accessible. Avoid rewriting unchanged settings.
Flush at save, update and shutdown boundaries; logging itself adds no `sync`.

Frontend initialization happens after the BaseOS handoff marker. Review its
logging, settings and recent-game writes in the frontend project, and measure
the effect on time to the first usable frame separately.

File operations do not map one-to-one to physical SD-card I/O: buffered writes
can coalesce and repeated reads can hit cache. Measure block I/O before
claiming physical write savings.
