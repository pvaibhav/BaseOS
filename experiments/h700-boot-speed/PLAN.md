# H700 boot-speed work

Replace vendor boot0 and U-Boot with automatic DRAM selection and a minimal
loader while preserving the vendor kernel ABI, monitor, SCP, display, input,
GPU, Wi-Fi and power behavior across all eleven models. No user configuration
may be required for DRAM or panel revisions.

Step 1 is committed as `205f677`, with the old-panel acquisition fix in
`ad95638`. Both corrected lab chains pass recovery, ordinary boots, sleep/wake
and the following boot. The corrected RGSP install preserved `lcd_type=old`,
and the user confirmed normal Slot. Leave personal RGSP untouched for daily
use and continue step 2 on the labs. Generic fleet qualification remains open.
The authoritative checkpoint and evidence locations are in [HANDOFF.md](HANDOFF.md).

The current user priority is an early boot logo from the native loader, before
Linux, with the configured p2 artwork and automatic panel selection. The kernel
pixel-copy path can preserve artwork during takeover but does not satisfy the
requested early appearance by itself. Qualify loader display initialization and
takeover on the two labs before resuming step 2. Personal RGSP stays untouched.

## 1. Qualify and ship the native boot chain

- [x] Preserve exact boot-area backups and identify each lab device explicitly.
- [x] Build and emulate the SPL, minimal A32 loader and original monitor handoff.
- [x] Prove restore-first BROM recovery and execute matched hardware controls.
- [x] Boot the original vendor Linux on both lab devices.
- [x] Correct runtime LPDDR3/LPDDR4 PHY selection; prove the vendor SID wiring
  selection against all eleven prepared banks.
- [x] Qualify the combined automatic PHY/SID implementation on both devices.
- [x] Resolve blank DE output and verify scanout against the active framebuffer.
- [ ] Exercise prepared factory DRAM banks on SP; retain separate fleet proof
  and physical-hardware coverage limits.
- [ ] Qualify PMIC bootreason, charger entry and ordinary shutdown behavior.
- [x] Qualify per-card persistent recovery, including deep-sleep resume and
  the following ordinary native boot, on both labs.
- [x] Activate the Linux health acknowledgement at the correct startup points.
- [x] Integrate per-card installation, generation-bound updates and recovery pins.
- [ ] Integrate generic fleet rootfs/image assembly.
- [ ] Validate repeated boot, controls, audio, GPU, Wi-Fi, games and sleep/wake
  on both lab devices; rerun all-eleven-model artifact and compatibility proofs.
- [x] Repeat matched clean-path benchmarks with display validation and fresh boot IDs.
- [ ] Repeat final persistent-release timing after its functional qualification.
- [x] Reconcile documentation and evidence, review the decision trail, and
  commit the native chain locally with a detailed message (`205f677`). No push.
- [x] Correct panel power/reset/strap acquisition for all models, enforce
  stock/native panel equality before acknowledgment, and requalify both labs.
- [x] Install the corrected RGSP release and establish physical-screen
  acceptance before leaving it for daily use.

The selected 128 KiB raw capsule contains exact stock boot0 and an adopt-only
native SPL. Actual Boot ROM replay, physical watchdog-retention controls,
automatic stock recovery and deep-sleep resume now pass on both lab devices.
The corrected recovery TOC suppresses vendor writes to both boot0 banks.
The earlier compressed and card-reader resume prototypes remain rejected.
Partial-sector header tears can still defeat ROM fallback; there is no
arbitrary power-loss protection guarantee from the secondary copy alone.

The first SP no-ack trial exposed a FAT write interrupted by intentional reset.
Its failed report is retained, its backed-up FAT/recent-list repair passes,
and the corrected trial blocks in production startup before frontend writes.
A repair command also killed userspace services by applying a filesystem-wide
kill to an unmounted path. The user rebooted the lab SP; scoped process guards
now reject that path and preserve init, ADB, SSH and unrelated services.

## 2. Read the critical path sequentially

The RGSP panel fix is installed and physically confirmed. Package the kernel and a cpio
containing the library squashfs image into a contiguous boot payload, load
libraries from RAM, and add boot-image A/B selection. Preserve root ext4,
rootfs slots and update semantics. Measure the added sequential bytes against
the avoided random card reads; the early one-second estimate is a hypothesis.

All eleven vendor kernels already support initramfs. Two current 27.35 MB library
images plus their kernels and stock recovery exceed p4's 64 MiB. The host startup
fixture and sixteen shell cases are ready. Measure the complete cost of a
synchronous RAM copy before selecting compression or a root-slot boot tail.
See `work/h700-loader/sequential-v1/boot-contract.md` for the measured space
budget and generation/update requirements.

## 3. Reduce kernel initialization

Rebuild the vendor 4.9.170 kernel only after the preceding changes. Preserve
module ABI and hardware coverage, remove unused initialization where justified,
and benchmark each change independently. This phase has not started.

## Measurement and recovery rules

Five fresh alternating USB-connected warm pairs per lab pass with correct DE
output on every boot. SP frontend exec is 5.811 → 2.913 s (native range
2.859–2.945 s), settled framebuffer 8.701 → 5.723 s. RG40 frontend exec is
4.181 → 2.765 s (native range2.709–2.858 s), settled framebuffer5.632 →4.211 s.
Evidence: `work/h700-loader/{,rg40xxv/}display-clean-matched-v1/comparison.json`.
These qualify the clean restore-first path. The persistent capsule has passed
functional checks, but these medians are not repeated persistent benchmarks
or physical panel observations. The earlier green-screen timings remain
historical only.

The counter origin is the 24 MHz hardware timer. These results do not measure
physical power-button-to-picture time. Keep device, card, rootfs, frontend and
USB connection matched; report failures and ranges as well as medians. New
comparison runs must reject blank or mismatched DE writeback pixels.

No manual recovery is available. Every first-stage experiment needs matching
instruction-level proofs and verified automatic stock recovery before writes.
Only the primary agent operates hardware; host-only workers use separate
artifact directories. Decisions and rejected trials are recorded append-only
in `work/h700-loader/decisions.tsv`.
