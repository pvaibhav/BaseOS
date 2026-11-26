# H700 boot project: handoff at user stop, 2026-10-15

## 1.5.2 (2026-10-20)

- Fast start on LPDDR4 units whose factory memory bank has zero tuning words:
  cause, fix and RG28XX trials are in
  [DESIGN.md](DESIGN.md#zero-tuned-lpddr4-banks-152). Expected to fix the
  RG40XX H and RG CubeXX field failures; only the RG28XX has run it.
- The boot log names the loader that ran and why (`Loader:` line), and adds a
  `--- Diagnostics ---` block from `baseos-diag` when useful. See
  [docs/10](../../docs/10-boot-io-audit.md#runtime-policy). `baseos-regs`
  reads the few chip registers the block needs; BaseOS has no `devmem`.
- The fast loader install no longer waits for 50% battery: it takes a few
  seconds, and its writes happen while the stock latch is held. The one
  window without a fallback is the U-Boot package rewrite on cards coming from
  1.4.0 or older. The 1.4.0 pair step still has its battery check.
- A fast start that fails its health check records why in
  `/data/boot-native-v1/install.log`.

## Next: per-model 1.5.1 release (user requirement, 2026-10-15)

The user wants a v1.5.1 `.img` and `.bosupd` for all eleven models. They must build
from the repository plus the prepared artefacts (`fetch-prepared.sh`, GitHub
`prepared-YYYYMMDD`), with no device or card access. The `.bosupd` must take any
card from 1.0.0 to 1.5.0 to 1.5.1.

- Drop the per-card binding. Build each model's capsule from the untouched stock
  boot0 in its prepared prefix. Writing the capsule replaces whatever boot0 the
  card holds; if the device falls back to stock, it retrains DRAM once, like a
  freshly flashed card. The generation and cookie then become per-model and can
  live in the rootfs image.
- A card may move between units and variants of the same model, and the user
  only picks the model's BaseOS. DRAM and panel must be detected on every boot,
  and nothing on the card may record one unit's variant. DRAM is already chosen
  at runtime by the SPL's bank selector, and the pristine boot0 carries no
  trained words. The panel is already read from the PE6 strap after power and
  reset. Remove the stored `/etc/baseos/native-panel` policy. Native health
  should instead require the kernel `lcd_type` to equal the panel the loader
  detected (its FDT display/native record) on that boot.
- Ship the model's capsule, firmware, image and recovery TOC inside the
  `.bosupd`. Extend `baseos-boot-migrate`, which already moves 1.0.0–1.3.x cards
  to the 1.4.0 boot files, with the guarded native write: hold the stock latch,
  write the body before the header, read everything back, and clear the exact
  latch. This is the same protocol as `v150/rgsp-install/install-rgsp-release.py`,
  as on-device code. Cover the starting points original vendor files (≤1.3.x),
  1.4.0 gzip pair, and 1.5.0 native.
- `.img`: the normal per-model card image with the capsule, firmware, image and
  TOC already written (sectors 16/17, 32800, p4+40/48 MiB).
- Trials: both labs from a 1.4.0 and from a 1.5.0 start, then the user's RG SP
  and RG28XX. The RG28XX's DDR3 at 792 MHz and its panel are only emulated so far.
- 1.5.1 loader source: branch `loader-speed` `a91eae7`. RG40XX V ran it: logo lit
  at 1.33 s, frontend at 3.16 s, against 1.5.0's 1.51 s and 3.46 s.
  `work/h700-loader/v151/build.py` gained an rg28xx entry after sealing; re-seal
  before more trials. The per-card rg28xx image attempt was stopped; do not
  resume it.

## Update: boot splash fixed for 1.5.0 (later on 2026-10-15)

The logo-v3 failure was the SPL itself, not a missing vendor register. The A64
helper released SYSCTRL 0x03000000/0x03000004 bit 24, which maps SRAM C
(0x28000..) to the display engine. The SPL still executes and stores data there
(`mmc_bread` 0x281f0, `sunxi_mmc`, rodata to 0x2b408, BSS 0x30000, stack 0x52a00,
called from `board_init_f`). The next card read crashed, so the watchdog chose
stock. The emulator never modelled SRAM C ownership.

The display program now runs in the A32 loader, in vendor U-Boot's position:
after BL31, from DRAM, before the kernel unpack. The SPL only reads the BMP.
Build, proofs and every hash are in `work/h700-loader/v150/` (`IMPLEMENTATION.md`,
`decisions.md`, `TOOLING.md`). No PMIC write is needed (`v150/pmic/REPORT.md`).

RG40XX V passed noack, two native cycles and sleep with the splash, generation
`4e756375…`. The logo is lit 1.51 s after the counter starts; stock lit it at
about 3.4 s. Kernel entry moves from 1.37 s to 1.74 s, mostly the vendor's
panel waits. RG34XX SP drew the splash, but its noack failed `FAT_healthy`. The
card's dirty flag was already set before this work (`old-native-normalize`
dmesg) and Linux 4.9 never clears it; `fsck.fat -n` reports nothing else.

Step 2 measurement: the card reads at 23.5–23.8 MB/s both in the SPL and in
Linux (50 MHz high-speed, 3.3 V only). Reading every library cold takes 1.33 s,
and preloading the 28.5 MB payload would cost about 1.2 s before Linux. The
whole-library preload is therefore not expected to save time.

Read this checkpoint before the older `HANDOFF.md`. The user stopped this run
and assigned the rest of the project to another agent. Implementation, new
trials and background tests are stopped. **Early logo and Step 2 are unfinished.**

## Checkout and saved work

The active source is already in the main local checkout,
`/Users/pvaibhav/Code/Me/BaseOS`, branch `uboot-trace` (`.git` is a directory).
`work/boot-speed` is an old detached worktree and has no source changes. Do not
cherry-pick its old HEAD over this checkout. Its ignored lab files/binaries are
still referenced by the environment files; keep those artifacts in place.
No branch switch, merge, push or PR was performed for this handoff.

Completed earlier commits:

- `205f677`: native SPL/minimal loader and guarded stock recovery.
- `ad95638`: vendor panel power/reset/mux prerequisites and panel-aware health.
- `d34fe44`: physically confirmed corrected personal RGSP installation.

The new handoff checkpoint preserves the unfinished logo implementation and
Step 2 source. Detailed agent notes are copied into [handoff-20261015](handoff-20261015/).
The compact machine-readable record is [handoff-state.json](evidence/handoff-state-20261015.json).
Large images, pinned originals, emulators, manifests and trial journals remain
under `work/h700-loader/` in this same local checkout. They were not deleted or
replaced by a source-only archive.

## User requirements that still apply

Finish the complete project: substantial boot improvement; native loader first,
then one sequential kernel/initramfs/library read and boot A/B; kernel rebuild
last. Support all eleven models and their DRAM/panel variants automatically.
Preserve the vendor kernel/module ABI, BL31, device interfaces, root ext4,
userdata and existing update/rollback semantics. No user panel configuration.

The user explicitly authorized lab firmware writes/restores and justified DRAM
trials with pinned backups/readbacks. They are unavailable for manual SD-card
recovery. Only the primary agent operates hardware. A watchdog/stock fallback
is required; do not infer hardware safety from emulation alone.

**Personal RGSP is excluded from further development tests/reboots.** Its
panel-fixed build runs normal Slot, physically confirmed by the user. It was
not accessed or changed during this logo/Step 2 work. SSH host is
`root@rgsp.local`; use the existing session authorization only when appropriate.

The user's latest technical direction is to follow/copy the **complete working
vendor boot0/BL31/U-Boot display sequence**, including prerequisites. Do not
continue guessing individual display registers or expanding generic test
frameworks. They are frustrated by the elapsed time and want focused completion.
Step 2 should stay on the host until final integration testing.

## Exact lab state at stop

| Device | Serial | Current running boot | Media for next boot |
| --- | --- | --- | --- |
| RG34XX SP | `1c00103dc2840671ed3` | Qualified panel-v2 native, boot `0b93bb0f-cc3e-4091-a910-baa68c301415`, BOE; frontend quiesced | Unchanged qualified panel-v2 generation `29d6650a43366b5a8e86beda15e927bea8f6279b41db2c19c2bbd0d2e2e71d8f` |
| RG40XX V | `5c001040f4438852493` | Accepted logo-v3 **without artwork** boot `61fb0e4d-2e5c-4744-a0e7-5b1a244716b6`; artwork restored; frontend quiesced | **Restored exact qualified panel-v2** generation `6a8ce34bcf4f4e67e306cb2d4ab23a38309dd68f4882ef7d59437a19b87f406b` |
| Personal RGSP | `ac001089c89588720d2` | Last confirmed normal Slot; not contacted | Confirmed panel-fixed generation `b875b0e05d7aa35fa88c329db5ed45287499769208fa3edd63ecbe6e6ffc57d5` |

RG40's private phase diagnostic was staged immediately before the stop request,
then **undone without reboot**. It was never booted. Full firmware span,
primary capsule, rootfs generation, original image, stock secondary, environment
and recovery package were read back. GP14/15 are zero and watchdog mode is zero.
The current running v3 generation differs from restored installed v2 until the
next boot; this is intentional. Do not call that an unexplained mismatch.

Final restore record and script:

```text
work/h700-loader/native-logo-v1/hardware/rg40xxv/restore-qualified-before-handoff/run.json
work/h700-loader/native-logo-v1/restore-qualified-rg40-before-handoff.py
```

SP returned through stock during this run. A qualified old-v2 ordinary boot
normalized it, then it was quiesced. No logo-v3/diagnostic/Step 2 image was ever
written to SP. Its record is `native-logo-v1/hardware/rg34xxsp/old-native-normalize/run.json`.
All FAT mounts were quiesced read-only; ADB/Wi-Fi were retained. No final reboot
or further trial was performed after the user requested handoff.

## Early-logo implementation and actual failure

Source additions are under `native/` and `spl/`. SPL reads mutable p2
`BOOTLOGO.BMP` with a bounded read-only FAT16/FAT32 reader; validates the raw
descriptor; loads an authenticated A64 helper; draws before reading Linux;
then A32 reuses its selected panel and passes vendor warm-display metadata to
the unchanged kernel. Smaller artwork is centered on opaque black. Shared
240-byte native record remains v2; optional logo descriptor is 16 bytes and
display-ready record 80 bytes.

The helper's panel streams and DE/TCON register tables were derived from actual
vendor U-Boot instruction replays, not invented values. The important unresolved
gap is the **surrounding vendor initialization**. Original U-Boot display ran
after BL31 platform initialization; our early helper runs before initial BL31.
The local oracle therefore began from a platform state that may not exist on
the native early path. Monitor proofs contain omitted resource/security/MBUS
writes. Their exact semantics/callers are not established. Do not blindly copy
them. See [vendor findings](handoff-20261015/vendor-and-packer.md) for exact
addresses, monitor PCs to trace, panel timing and a weaker PLL-bit27 hypothesis.

Host qualification completed:

- All eleven native model builds, real helper, A32 producer, cold/warm original
  kernel pixel-copy consumers, and original monitor proofs passed:
  `native-logo-v1/fleet-v1/fleet.json` (`all_eleven_models=true`).
- Final SP/RG40/RGSP capsules: `native-logo-v1/artifacts-v3/`; all three exact
  boot-chain qualifications, six actual PIO/helper cases each, 36 outer cases
  each, two inner-entry cases each, and 15 actual BootROM cases passed.
- `artifacts-v3/artifact-pins.json` seals 440 artifacts against 102 sources;
  SHA256 `7f5c7543a6b13ec90b952040c20418449f8ac2aa2ffadc11d6ef8ffcead0353c`.
- Schema3 noack fixture validates exact initial framebuffer pixels, raw80 status,
  raw56 native timing, panel, generation, source identity and watchdog ownership.
  Host cases and both live-artwork model bindings pass. This is not physical
  display acceptance.

Actual RG40 trial **failed before Linux health**: production noack yielded
autonomous stock fallback but no native witness. Journal:
`native-logo-v1/hardware/rg40xxv/noack/run.json`. Recovery helper/rcS cleanup
succeeded. Then the exact same v3 capsule/firmware booted accepted native when
only the backed-up FAT directory name was hidden. The original directory sector
was restored in `finally`; journal `hardware/rg40xxv/without-artwork/run.json`.
This isolates the failure to the artwork/early display path. No visible early
logo, successful warm hardware takeover, logo timing or new sleep qualification
was achieved. All host reports retain hardware eligibility false.

The private diagnostic is ready under `native-logo-v1/diag-src/` and
`diag-artifacts-v1/`. It logs owned GP14 phases and restores its original cookie
on success. Five helper cases, 36 outer cases and one actual BootROM native case
pass. It is **not installed now**, not booted, and not a release. Exact generation,
phase map, source pins and compare-and-swap cleanup are in
[diagnostic handoff](handoff-20261015/diagnostic-and-selector.md). No phase was
observed on hardware. Do not interpret prepared phase codes as measurements.

## Step 2: implemented host prototype, not firmware integration

The current 27.35 MB library squashfs plus kernels cannot fit two payloads and
stock recovery in p4's 64 MiB. Implemented layout keeps each root slot 512 MiB,
with **472 MiB ext4 + 40 MiB raw boot tail**. Existing whole-slot hash/readback
and GPT p5 flip select rootfs and boot payload together. p4/p6/p7 geometry stays
unchanged. Never write this tail over a live full-size ext4 filesystem.

New packer: `sequential/{boot-blob.h,build-payload.py,init}` and
`tools/boot_payload.py`. Deterministic newc carries static BusyBox and the library
sqfs. Init matches selected root identity, copies sqfs into a dedicated tmpfs
under newroot, bind-mounts it over the existing sqfs file, and switch_roots to
the original `/init`; production rcS remains unchanged. Stable firmware ABI ID
is separate from per-card capsule/RTC generation and mutable payload/root ID.

Production build/update prototype: `build-image.sh`, `build-update.sh`,
`tools/{slot_image.py,mkupdate.py}`, `overlay/usr/sbin/baseos-update`. One format2
bundle includes old/boe variants; the updater chooses automatically from trusted
existing policy, with no user configuration. It requires exact installed/running
ABI and payload IDs before complete-slot write/readback and the single GPT flip.
Legacy format1 behavior remains tested.

Completed results:

- Real SP assets packed: library 27,357,184 B; initrd 28,476,928 B; image
  37,126,144 B; blob 37,126,656 B; tail free 4,816,384 B.
  `work/h700-loader/sequential-v2/build-sp-v1/manifest.json`.
- Six format2 host methods passed with real ext4/squashfs/kernel/BusyBox and
  actual updater CLI over private regular files, both automatic panel choices,
  full-slot hashes and stubbed GPT commits. Proof:
  `sequential-v2/update-host-v1/{host-proof.json,tests.log}`.
- Existing updater/rollback suite and nine mkupdate inode tests passed.
- `sequential/{slot-reader.h,test-slot-reader.py}` are **untested drafts**.
  They were never compiled or run. No Step 2 firmware/device test or speedup exists.

Next agent must finish the GPT/descriptor C reader; wire selected-slot loading
into SPL; prove larger memory lifetimes/cache bounds; export raw32
`baseos,native-abi` and `baseos,boot-payload-id`; implement native-health format2
while retaining exact RTC ownership and legacy checks; safely migrate installer
layout; prove initramfs lifetime and A/B recovery; then do final lab integration.
Current native limits are still 1 MiB initrd / 16 MiB input. The 37.1 MB payload
cannot boot with those limits. Proposed 32/48 MiB bounds are not qualified.
Detailed contracts, commands and open gates:
[sequential integration](handoff-20261015/sequential-integration.md).

## Current benchmark, with limits

Five alternating warm USB-connected stock/native pairs per lab, before the
195 ms panel prerequisite fix, established:

| Event, median seconds | SP stock | SP native | RG40 stock | RG40 native |
| --- | ---: | ---: | ---: | ---: |
| Frontend exec | 5.811 | 2.913 | 4.181 | 2.765 |
| Settled framebuffer | 8.701 | 5.723 | 5.632 | 4.211 |
| Pre-Linux | 4.377 | 1.393 | 2.825 | 1.324 |

Evidence is `work/h700-loader/{,rg40xxv/}display-clean-matched-v1/comparison.json`.
These use the architectural 24 MHz counter, not a physical power-button/video
measurement. Corrected persistent builds have functional boots around 3.0–3.25 s
to frontend exec, without a fresh paired benchmark. No early-logo or Step 2
latency has been measured. Preserve v1.4.0 event definitions and first-frame
sampling; a boot-logo frame must not count as frontend first frame.

## Tools, proof ownership and operating notes

Use `/tmp/uboot-venv/bin/python`; system Python is 3.9. Unicorn JIT needs an
escalated host exec on this macOS host; sandbox SIGILL is not a firmware result.
Docker container `baseos-h700-mainline` contains the pinned upstream SPL tools.
No tool/test process or subagent remains active at handoff.

Lab environments (still depend on preserved old-worktree lab artifacts):

```sh
source work/boot-speed/work/lab/rg34sp.env
source work/boot-speed/work/lab/rg40.env
```

Read `lab.py` and `lab_devices.py`. Use pinned `lab.reboot()`/`reboot_next`, scoped
quiescers and exact identity checks. Never use mass process killing or a plain
reboot to bypass mounted-FAT checks. Only GP14/15 (`07000138/13c`) are owned;
preserve GP0..13 and retention. Clear only an exactly observed owned pair with
the compare-and-swap helper while the watchdog is stopped. Historical wrong RTC
addresses `1d8/1dc` are rejected. Stock recovery TOCs suppress boot0 media writes;
keep both original secondary banks, normal env and original boot image exact.

The canonical verifiers and successful local artifacts are the authority; failed,
superseded and unfrozen proofs were retained with explicit names. Re-running
`verify.py --spl` changes generated FDTs: kernel-consumer reports must bind the
new FDT bytes, not an earlier standalone run. Prepared and live artwork differ;
use each actual card's pinned p2 snapshot. RG40 live artwork is fragmented into
two extents. Diagnostic/work files use absolute ROOT paths intentionally.

`work/h700-loader/decisions.tsv` retains the decision trail. No goal object, PR
or push exists. Read this checkpoint and the copied agent notes, inspect the live
device state, then choose the smallest next vendor-backed fix.
