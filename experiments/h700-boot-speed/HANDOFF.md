# Handoff: where the pre-Linux time goes, and the counter-stamp measurement

**Stopped at the user's request on 2026-10-15. Read
[PROJECT-HANDOFF.md](PROJECT-HANDOFF.md) first.** It supersedes the in-progress
checkpoint below, records exact restored lab state, all current source/proof
locations and the unfinished logo/Step 2 integration. Active code is already in
the main local checkout on `uboot-trace`; the old boot-speed worktree is clean.
The private diagnostic was never booted and RG40's qualified v2 media was restored
without reboot. No background work remains.

## Active checkpoint — read this before the historical notes

**Current priority: restore an early native-loader boot logo.** The user clarified
that a first logo during kernel display initialization would be too late. Read-only
FAT/BMP loading, an A64 DE/TCON/panel helper, and the original-kernel warm
pixel-copy handoff are implemented. The helper runs before the main image read,
selects the panel after power/reset, and authenticates its active-display record
for the A32 loader. Final SP host replay passes the actual helper, cached-panel
producer and both cold/warm kernel consumers; the full boot chain and eleven
model profiles are being qualified under `native-logo-v1/artifacts-v3` and
`native-logo-v1/fleet-v1`. RG40XX V v3 staging passed exact backups/readbacks,
but the first noack trial returned through autonomous stock fallback without a
native witness. The identical v3 capsule/firmware then booted accepted native
with only the FAT artwork name temporarily hidden; its exact directory sector
was restored. This isolates the failure to early display initialization. Follow
the complete vendor display call chain and its prerequisites, as the user
explicitly requested; do not add another guessed register fix. A private owned
RTC-phase diagnostic is being prepared. RG34XX SP returned on stock, passed an
ordinary old-v2 native normalization boot and is quiesced; v3 is not installed
there. Personal RGSP remains untouched on its confirmed working panel-fixed build.
Step 2 hardware comparisons are paused for this display work. Current artifacts
and ownership are recorded in `work/h700-loader/native-logo-v1/design.md`.

Step 1 is committed as `205f677`, with the panel fix in `ad95638`. **The
corrected personal RGSP release is installed and the user confirmed normal Slot.**
Acceptance of the earlier native release remains withdrawn: its stock `lcd_type=old` became `boe`
under native and the physical screen was black. Linux, GPU/DE and Slot checks
missed the panel mismatch. Verified 60 Hz did not restore it. Stock fallback
restored `old`, and the user confirmed: **"Yes, normal Slot screen."**

The corrected release is now installed on personal RGSP, generation
`b875b0e05d7aa35fa88c329db5ed45287499769208fa3edd63ecbe6e6ffc57d5`.
Its single verification boot `c1f941f9-3144-4b73-8956-6721f2287f4e` preserved
`lcd_type=old`, passed production acknowledgment and exact startup/media checks,
and runs the unchanged Slot binary. The user confirmed: **"Yes, normal Slot screen."**
RGSP is now off limits to further agent tests, writes and reboots. Continue
step 2 on the two labs while the user uses RGSP normally.

The corrected code is committed as `ad95638`. Qualification
`work/h700-loader/rgsp-user-install/panel-acceptance-review-v1/release-qualified-v3`
binds all eight fresh panel-aware lab reports and commit `ad95638`, SHA256
`002ed30516288fdb4ad3de0ca95b692bce3742540f8dc5332b3d2b751e6ab4f7`.
The 20-case stock transition restored only the pinned original primary while
holding the old stock latch, verified the untouched secondary, then cleared
only the exact owned latch; it issued no reboot. The ordinary corrected installer
then performed one verification boot. Local journals are
`rgsp-user-install/panel-stock-transition-v2/hardware-v1/run.json` and
`rgsp-user-install/panel-install-v2/install-journal.json`. Compact install proof:
`evidence/rgsp-panel-install-v2.json`. Earlier recovery restored the normal stock
picture with `high_refresh=1`; the refresh preference has not been disabled.

Native omitted PI15 power and PI14 reset with the vendor 30/15/50/100 ms waits,
then used PE6 function0 instead of function6. Pull-up policy also varies by model.
The kernel trusts the command-line panel choice. The new compiled-probe proof
compares actual operations with all eleven original U-Boots. The new health
helper requires exact per-card `/etc/baseos/native-panel` policy and matching
command line before watchdog acknowledgment. Fresh builds, all eleven linked
model proofs and all eight new lab recovery/ordinary-boot/suspend reports pass.
The original kernel, monitor, image and DTB remain byte-exact. The personal
old-panel installation passed its protocol and the user confirmed the picture. Evidence is under
`work/h700-loader/rgsp-user-install/black-screen-v1`,
`work/h700-loader/panel-health-v1` and
`work/h700-loader/rgsp-user-install/panel-acceptance-review-v1`.

The corrected capsules are under `work/h700-loader/panel-release-artifacts-v2`.
Their eight fresh hardware reports are under
`work/h700-loader/panel-persistent-hardware-v2/{rg34xxsp,rg40xxv}` using
`noack`, `native-cycle-1`, `native-cycle-2`, and `sleep`. Every report binds actual
panel command lines to its native/recovery/resume boot IDs. Both labs are BOE;
these reports do not establish physical old-panel coverage. Compact proof:
`evidence/panel-acquisition-fix.json`. Both labs also passed the following native boot. The initial failed noack reports remain
under `noack-first-transient-state`; their native witnesses preserved RTC words,
but the stock route normalized vendor GP2/6. SP also had a dirty FAT flag.
Read-only fsck found no structural errors; backed-up metadata changed only byte65
when the dirty flag was cleared, then read-only fsck passed. Fresh trials from
normal stock state pass every gate without weakening the checks.

Both persistent lab capsules pass deliberate no-acknowledgment recovery,
two ordinary native boots, real suspend/wake and a native boot after waking.
The general fleet release remains open. Resume sequential kernel/library
loading and boot-image A/B after the panel fix, preserving all eleven model
and panel contracts without user configuration.
Only the primary agent operates hardware; other agents work on host artifacts.

**Historical v1 acceptance:** these eight reports qualified the earlier
persistent capsules on BOE lab panels. They do not qualify the corrected panel
probe or establish old-panel coverage. Reports are preserved under
`work/h700-loader/raw-persistent-hardware-v1`:

| Lab | No-ack recovery | Two ordinary boots | Suspend/wake | Following native boot |
| --- | --- | --- | --- | --- |
| RG34XX SP | `noack-health-decline` | `native-cycle-1-v2`, `native-cycle-2` | `sleep` | `native-after-sleep`, boot `c7247c5e-6b54-4592-bb4f-37e87211f39e` |
| RG40XX V | `noack-v2` | `native-cycle-1`, `native-cycle-2` | `sleep` | `native-after-sleep`, boot `a27447a4-717e-4cb4-b6c4-24fcf5b96c37` |

Production health clears the owned attempt pair and stops the watchdog. GPU,
Wi-Fi, audio/input nodes, frontend and nonuniform DE writeback pass; firmware,
secondary bank and environment readbacks remain exact. These checks establish
per-card functional acceptance, not physical-panel observation, cold-button
timing or all-eleven-model hardware qualification. Factory SP LPDDR4 training,
charger entry and the general fleet image pipeline remain open.

The failed reports remain failed. The first RG40 preflight refused a trial
modified environment before writes; the successful rerun uses its exact
original inventory environment. SP's first no-ack test started NextUI before
its intentional watchdog reset, interrupting a FAT recent-list write. Root
preserved both FATs and the 305-byte recent list before repair. A mistaken
`fuser -km` call on an already-unmounted path killed userspace services; no
repair or firmware write followed that failed attempt. The user's power cycle
restored connectivity. Scoped process selection replaced the broad kill, with
14 safety cases, and the backed-up FAT repair passes read-only fsck with the
recent list restored byte-exact. Evidence is `rg34xxsp/noack/fat-repair`.

The corrected SP no-ack fixture exercises the actual production startup gate:
the real helper first confirms readiness, then a temporary wrapper declines
acknowledgment before FAT/frontend startup. Its persisted native witness shows
the exact owned pair and CFG1/MODEb1; autonomous stock recovery returns a healthy
NextUI and clean FAT. The production helper is restored afterward. Use
`noack-health-decline/run.json`, preserving the original failed `noack/run.json`.

Lab reboot now remounts the frontend card read-only before reset and restores
owned mount changes on refusal. SP NextUI keeps writable log descriptors, so
its scoped `work/h700-loader/quiesce-sp-nextui.py` must run before lab reboots.
Never use filesystem-wide process killing to quiesce a card.

The historical install-v5 installer was `work/h700-loader/install-rgsp-release.py`, SHA256
`e36dd337675055a3c357e85a6fcc1be6e88f43185e9f716c7a4aa3e8c6053e18`.
Its 106 host cases include typed actual sleep reports, shared SSH
connection handling and scoped frontend refusal recovery. `qualify-rgsp-daily.py` pins all eight actual lab reports,
all inputs and the source commit, then invokes the real installer preflight
before emitting a deployable qualification. The successful qualification is
`rgsp-user-install/release-qualified-v4`, SHA256
`4d809c46ed73e1ef395f3af1b7fac6bcbd8be78b0c1a246e217493c01042b483`.

Earlier personal install attempts are retained as failures under
`rgsp-user-install/install-v1` through `install-v4`. The first failed before
writes at hostname resolution. A later staging failure needed exact manual
completion of rollback. The next two completed original-byte rollback in their
original boot. None completed a reboot. Read-only census reproduced one failed
fresh password login in six, versus six successful shared-connection requests.
The current transport uses an explicit task-owned SSH master, preserves the
pinned `rgsp.local` host key for its numeric endpoint, retries only connection
authentication, and never retries a remote write or reboot command.

The last attempt safely refused reboot because Slot held writable stdout and
stderr on `Logs/slot.log`. The new scoped handoff temporarily removes only the
frontend respawn action, stops only the exact Slot process, remounts FAT read-only
and restores the original init file before reboot. Slot Services and Wi-Fi stay
running. That exact method passed on RG40 with no reboot, restored card/init
state, a new healthy Slot PID and 100% matching nonuniform DE writeback.
Evidence: `rgsp-user-install/slot-quiesce-rg40-v1`. Thirteen separate host cases
include four actual generated signal-shell cases. The following `install-v5`
completed one verification boot but missed the panel mismatch. Compact
proof is `evidence/rgsp-native-daily-install.json`; the complete local journal is
`work/h700-loader/rgsp-user-install/install-v5/install-journal.json`. The installer backs up before writes,
verifies every write, preserves Slot and user files, and performs one boot.

### Clean native path and current devices

Both clean combined candidates use ABI2/240 bytes, the vendor PMIC bootreason
contract, automatic DRAM type/SID wiring selection, and vendor SRAM release.
Kernel and monitor bytes remain exact. The failed six DE/CCU writes and all
kernel/FDT display diagnostics are removed.

| Lab | Bundle / SPL below `work/h700-loader` | Initial clean qualification boot ID | One warm frontend exec / settled framebuffer |
| --- | --- | --- | --- |
| RG34XX SP | `native-loader-display-release-clean-v1` / `mainline/native-preload-display-release-clean-v1` | `0f5dd8fb-1b81-4b6f-bb77-a8b589e62792` | 3.111 s / 5.579 s |
| RG40XX V | `rg40xxv/native-loader-display-release-clean-v1` / `mainline/rg40-native-preload-display-release-clean-v1` | `18046484-6e2f-4552-b598-102903a2774e` | 2.715 s / 4.140 s |

These are single diagnostic runs, not paired release benchmarks or physical
button-to-picture measurements. Both displays have nonuniform DE writeback
matching the active framebuffer 100% on the initial capture. SP runs NextUI;
RG40 runs the user's Slot fork with an empty library/status-bar screen.
Evidence is `green-screen-v1/display-release-clean-v1` and
`rg40xxv/display-release-clean-v1` under `work/h700-loader`.

Both exact SPLs passed scratch restoration, primary restoration and real warm
DRAM retraining before BROM: SP 2 GiB LPDDR4 and RG40 1 GiB LPDDR3, both map1.
Their `display-release-clean-{scratch,primary,train}-v1` wrapper directories
contain the actual run files. Each BROM trial restored stock boot0 before DRAM;
the cards contained stock first stages during that qualification. Both then passed two normal
stock boots and forced-secondary recovery through the corrected no-boot0-write recovery TOCs under
`work/h700-loader/recovery-toc-no-boot0-writes-v2/{model}/stock-hardware-control`;
their run.json files contain the live IDs. Owned benchmark/factory trace words
were cleared with expected-value pair checks. RTC wake alarms remain empty.
Do not touch vendor retention GP words.

Five fresh alternating warm pairs per lab now pass the output guard on all
20 boots, with zero failures. SP frontend exec5.811 →2.913 s (native
2.859–2.945), settled framebuffer8.701 →5.723 s (native5.611–5.793). RG40
frontend exec4.181 →2.765 s (native2.709–2.858), settled framebuffer5.632 →4.211 s
(native4.156–4.297). Evidence: `work/h700-loader/display-clean-matched-v1` and
`work/h700-loader/rg40xxv/display-clean-matched-v1`, each `comparison.json`.
Both reports preserve per-run timings, software/firmware pins, device load and
DE pixels. These qualify the clean restore-first path; repeat final persistent
release timing after functional qualification. Earlier green-screen runs remain
historical observations, with visible-menu acceptance withdrawn.

### Display cause and promoted fix

The original-kernel pre-SMC witness measured SYSCTRL0 `7fffffff`, SYSCTRL+4
`01000000`. Native left SRAM borrowed when Linux initialized DE; vendor U-Boot
releases it first. The isolated release candidate produced exact stock pixels
on SP. Early garbage DE readbacks did not establish successful programming or
direct MMIO-to-SPL-memory aliasing.

Shared `native/loader.c` now performs the exact vendor prefix after successful
kernel preparation: `SYSCTRL0 &= 80000000`, `SYSCTRL+4 &= feffffff`, then DSB SY.
It leaves DE and CCU initialization to Linux. The five-file promotion and fresh
proofs are in `work/h700-loader/display-release-promotion-v1`. The portable
`native/verify-sram-release.py` compares actual compiled writes against all
eleven original U-Boots. Both clean loader/cross/handoff proofs pass; four
invalid binary mutations reject. Private absolute-path changes were not promoted.

### Fleet DRAM and power coverage

`spl/verify-auto-dram.py` tests every bank as winner by index, both SID wiring
maps and three earlier-bank failure modes. All eleven prepared full-native
builds plus both distinct live parameter sets pass **1,377 cases**. RG28's DDR3
bank runs at 792 MHz. Evidence: `/tmp/h700-fleet-auto-dram-v1/fleet-proof-report.json`.
Old A32 all-eleven proofs cover retries/timings/protocol with compile-time PHY
tables; they do not establish this combined automatic-map coverage.

Factory SP banks differ electrically from its trained live primary. A separate
host-qualified fixture uses prepared `[LP3, LP3, LP4]` banks while preserving
the exact live stock64K restore source and a distinct generation. Hardware
training failed safely: SPL `mainline/fleet-auto-dram-v1/sp-prepared-live-restore`,
bundle `/tmp/h700-fleet-auto-dram-v1/sp-prepared-live-restore-native`, wrappers
`/tmp/h700-fleet-auto-dram-v1/sp-warm-{scratch,primary,dram}`. Full pins and model
boundaries are in `sp-fixture-proof-summary.json`. Scratch and primary controls
passed; actual training remained at stage22 until watchdog stock recovery,
boot `bf784e20-e867-4c92-9014-1bc0632dcbdf`. Evidence:
`work/h700-loader/factory-sp-hardware-v1`. No bank outcome can be inferred from
the result fields, which are filled only after training returns. Upstream PHY
read-calibration has two unbounded polls; a private bounded/RTC-traced fixture
at `/tmp/h700-sp-factory-retry-v2` passes31 linked timeout/response cases and
all named handoff proofs. Both v2 hardware restore controls passed, but training
again failed safely: boot `b249a828-f127-4ef4-a078-0ea373a53808`, GP14=`4253d002`
proves LP4 bank2 was reached and GP15=`047fb324` records its last wait address.
That address does not establish where execution finally failed. Evidence:
`work/h700-loader/factory-sp-hardware-v2`. Generic prepared-SP LP4 selection
remains unresolved; the live calibrated SP path and RG40 LP3 path still pass.
RGSP is independently type7/1GiB with its own pinned inventory. Do not transplant one board's trained values across
the fleet. DDR3/map0 physical training remains unobserved;
no prepared bank requests the unsupported DDR3 `tpr13 bit7` special map.
Non-power-of-two detected capacities are also unsupported by the normalizer.

Dynamic PMIC bootreason preserves the actual vendor classification. Its native/
vendor proof passes 5,731 cases. Clean combined SPL hardware boots exercise the
unknown/warm-restart case; charger/power-button hardware acceptance remains open.

### Sleep and persistent recovery

Guarded RTC1 wake tests pass on RG40 stock, SP native and RG40 native, retaining
the same Linux boot ID, GPU, Wi-Fi, frontend and valid DE output. Evidence is
`work/h700-loader/resume-entry-census-v1/{rg40-stock-rtc-v1,sp-native-clean-rtc-v1,rg40-native-clean-rtc-v1}`.
The helper observed its owned watchdog state after wake and restored the prior
state; each own eight-second RTC alarm was consumed/cleared. Exact Boot ROM
instructions subsequently proved that reset clears watchdog mode bit0, so
post-wake readback does **not** prove continuous watchdog protection through
suspend/reset. These tests use
stock boot0 on card and **do not qualify persistent native-first-stage resume**.

Read-only SRAM snapshots work through mmap. SP contains native SPL before
suspend and exact stock boot0 in its first32KiB afterward. The native Linux
boot ID is unchanged. Actual warm-monitor traces write no SRAM before Linux
ERET, placing stock materialization earlier in wake. Exact ROM reset replay
ignores RTC1f8/1fc and follows its SD loader; a separate CPUCFG soft-entry magic
can bypass media and remains a distinct path to audit. Upper SRAM at `28000` becomes invisible/zero under
DE ownership, so full64KiB hashes cannot establish full stock materialization.
Both actual kernel suspend_ops pointers select the PSCI SYSTEM_SUSPEND tables.
The correct global PA is `41167828`; earlier reads at `41667828` were an address
translation error and are retained with correction sidecars. Exact dispatch,
monitor and snapshot evidence: `/tmp/h700-sram-resume-discriminator`.

The persistent protocol, generation binding and Linux health helper are now
active on both lab cards. The following rejected prototypes are historical. The old A64 RMR2 resume prototype is
architecturally invalid. The corrected A32 selected-card reader passed normal
BROM controls but failed its actual reader test; automatic stock restoration
succeeded (`a32-reader-lab-brom-v3`). An embedded compressed native/stock A32
stage0 fits SP within64KiB with300B aligned margin and passes39 compiled cases
plus2 inner-FEL comparisons. It is host-only; real resume entry, SRAM ownership,
early-decode refusal recovery and fleet native-size margins remain open.
Evidence: `/tmp/h700-embedded-stage0-latest`. No outer warm trial was built.

Exact 64KiB ROM dumps from both labs, twice each, are identical SHA256
`da649502bc3fc09fcbde1d50beaaddbf0e9390e0c7fcaacb86fac399a9188a1a`.
Actual ROM code accepts 128KiB images into `20000..3ffff`, below its active
stack, and retries a checksum-invalid primary at LBA16 using LBA512. The raw
native/stock capsule review removes the earlier 64KiB compression constraint.
Host ROM proof: `/tmp/h700-brom-media-v1/REPORT.md`; reset/resume proof:
`/tmp/h700-brom-resume-v1`. Native48KiB→stock64KiB header byte-prefix cut18 can
produce length0 and prevent fallback; no arbitrary torn-sector guarantee exists.

Both real checksum-only fallback controls reached healthy stock Linux with
SRAM header+28=10, confirming secondary selection. RG40 secondary stayed byte
exact; SP secondary's calibration/checksum words changed, so its immutability
assertion failed. Evidence: `work/h700-loader/brom-secondary-hardware-v1`, with
SP `run-corrected.json` preserving that distinction. Actual vendor U-Boot replay
also proves it can rewrite both banks at the declared image length. A raw
capsule therefore requires a qualified recovery-U-Boot header-update suppression
before either bank can be treated as immutable. Investigation:
`/tmp/h700-raw-capsule-audit`.

The corrected portable `native/prepare-recovery-toc.py` now validates and updates
both nested U-Boot and TOC checksums. Its first version omitted the nested
checksum and was rejected before device writes; keep v1 candidates/reports as
rejected. Current tool SHAe8279e8d and sealed audit88360052 pass all11 CLI outputs,
six negative inputs and44 actual caller cases. New recovery TOCs SP2bab1b40 and
RG409daec386 passed two normal stock boots **and** actual checksum-fault secondary
boot on both labs, with both full boot0 banks byte-exact and correct DE output.
Evidence: `work/h700-loader/recovery-toc-no-boot0-writes-v2`. The stock qualification Linux
IDs were SP`ba2fed17-41d3-4f82-9d58-9a9b184765c8`, RG40`abb644bf-7a25-4c1e-b133-c28c968180ff`.

The earlier raw outer prototypes and adopt-only inner builds were host-only:
`/tmp/h700-raw-capsule-v1`, `/tmp/h700-raw-inner-v1`. The inner validates the
outer-owned RTC pair/watchdog, compiles out the old journal/lab/RMR2 path and
SPL_MMC_WRITE, embeds pinned board8, and reads only firmware/image. Actual linked
PIO/loader/monitor chains passed for SP and RG40; the later sealed release includes RGSP's own chain.
Private warm outer entry-stop controls are under `/tmp/h700-raw-warm-v1`; their
launcher must arm its watchdog **after** BROM, not rely on a Linux-prearmed
watchdog surviving reset. All four physical warm entry-stop controls now pass
with exact success pairs, valid DE output and complete cleanup:
`work/h700-loader/raw-warm-hardware-v1/{rg34xxsp,rg40xxv}/{native,stock}`.

Fresh builds from the promoted repository sources are now frozen under
`work/h700-loader/raw-release-artifacts-v1/{rg34xxsp,rg40xxv,rgsp}`
with selected SPLs under `mainline/{model}`. The original temporary archive was
`/tmp/h700-raw-release-v1`. All130 selected source,
input, artifact and proof pins verify. Each complete host chain passes17
adoption cases,3 actual PIO/load cases,36 outer cases and2 A32 entry paths.
These replace the private-path prototypes. Their build-time manifests remain
explicitly ineligible: only the separately pinned aggregate of actual passed
hardware reports can authorize a per-card installation. The RGSP generation
is47ed21b125f3c2f3df7b017c573016ddca2b152d57fc531ff6ff51ed8f9662b0.

Strict adopt-only BROM use required an actual pair/watchdog retention check
across the inner's early A32→A64 RMR3. Request-only emulation could not establish
this. The restore-first witness at `/tmp/h700-early-rmr-retention-v1` completed
that gate as recorded below.

The exact pre-own-arm witness now passed both real warm scratch and primary
restoration controls under `/tmp/h700-early-rmr-retention-v1/warm-*`.
At that checkpoint SP stock boot was`d3273afc-c166-4f3e-8346-69ad7b278cd4`.
Its primary was exact pinned stock2f1745f9; secondary remained365d5690
(same executable, distinct calibration header), and recovery TOC remains2bab1b40.
The physical128KiB BROM retention probe subsequently passed:
`work/h700-loader/early-rmr-retention-hardware-v1/run.json` captures the exact
outer-owned pair, WDT CFG1/MODEb1 and CurrentEL12 before the A64 witness's own
arm. Primary stock2f1745f9 was restored, secondary365d5690 and recovery TOC2bab
stayed exact, and healthy stock Linux returned as
`58fac7ee-750c-439d-ba73-8db9bbe5bc22`. The completed witness pair was cleared
with an expected-value check; all vendor GP words were preserved. The warm
controls' purpose-specific root runner is
`work/h700-loader/run-raw-warm-control.py`.

Production health build/rootfs installation and reviewed rcS hooks are now
integrated, with canonical protocol `src/native-attempt.h`. The actual static
A64 helper, ten startup cases, legacy charger/poweroff and protocol tests pass:
`/tmp/h700-health-integration-v1/integration-report.json`. No generation/helper
had been deployed at that checkpoint. The helper, exact raw32 generation and
update/migration protections were staged on both labs, with acknowledgment
hooks deliberately absent for the first no-acknowledgment recovery trial.
Production hooks and acknowledgment now pass on both as recorded above.
Reports are`work/h700-loader/raw-persistent-hardware-v1/{model}/policy-noack/report.json`.
RGSP remains unchanged. A complete production tools build subsequently passed and
refreshed its verified stamp, preserving the same helper binary SHA007d28ed;
evidence `work/h700-loader/release-build-v1/tools-build-report.json`. Rootfs
assembly remains pending. The migrator now skips all boot-area access whenever
installed native policy exists, including stock fallback without native FDT;
37 real BusyBox migration tests pass. Rootfs update compatibility is now
implemented: `mkupdate.py` reads the actual ext4 raw32 generation and helper
inode through debugfs; the updater refuses native/legacy cross-installs and
malformed/mismatched installed/payload/FDT generations before writes. Full
updater suite, nine real ext4 CLI methods and the production build-update Docker
wrapper fixture pass; report `/tmp/h700-update-generation-guard/report.json`,
seal5812ff61. Archive members remain manifest/rootfs.img.gz. Build/image/
update design is `/tmp/h700-production-integration-audit/design.md`. Both lab activations and all eight required per-card acceptance reports now
pass. The aggregate installer preflight must still pass against the committed
sources before RGSP installation. Generic fleet rootfs assembly remains open.

### Proof gate and next work

`spl/qualify.py` now checks named reset/handoff/loader/monitor/cache/PLL proofs,
strict ABI2 SRAM/PMIC contracts and imported-checker hash stability before device
access. Reset-only reports cannot qualify a handoff. Both clean runs have fresh
`qualification-after-checker-freeze.json` sidecars using checker `69d32bfe...`.
The original RG40 preflight overlapped checker development; preserve it and use
the fresh strict sidecar. New benchmark runs require valid DE writeback pixels.

The per-card native chain is committed, qualified and installed on RGSP. Begin
the sequential kernel/initramfs/library-image and boot-image A/B work on labs.
Host inspection proves all eleven vendor kernels support initramfs, loop and
LZ4 squashfs. Two current whole-library bundles plus stock recovery exceed the
64 MiB boot partition. `sequential/build-fixture.py` and its sixteen passing
shell cases prepare a same-byte disk-versus-RAM library comparison before
choosing storage. Preserve rootfs slots, recovery and update binding. Details:
`work/h700-loader/sequential-v1/boot-contract.md`. Factory-bank hardware selection, charger
and physical cold-boot coverage, repeated persistent-release timing and generic
fleet image assembly remain separate open gates. Kernel rebuilding is last. RGSP's
read-only inventory/backups are `work/h700-loader/rgsp-user-install/inventory-v1`;
perform no further agent access or tests there after the completed installation.
Canonical decisions are append-only in `work/h700-loader/decisions.tsv`.
Historical sections below must not override this checkpoint.

## Release blocker and new install request, 2026-10-15

The final native RG34XX SP boot displayed a green physical panel, reported by
the user. A framebuffer dump contains the correct NextUI menu, so previous
GPU/framebuffer/process health checks missed this failure. The stock reboot
restored the normal visible menu (user confirmed). Native display manager
reports iommu[0], stock iommu[1], with the same virtual layer address ff800000;
the causal path is under investigation. Evidence: work/h700-loader/green-screen-v1.
Native timings remain measured (five warm pairs: frontend5.812→2.959s,
settled framebuffer8.640→5.759s), but visible-display acceptance is withdrawn.
Do not install this bring-up build as a release.

The user requests a persistent release install on RGSP over root@rgsp.local,
password root, for normal power cycling and Slot gaming, followed by no further
agent testing on that device. Read-only SSH inventory confirms BaseOS1.4.0,
targetrgsp, serialac001089c89588720d2, stock bootreasonbutton/lcd_typeold.
The RGSP remains unchanged. Prepare and qualify release recovery/display/power
behavior on lab devices first; the requested install is still outstanding.

## Current checkpoint: native Linux diagnosis, 2026-10-15

The historical notes below describe earlier checkpoints. Nothing is committed
yet. The repeated working fast path still retains vendor boot0: matched
frontend launch **5.868 → 5.213 s**, settled menu **8.739 → 8.035 s**, five
alternating warm restart pairs. This is not a native-SPL benchmark or a
physical power-button cold-boot measurement.

Native SPL reaches payload complete at **0.954 s** and monitor handoff at
**0.956 s**. Native Linux has called PSCI CPU_ON (v12 / BROM trial v6), but
has not completed a boot. These are partial checkpoints, not a total boot time.
The estimated total if the remaining fault were fixed is 2.6–2.9 s to frontend
launch, based on the measured 1.46 s Linux-to-frontend segment plus estimated
decode/entry time. It is not an achieved result.

Post-clean kernel/FDT byte checks pass on hardware. Early fault vectors do
not record an exception before Linux replaces them. A `maxcpus=1` diagnostic
also fails (native-loader-v13 / brom-native-loader-v7), so secondary CPU
bring-up alone cannot explain the failure. Actual monitor instructions already
configure timer access, SMPEN and GIC groups/interfaces; speculative copies
of vendor initialization have not been added.

Recovering the previous printk ring after reset does not work: its addresses
are verified in live Linux, but a first-U-Boot-entry census already finds the
old ring erased. The temporary log package was restored to the known normal
TOC, SHA256 f4f9ba393d37f16df73c1055897c45a04c810732f7dab1f3b24a14b5d77b665c.
The precise destructive operation before U-Boot is not established.

Latest diagnostic: native-loader-v15 with mainline/native-preload-v10. It
records do_one_initcall function pointers in RTC GP14 through PSCI_VERSION,
and ordinary PSCI IDs in GP15. Host proof binds exact generated binaries,
preserves all registers/NZCV and the displaced stack prologue in 32 cases,
and executes four actual-monitor scenarios. Original monitor ERET is intact.
The restore-first BROM trial v8 reached **vin_init**, PA0x40d668d8, initcall
level4s, after SMP and several main initcall levels. GP14=0x08d668d8 and
GP15=0xc4000003. Entry does not prove the function failed or completed. Native
userspace still failed; watchdog recovery returned healthy stock Linux in
29.39 s with exact boot0/environment and no host restore. Both same-SPL
hardware controls and full preload/loader/monitor proofs passed. Evidence:
work/h700-loader/brom-native-loader-v8/run.json and initcall-resolution.json.
Both temporary RTC words were restored to their original zero with expected
current-value checks and readback (rtc-cleanup.txt). This tracing is not a
persistent RTC recovery protocol.

**Native Linux now boots:** native-loader-v16 / native-preload-v11,
brom-native-loader-v9. Restoring boot0's nine media PLL power-bit31 enables
fixes the failure. Exact CSI clock code sets output gate27 and programs rates,
but assumes power31 is already enabled. Without power31 its lock poll times
out; with it the same code succeeds. Compiled SPL/vendor RMW comparison
passes 262 cases and all 11 prepared vendor banks. No PLL rates or other bits
are changed. The new hardware trial passed native-generation, four-CPU,
GPU/Wi-Fi/framebuffer/frontend checks; boot ID82b1e116-c1e0-4586-a938-cdd9bcabde53.

This diagnostic build takes 9.650 s before Linux and 11.204 s to frontend.
Its seven native timestamps end at 1.317606 s before final cleanup/handoff;
the expensive post-clean checksum probes are still present. These numbers
are **not** the clean-loader speed result. The clean build below removes all
temporary kernel/monitor probes and keeps the original vendor binaries.
The stock recovery kernel/monitor have been booted again and GP14/15 restored
to zero (v9/rtc-cleanup-final.json). An attempted live native clock snapshot
failed because its /tmp helper was absent; native-clocks-failed.txt is not
clock evidence. native-runtime.txt does prove four online CPUs and timer IRQs.

Native and vendor DRAM master access masks and all 40 QoS writes match, so
those were not changed. Durable installation/recovery and a clean repeated
native benchmark still remain before the requested commit/install milestone.

Clean qualification passes: native-loader-clean-v2 (generation9823b653...,
loader15872bytes), native-preload-v12, brom-native-clean-v1. Original kernel
SHA1332da28... and monitor SHAba5a3412... are byte-identical; no kernel hooks,
A64 checksum trampoline, or monitor hooks remain. One warm run: pre-Linux
1.449s, frontend2.913s, sampled settled framebuffer5.719s; all health checks
passed. Native timestamps end at1.273568s, so final cleanup/monitor/early Linux
adds about0.175s. This is one qualification run, not a repeated benchmark.

The first matched comparison (native-comparison-v1) stopped after a healthy
baseline5.886s because its full-boot0-SHA guard rejected stock's documented
per-boot training-header rewrite. Only checksum0xc and calibration words
0xa0/0xa8/0xac changed; executable body, checksum validity, environment, TOC
and gzip image remain exact. Keep this failed validation evidence. The next
runner permits only those words and records their values before restarting
all five pairs; it must not silently drop the stopped attempt.

Sleep ABI correction: the preliminary first-entry-only monitor harness found
a NULL FDT, but it had skipped the final8000ff04 kernel handoff. That existing
call registers44000000 in both monitor families; Linux reserves this original
FDT. No extra ARISC registration call is needed. Actual first entry + kernel
SMC + SYSTEM_SUSPEND reaches the vendor SRAM standby code in emulation,
preserving GP14/15. Ordinary CPU idle resumes through monitor RVBAR and bypasses
SPL. A deeper PMIC-off standby path uses RTC1f8/1fc/signature and needs the
stock boot0 resume dispatch studied before durable installation.

The RG34XX SP is healthy on stock recovery Linux; native trials restore its
stock boot0 before DRAM training and watchdog failures recover automatically.
The user is unavailable for manual recovery. The RG40XX V remains healthy,
but it has not completed a native Linux boot. RG34XX SP has. All 11 model families
and DRAM/panel variants remain required; modeled coverage is not hardware
acceptance. Durable recovery, PMIC boot reasons, suspend and cold boots remain
open. Complete and benchmark step 1, commit/install it, then pursue the
contiguous RAM library/blob and A/B work in step 2.

Written 2026-10-15 for whoever continues. Nothing in this worktree is committed
yet (branch `uboot-trace`, worktree `work/uboot-trace`). The `work` entry in
the worktree is a symlink to `../boot-speed/work`, which holds the prepared
firmware of all eleven targets and the lab backups; it is excluded from git.

## The question

BaseOS 1.4.0 boots to the frontend handoff in about 4.1 s on the RG SP and
5.8 s on the RG34XX SP. The kernel-to-handoff part is 1.4 s on both. The rest
is before Linux: 2.7 s and 4.4 s from the counter origin to the kernel's clock.
The user wants to know how that time splits, on every model, to decide whether
BaseOS should replace the vendor U-Boot with its own loader (keeping boot0 and
the monitor so that DRAM and panel detection stay stock; see
`variant-survey/README.md`).

## What is established

- Films at 240 fps: the logo appears 0.54 s (RG SP) and 0.66 s (RG34XX SP)
  after the power LED. The emulation shows the backlight comes on 0.51 s after
  U-Boot's display init, so the LED is most likely switched on by U-Boot's PMIC
  init, not at power-on; the film does not see boot0 or DRAM training.
- The USB cable changes nothing (no-cable boots logged the same pre-kernel).
- The RG SP and RG34XX SP U-Boot items are the same program; only the build
  date string differs (`uboot-trace-notes/delays/diff.md`). Environment
  partitions are identical. Device trees differ only in ADC, keys and battery
  capacity, which U-Boot does not read.
- Under emulation (`uboot-trace.py`, all eleven builds, both strap values,
  `evidence/uboot-trace-notes.md`): from display init to the kernel jump U-Boot
  spends about 1.04 s of modelled time: 0.51 s panel sequence (285 to 465 ms of
  busy delays in interrupt context, which block the bitmap load), 17 ms of
  vendor post-logo steps, 363 ms card read of the 7.2 MB boot image at an
  assumed 20 MB/s, 265 ms gunzip at an assumed 1 GHz. The shape is the same on
  every build. Panel delays are the only per-build variable.
- So on hardware roughly 1.7 s (RG SP) and 3.2 s (RG34XX SP) between the logo
  and the kernel clock are outside what the model can see: the card's real
  speed under U-Boot's driver, the CPU clock U-Boot runs at, the monitor call
  and early kernel, and on the RG34XX SP something more. The RG34XX SP reads
  its panel strap low (`lcd_type=boe`, 465 ms table) where the RG SP reads it
  high (285 ms): 180 ms of its gap, before the logo. Its PMIC readings are
  healthy and the vendor battery check takes the fast path
  (`uboot-trace-notes/battery/eval_regs.py` on `work/rg34xxsp-live/`).

## The measurement in progress: counter stamps in U-Boot

A patched boot package for the rg34xxsp target records the 24 MHz generic
counter at six points of the vendor U-Boot flow (stubs placed in the dead help
text of `sunxi_nand_test`; call sites redirected; checksums refreshed):

| slot | where | meaning |
|---|---|---|
| 0 | 0x2B22 | logo routine returned |
| 1 | 0xF13E | vendor post-logo steps done, main_loop entered |
| 2 | 0xC1FC | card read of the boot image starts |
| 3 | 0x7E80 | card read done, bootm starts |
| 4 | 0x152A | kernel unpacked, boot_prep_linux |
| 5 | boot_jump_linux | about to jump |

Together with the boot log's `pre-kernel` figure (counter at the kernel's
clock origin) this splits the time into: vendor steps, card read, gunzip,
monitor plus early kernel. The LED-to-logo film and slot 0 give the pre-logo
part. Files: `work/rg34xxsp-stamp/` (build-stamped.py, verify-stamped.py,
stamps.json, NOTES.md, read-stamps.py, the packages). Copy the scripts into
`experiments/h700-boot-speed/uboot-stamp/` before committing; the .bin files
are derived.

State on 2026-10-15 evening: v1 (`boot-package-stamped.bin`, stamps stored at
0x40000100) is on the RG34XX SP's card and boots normally, but the kernel
reuses that page before it can be read, so the stamps were unreadable. A v2
was requested from the build agent: the final stub runs after the FDT copy to
0x44000000 and copies the stamp block (magic 0x5354414D plus six 64-bit
stamps) into the last 64 bytes of the device tree, which the kernel reserves
and exposes at `/sys/firmware/fdt`. `stamp-run.py read` already expects that
layout.

### How to run it (RG34XX SP, adb serial 1c00103dc2840671ed3)

The lab tooling lives in the boot-speed worktree. From `work/uboot-trace`:

    set -a; . ../boot-speed/work/lab/rg34sp.env; set +a
    /tmp/uboot-venv/bin/python experiments/h700-boot-speed/stamp-run.py install [boot-package-stamped-v2.bin]
    /tmp/uboot-venv/bin/python experiments/h700-boot-speed/stamp-run.py restart
    /tmp/uboot-venv/bin/python experiments/h700-boot-speed/stamp-run.py read
    /tmp/uboot-venv/bin/python experiments/h700-boot-speed/stamp-run.py restore   # the build's own package back
    /tmp/uboot-venv/bin/python experiments/h700-boot-speed/stamp-run.py restart

`install` refuses unless the card holds the build's package or a known stamped
one; writes are read back and verified. `restart` is the lab's safe path
(pause the frontend, remount read-only, run reboot_next); never plain `reboot`.
The device sometimes drops off adb for no visible reason; a power cycle brings
it back. If `/tmp/uboot-venv` is gone: `python3 -m venv` and install
`experiments/rgsp-kernel-gzip/requirements.txt`.

If the device stops booting after a write: the boot package has no second copy.
Restore `work/rg34xxsp/boot-gzip/boot-package.bin` at byte offset 16793600 of
the card from a card reader (the lab's `fix-card.py` in the boot-speed worktree,
or dd), length 1310720.

### What to do with the numbers

Report the split per bucket and compare with the emulation's assumptions: card
bytes per second under U-Boot (7.25 MB over slot 2 to 3), gunzip time (slot 3
to 4, 17.7 MB unpacked), monitor plus early kernel (slot 5 to the kernel clock
origin), vendor steps (slot 0 to 1, expected about 17 ms plus panel interrupt
stalls). The RG SP was in use by another agent on 2026-10-15; the same package
built for rgsp (identical U-Boot, so the same patch applies; rebuild from
`work/rgsp/boot-gzip/boot-package.bin` with build-stamped.py) would give the
RG SP's split for comparison.

## Other artifacts in this worktree

- `uboot-trace.py`: the tracer (phases init, pre, late, main; models listed in
  its MODELLED string). About 75 s per target.
- `uboot-trace-notes/`: routine map of the rgsp U-Boot, the SD driver
  specification, the per-build delay inventory, the battery-check evaluator,
  the analysis scripts.
- `variant-survey/`: how boot0 detects DRAM and U-Boot detects the panel, with
  the scripts and their output; the shape of a replacement loader.
- `evidence/uboot-trace-all.json`, `uboot-trace-pe6-low.json`,
  `uboot-trace-notes.md`: the emulation results.

## Open items

1. Read the stamps (v2), on the RG34XX SP and ideally on the RG SP.
2. Write the per-bucket budget into `evidence/` and the architecture
   recommendation: a loader behind boot0 and the monitor that overlaps the
   panel sequence with the kernel read, reads fewer bytes, and avoids gunzip.
3. Commit the worktree (plain-language message; do not push).
4. The 1.5 s RG34XX SP gap is a hardware-bound part of the budget; the stamps
   say which bucket. 180 ms of it is the panel strap.

## Update, end of 2026-10-15: the split is measured, next step is the loader

See `uboot-stamp/RESULTS.md`. On the RG34XX SP the kernel clock starts 4.39 s
after the counter origin; 1.01 s of that is after the logo (0.1 vendor steps
and panel stalls, 0.30 card read at 24 MB/s, 0.39 gunzip, 0.04 prep, 0.17
monitor and early kernel) and 3.38 s before it (boot0, DRAM, U-Boot init,
logo). The pre-logo split between boot0 and U-Boot was not captured (v3
stamps did not surface). The user's decision: stop measuring and build the
replacement, then measure with it.

Loader plan (keeps boot0 and the monitor, replaces only the 32-bit U-Boot item
of the TOC1 package; see `variant-survey/README.md` for what it must do):
1. Entry at 0x4A000000 with the Allwinner `uboot` header so boot0 fills in the
   DRAM block; AArch32 code; stack in SRAM or low DRAM; counter stamp at entry
   (this measures boot0).
2. SD host driver for SMHC0 (spec in `uboot-trace-notes/uboot-mmc-rgsp.md`):
   card is already initialised by boot0? No: boot0 initialises the card to
   load the package but the loader must re-init (CMD0..CMD7, 4-bit, high
   speed) unless it reuses boot0's state; use DMA with large descriptors.
3. Read the kernel in one sequential transfer; store it uncompressed (17.7 MB
   at 24 MB/s is 0.74 s) or LZ4 (about 9 MB, 0.37 s plus 0.1 s decode); gzip
   costs 0.39 s today. Read the DT item from the package.
4. Panel: start the power sequence (power on, 30 ms, init with the PE6
   strap, 50 ms, tcon, 100 ms, backlight) with a hardware timer and overlap
   it with the card read; or leave the panel to the kernel's disp driver and
   draw the splash from rcS (black screen until about 1 s).
5. Command line as the vendor builds it (partitions map, snum from SID,
   bootreason from PMIC reg 0x20, lcd_type from PE6, disp_reserve only with a
   splash), /dram and /memory from the header block, then the monitor call as
   `boot_jump_linux` does (0x14E4 via 0xB58 when the monitor is present).
6. Emulate under Unicorn with the tracer's models before any card write;
   deploy with `stamp-run.py`-style verified writes on the RG34XX SP.
Expected result: kernel clock at about 0.6 s plus boot0 (unknown, measured by
the loader's first stamp) instead of 4.4 s; the same on every model.

## Active continuation: unattended trials

The user authorized firmware trials/restores, recovered the first failed LZ4
trial through a card reader, and said no further manual steps are available.
They subsequently specified this order: complete mainline SPL plus a stripped
loader (including replacing boot0), commit with a detailed message, install
on the RG34XX SP, then pursue a single sequential kernel/initramfs/library
blob. Kernel rebuilding comes last. The retained-boot0 fast path is a proved
checkpoint, **not completion of step 1**. No local commit or push has occurred.

### Benchmarked checkpoint

Five alternating boots per side, identical kernel/rootfs/card and pre-staged
images, changing only one 512-byte environment sector between timed boots:

| Counter-origin time | Gzip/splash baseline | Fast LZ4/no splash |
|---|---:|---:|
| Before Linux, median | 4.370 s | 3.747 s |
| Frontend handoff, median | 5.868 s | 5.213 s |
| Handoff range | 5.791–6.215 s | 5.154–5.424 s |
| Settled NextUI menu, median | 8.739 s | 8.035 s |
| Menu range | 8.600–9.916 s | 7.862–9.270 s |

The handoff improves 0.655 s (11.2%); all ten boots passed health checks. The
first pair was slower on both sides and is retained. A framebuffer snapshot
shows the actual NextUI menu. The previous 1.4.0 benchmark was 5.793 s to
handoff and used Slot on TF2; its first-frame result is not directly comparable
to current NextUI on TF1. Evidence: evidence/retained-boot0-fast-path.json and
work/h700-loader/comparison.json. The target of 1 s or 25% is not yet met.

True-entry v5 stamps put vendor U-Boot entry at 2.612 s, kernel origin 4.377 s,
and handoff 5.760 s in one instrumented boot. Thus retaining boot0 cannot reach
2.5 s total. Old pre-logo scratch stamps were overwritten by the logo.

The checkpoint package is work/h700-loader/final-fast/boot-package.bin, SHA
1d884f4a48a532d2679efa2aff834e553999ba82718fdf5d05a709e8885965dd.
It preserves the stock gzip/splash path unless armed. Fast mode omits the logo,
samples PE6, suppresses uninitialized disp_reserve, and changes runtime HDMI
status from okay to late. An ABI-validated module probes HDMI from rcS.
Static DT, kernel and monitor are unchanged. Reads use exact image extents.

### Recovery and current device

The checked trial writer remains pinned to RG34XX SP serial
1c00103dc2840671ed3. Source:

    . work/boot-speed/work/lab/rg34sp.env

ADB, Docker and Unicorn require escalated execution; sandbox Unicorn JIT dies.
Use lab.reboot(), never plain reboot. Authorization persists; do not ask again.
The primary gzip at p4 start remains intact. One-shot gzip and fast images use
p4+32 MiB (sector 0x5a000) and p4+48 MiB (0x62000). Physical vendor SD APIs work;
standard vendor `mmc dev 0` does not.

Canonical CRC-valid environments differ only in their first 512-byte sector.
Injected code verifies, consumes and reads back the pinned normal sector
before risky work. Watchdog fallback was deliberately tested; RTC scratch is
untouched. Fast Linux trials need host watchdog disarm immediately after ADB
returns. SRAM probes instead request a reset and boot the normal gzip path.
Do not leave a fast default that depends on host watchdog disarm.

Pinned recovery files: work/h700-loader/baseline-package.bin (32f92785...),
baseline-boot-partition.bin (2893dfd4..., exact 64 MiB), baseline-env.bin plus
.sha256. Artifact ledgers refer to immutable files; never rebuild over installed
output directories. The first failed LZ4 binary is kept as an alignment fault
regression: SCTLR.A rejected its unaligned word copy. Current decoder tests
reproduce it and enforce aligned/byte accesses.

Startup has a reversible lab rcS change, SHA
6993878accb91f55f4610a5f04f478d94c9f373474057e99b0b23ae8f4eb4e72:
first-frame sampler and /usr/share/baseos/lab-fast/h700_late_hdmi.ko. The exact
original rcS is backed up under work/h700-loader/startup/. Restore with
fastboot/startup.py restore. /lib/modules is read-only squashfs.

Current device is healthy on normal gzip after a successful automatic DRAM probe:
boot ID b111875a-5839-4b8e-a81b-4ac72ca4f509. Package is the SRAM trial adapter,
SHA 0695cc8c623b78bc4a7810b2965f9f3338d07677b26c2ac5c43d083da3f3fa4c.
It defaults to original gzip; the trial environment is consumed and normal.
Result sector is p4+24 MiB (physical 0x56000); the probe image occupies p4+48 MiB.
Boot0 executable is untouched. Its trained header changes on ordinary boots;
the trial restore source is work/h700-loader/after-dram-boot0.bin. Fresh paired
inventories are work/h700-loader/{rg34xxsp,rg40xxv}-inventory-v1/.

The user connected RG40XX V serial 5c001040f4438852493 and requires automatic
support for all eleven models and their DRAM/panel variants. Its current
firmware is 1.4.0, with about 1 GiB LPDDR3 and a BOE panel. Source
work/boot-speed/work/lab/rg40.env for its read-only lab identity. Its fresh
firmware backups are pinned in rg40xxv-inventory-v1. No new RG40XX V firmware
trial has run. Do not use an RG34XX SP package or bypass serial checks.

### Mainline bring-up

Official U-Boot v2026.10 archive is cached at
work/h700-loader/mainline/u-boot-v2026.10.tar.gz, SHA
f943a88389c8fd70a6b0d061a88267465e143c7ca6ea2b75837ab20f5b60c739.
The generic H700 config and ROCKNIX both support LPDDR4 at 672 MHz. This card
agrees on drive/ODT values; trained TPR6/11/12 vary per boot. The probe uses
values from the recovered live header. Unmodified upstream SPL built to 40 KiB
in work/h700-loader/mainline/u-boot-2026.10/spl/sunxi-spl.bin, SHA
4eb5bffd4d31632dd5afe4b18a6012c62302c62a2f79d60a34def4d22dc7aeda.
Persistent build container: baseos-h700-mainline, pinned h700-toolchain image.

spl/ holds rerunnable SRAM probe builder, actual-instruction verifier, checked
package builder and trial runner. Only upstream C include directives change;
DRAM logic is compiled as AArch32 for testing behind the vendor monitor.
The emulator models SD registers and the DRAM return; it does not prove
training. Real hardware now does:

- SRAM/PIO control passed (sram-control-v4): exact copy, two durable checkpoints,
  SD readback, watchdog reset, normal gzip fallback and all health checks.
- Mainline DRAM initialization passed (sram-dram-v4): reset/retrained controller,
  detected 2147483648 bytes, checked 16 KiB memory, persisted completion and
  returned through watchdog fallback. Counter delta 652026 ticks = 27.168 ms.
  /proc/meminfo reports 2027132 KiB, consistent with 2 GiB board capacity.
  This is one **warm** initialization test, not a cold boot or stress test.

Earlier SRAM controls recovered normally but did not complete. Durable MMU
census found TTBR0 0xbfff0059 and descriptor 0x00000c12: strongly ordered and XN.
The adapter now maps that section normal/executable before jumping. A1 is
**32 KiB**, not the 48 KiB SPL code budget. Borrowed SRAM C begins at 0x28000
and may belong to display/video after vendor init. Current code/data/stack all
fit A1, stack 0x27800. The emulator checks the A1 boundary and executable mapping.
Earlier reporting that 48 KiB was physical A1 size was wrong; corrected in
spl/README.md. Failed artifacts and a boundary regression are retained.

Vendor monitor loads at **0x48000000**, from image header +0x2c. Its default
next stage is fixed AArch32 SVC at 0x4a000000, SPSR 0x1d3. Boot0 copies 32 DRAM
words to monitor +0x30 and an eight-byte board block to +0x24. There is no
separate SCP TOC1 item. Mainline's normal AArch64 ATF BL33 handoff is therefore
not directly compatible; preserve the vendor monitor using an entry adapter.

Native SPL SD re-initialization from SRAM has now passed hardware and emulation.
The hardware control reached EL1, selected 50 MHz/four-bit mode, verified a
64 KiB scratch mirror, then returned to normal gzip. Its SPL SHA256 is
97d69719af8d146fb44602c6b07bc702502653151de7b4ad7314d67b32dc2096.
Build and evidence live in mainline/early-mmc-v2 and spl-mmc-control-v2.
Its BSS and allocator live in SRAM. It calls spl_early_init(), since spl_init()
alone postpones the early allocator until after DRAM stack relocation.

The automatic selector prototype also passed a warm RG34XX SP DRAM trial,
selected LPDDR4 on its first attempt, detected 2 GiB, and checked 16 KiB.
All eleven prepared model banks build and pass instruction-level selection
tests. Training and geometry are modeled there. The counts are 66 retry,
66 timing, and 44 result-protocol cases. See evidence/dram-selection.json and
spl/README.md. RG28XX includes DDR3 as well as LPDDR4. Runtime timing conversion
must use the selected candidate's clock. RG28XX and CubeXX also have different
PE6 token behavior from the other nine models. evidence/compatibility.json
records the per-model contract and both live inventories.

The normal fallback and both benchmark sides report a pre-existing FAT dirty
volume warning. lab.reboot() pauses the frontend and syncs but leaves its FAT
handles open. A guarded remount attempt refused with EBUSY, resumed the frontend,
and did not reboot. That temporary change was removed. No filesystem check or
repair is claimed. Closing those handles safely belongs in final validation.

Next gates: adapt automatic selection to native SPL, prove LPDDR3 on RG40XX V,
implement pre-DRAM first-stage restore, complete vendor monitor/loader handoff,
then run cold SPL trials. A
possible safe trial design is for patched SPL to read a pinned original boot0
from p4 and restore/read back its full 64 KiB **before DRAM init**. Failure then
resets into stock boot0. This is not implemented. Simply chainloading stock
boot0 without first restoring it on card is unsafe: its own trained-header
write could mismatch a new SPL body. Mainline early MMC also needs SRAM BSS/
allocation, since normal SPL BSS is placed in DRAM. Do not write boot0 until
that recovery path is proved.

### Continuation after the status request

The user corrected stopping after a status request. Continue the original work;
status questions do not pause it. No commit or push has occurred.

RG40XX V warm SRAM control and LPDDR3 retraining now passed. Artifacts are
work/h700-loader/rg40xxv/sram-control-v2 and sram-dram-v1, probe
mainline/rg40-probe-auto-v1. It detected 1 GiB, selected type 7 on its first
attempt, and returned to healthy normal OS. Its card has no frontend, so use
run-trial.py --os-only. Do not claim a frontend benchmark there. The first
control recovered but the old runner waited for a nonexistent frontend marker.
The corrected control records boot ID c3190c0a-4031-49b6-8e84-13433b2cc104.
The DRAM run records 4a8fd413-4382-482d-8e61-71eb4bca7104. Both report GPU,
Wi-Fi, framebuffer, audio and input nodes. The RG40 package is its own
d4502381cb8d932bdce798d740a1d48b5f2fb7371f97b20a3e183126d93eb0fb,
normal gzip by default. lab_devices.py pins both serials and original packages.

Native AArch64 selection is integrated. mainline/native-dram-v3 passed a
matched SD control and warm 2 GiB retraining on RG34XX SP. The restore-capable
mainline/native-restore-v1 passed its scratch control and restoration of the
full pinned stock boot0 at sector 16 before DRAM. Its SPL SHA256 is
d40140c21b0084d88de7eb7195ed63892f20f69d00626ffd6e3ae56940db222e.
The primary-restore hardware run is native-primary-restore-v1, boot ID
8495402c-96e5-4074-b685-4da84651b66b.

The first BROM-entry trial then passed, using that identical SPL. It reached
real EL3, restored and verified all 65536 bytes of stock boot0 before DRAM,
and reset into healthy normal gzip without intervention. Evidence is
work/h700-loader/brom-restore-control-v1/run.json, boot ID
066e2fa3-95ad-4dbd-b283-2183b9dbbfaa. Original boot0 is back on card. This
was a system restart through BROM, not physical power-on. It deliberately
reset before Linux, so its recorded stock boot time is not a new benchmark.

Unicorn did not change execution level when PSTATE was set to EL3. Earlier
EL3-labeled runs actually followed EL1. arm64-system.py now explicitly models
CurrentEL, EL3/banked registers and TLB maintenance; corrected verifiers assert
the recorded level. Hardware CurrentEL=3 is independent proof of BROM entry.
verify-brom-entry.py executes both real AArch32 revision branches to the RMR
reset request. RMR and banked SP_irq behavior are modeled there.

Executing the vendor TOC1 loader corrected prior inferred addresses. DTBO goes
at 0x4a100000, DTB at 0x4a200000. Monitor +0xb0 points to DTB, its 32 DRAM words
start at +0x30, and its eight-byte board block is at +0x24. The +0xec block
belongs to the optee path, not this monitor. verify-boot0-load.py proves this
with real instructions in mainline/boot0-contract-v1. verify-monitor.py reaches
the monitor's real return with ELR=0x4a000000 and SPSR=0x1d3. System registers,
GIC and translation effects are modeled; monitor-entry-v6.json is the result.

First intermediate handoff build: mainline/vendor-handoff-v2, SPL SHA256
fd11b90347b2a3e0576cea17affbb73623242f4aaf7285230928f5906a1f68c7.
It adds a journal-gated restore-first PMIC/DRAM/TOC1/vendor-monitor handoff.
Its default control still resets to stock. It uses candidate-specific DCDC3
voltages: LPDDR4 1.1 V and LPDDR3 1.2 V, matching both live regulator readings.
DDR3 uses 1.5 V. The generic plug-in shutdown setting is disabled for lab trials.
Its matched controls passed. The BROM handoff trial reached stage 34 after
restoring stock boot0, initializing PMIC/DRAM and loading TOC1. Linux returned
healthy, but without the SPL marker and with stock-like timing. This did not
prove a candidate boot; brom-vendor-handoff-v2/run.json correctly fails it.
The marker then occupied FDT padding, so its absence alone was inconclusive.

Executing vendor boot0's final reset routine established another part of the
entry contract: it requests a core warm reset into the monitor, rather than
branching directly, and leaves r0=0x48000000, r1=0x08100044, r2=3 and r3 equal
to the selected RVBAR-high address. boot0-contract-v2 records both revision
branches; monitor-entry-v7 executes BL31 with these arguments to its AArch32
BL33 return. System register/reset behavior is modeled in these proofs.

mainline/vendor-handoff-v4 matches that reset contract. SPL SHA256 is
7f49b37c9683feab6636281fc061d48a30f985e6f02c17a71968be5dc8dd4296.
It inserts a real /chosen/baseos,spl property, with preservation/idempotence
checks on all eleven DTBs, and uses aligned word loads for TOC hashing.
Its SRAM restore, native DRAM selection, eGON entry and full monitor-handoff
instruction proofs pass. Matched hardware controls are next. The hardware
runner records watchdog state and host recovery duration to distinguish a
candidate boot from a watchdog fallback. Stock boot0 is restored before DRAM
on every BROM trial; the current default remains original gzip.

Only after this intermediate monitor contract is proved, replace vendor
U-Boot with the stripped sequential-read loader. Commit/install complete
step 1 before starting the library-in-RAM step 2. No native-SPL speedup has
yet been established; the repeated retained-boot0 benchmark remains current.

The v4 BROM handoff also recovered normally but failed candidate proof. It
reached stage 34 at 1.369 s, with stock boot0 already restored, and Linux then
reported stock-like timing without the marker. Evidence is
brom-vendor-handoff-v4/run.json. Do not try another handoff fix under the same
unproved premise: "the native first stage actually reaches BL31 and BL33".
The next unit is a census, vendor-handoff-trace-v1: in-memory entry/exit hooks
write stages 35/36/37 to RTC GP15, after the normal SD journal stage 34.
The original GP15 is zero (mainline/rtc-before-trace.txt); restore it after
diagnosis. Matched warm controls must first prove marker persistence through
stock recovery. No monitor/UBoot trace code is written to the on-card TOC1.

### Native monitor contract reached; stripped loader next

The final census is vendor-handoff-trace-v5, SPL SHA256
fa260b0d4f041652d2e131d6a48cb516a0727d71e77dd09463a7d92fb715e077.
Its matched scratch and primary controls passed. The BROM trial
brom-handoff-census-v5 reached RTC stage 37 inside BL33's own header, proving
native SPL -> vendor BL31 -> AArch32 U-Boot entry on hardware. Stock boot0
was restored and verified before DRAM; normal Linux was healthy afterwards.
The overall runner still fails Linux-path proof because the SPL marker is
absent. Do not conflate that failure with failure to reach BL33.

The earlier trace had two diagnostic defects. Ordinary DRAM is XN under
BL31's EL3 page tables, so EL3 hooks must stay in its executable header page.
Its final ERET routine is also reused by kernel SMC calls: an unconditional
hook overwrites the initial checkpoint and clobbers x16/x17 on AArch64 returns.
The v5 hook runs before the original x16/x17 restore and captures only the
first return, then passes all later contexts unchanged. Four real-instruction
cases test correct/wrong initial destinations and later kernel returns.
The BL33 hook stays inside its own header, not a potentially secure DRAM area.

Newly checked: the Android image embeds a DTB (137723 bytes), in addition to
the TOC1 DTB. The old SPL marker modified only the TOC1 tree, so its absence
in Linux is inconclusive. The stripped loader must add its marker to the
actual tree it passes to Linux. The existing LZ4 image has kernel8501202,
ramdisk512, second0, recovery_dtbo0 and DTB137723 bytes.

variant-survey/fdt-contract.py and evidence/fdt-contract.json compare every
live property with each device's package and prove the legacy /dram mapping
on both devices. Vendor U-Boot writes the first 24 raw parameter words into
the legacy property-name sequence; preserve that mapping despite misleading
names for the H616 layout. Memory reg is two 64-bit cells: base0x40000000,
size2GiB on RG34 and1GiB on RG40. Dynamic bootargs must retain SID serial,
panel strap selection and the existing root/partition/kernel arguments.

An early-ADB observer helper is installed at /usr/sbin/baseos-boot-observer.
It can reload an already-armed watchdog, without changing its timeout or
enabling it, for at most 60 seconds while a trial is observed. The inactive
hardware control passed. The v5 trial recorded no early ADB rows; that absence
alone does not prove Linux never started. Improve capture before relying on
it. The helper is inert unless explicitly invoked and must be removed during
final lab cleanup. RTC GP15's backed-up zero value was restored after census.

Next implementation unit compares native MMC callback reuse against an
independent post-DRAM loader driver. Grounding is /tmp/h700-native-grounding.md;
the design comparison is /tmp/h700-native-arena.md. Do not start library-in-RAM
step 2 or call step 1 complete. No commit has occurred, and the repeated boot
benchmark remains the retained-boot0 result.
# Native stripped-loader implementation, 2026-10-15 continuation

Current candidate is native-loader-v12 / mainline/native-preload-v8, not v8
listed historically below. This adds temporary monitor PSCI entry/ERET progress
tracing in reserved executable header bytes, and host proof of an actual A64
PSCI_VERSION query/return. All initial entry and native kernel/FDT witness
proofs passed. Full preload and recovery-control qualification are running.
`brom-native-loader-v5` (native-loader-v11 / SPLv7) left stage51 unchanged:
no early fault PC, healthy stock fallback, boot ID
7b0acd12-9041-402b-b30d-cb7012a3eef9. This does not exonerate early Linux;
silent stalls and faults after Linux replaces VBAR are outside the recorder.
Native Linux and a complete native benchmark remain unproved.

Update: `brom-native-loader-v6` used that v12/SPLv8 candidate and GP15 ended
at **0xd4000003**: standard PSCI CPU_ON (0xc4000003) reached, with a later
common EL3 return observed. Thus native Linux actually progressed through
primary MMU/C/FDT/memory setup to secondary startup. GP15 is shared across
cores, so the return bit cannot identify which core or call returned.
The boot still failed to reach ADB/frontend and recovered healthy stock,
boot IDf2fdf9e4-9d26-4340-acaf-a07ffdc6743c; stock boot0 and env exact.
Next candidate is v13 / SPLv9 with diagnostic maxcpus=1 to separate secondary
startup from other causes. Single-core mode is not a production fix.

Latest hardware result supersedes the in-progress entries below:
`brom-native-loader-v4` (native-loader-v10 / mainline/native-preload-v6)
reached **RTC stage51**. Both the full 17,686,536-byte decoded kernel checksum
and the final FDT checksum passed in physical backing RAM after A32 cache/MMU
cleanup, through the actual vendor SMC/A64 entry. Linux still did not return.
Automatic watchdog recovery returned healthy stock, boot ID
92f1e4ef-8a5b-4ecf-afb4-53c0a3186976; exact stock boot0 body/environment,
valid checksum and GPU/Wi-Fi/framebuffer/frontend checks all passed. No host
restore/manual intervention. This is a failed native boot, not a speed result.

The prior v8/v5 trial (`brom-native-loader-v3`) reached A64 entry stage49 and
also recovered stock. The v10 candidate additionally matches working no-logo
initrd properties as be64 and reserves the initrd. Four-way full property and
reservation census is `native/census-fdt.py`, output
work/h700-loader/native-fdt-census.json; authoritative working reference is
work/h700-loader/working-fast-kernel.dtb. The remaining FDT differences are
bootargs provenance and native timing/generation metadata.

Actual monitor instructions already reset the full lower EL1 context, set
CNTVOFF=0/CNTHCTL=3 and A53 SMPEN, and program secondary RVBAR/reset/power
through PSCI CPU_ON. Missing timer/SMP initialization is not an evidenced fix.
The next diagnostic adds early exception vectors only in the inspected Image's
zero idmap-page padding, with VBAR set by the A64 probe. It records low32 ELR
in temporary GP15 before watchdog recovery. It does not alter existing Linux
instructions and is explicitly excluded from production eligibility. This
narrows early kernel faults without a kernel source rebuild.

Latest correction supersedes the embedded-DTB assumption below: a full property
census on **both** live devices found seven differences between the prepared
TOC1 DTB and the original Android-embedded tree; live Linux matches TOC1 on all
seven (five USB statuses late, auto_print disabled, platform debug_mode zero).
tools/kernel_gzip.py patches only the TOC1 tree. Native build.py now embeds
that prepared TOC tree in its own Android payload. native/verify.py compares
every other output property with live Linux, catching this exact mistake.
The corrected payload is 8,650,752 bytes. Original Android-embedded DTB was
not the authoritative kernel-base contract. Evidence:
work/h700-loader/{rg34xxsp,rg40xxv}-fdt-source-comparison.json.

The second native hardware trial brom-native-loader-v2 (native-loader-v5 /
mainline/native-preload-v3) recovered to healthy stock, but **GP15 stage47**
proves own A32 entry, own MMU, real kernel decompression, final FDT construction
and arrival at the final cache-clean/kernel-SMC routine on hardware. Native
Linux still did not return. Stock boot ID018ea84e-f388-4541-ba18-10a6657a88ba;
stock boot0/environment and full health verified. Diagnostics now distinguish
stage48 (A32 cleanup complete, before SMC) and stage49 (A64 kernel entry probe).
The 32-byte A64 probe preserves x0..x3 and branches to unchanged kernel entry;
it is diagnostic-only and must be removed before release.

Current corrected candidate is **native-loader-v8**, generation
bd814bd50670bd79844c681363163fd73ec78314e654dc9182f3343d00804fd0,
loader SHA4b5df8ec04564521db6ab822f7a48256822fbd35362407d8551356651d6a544d,
SPL **mainline/native-preload-v5**. It passed own loader/FDT/live-property/
kernel-entry-probe and actual vendor SMC tests. Its full SD/PoC proof plus
actual-SPL-record A32 proof and scratch-control build are currently running.
Complete same-binary scratch and primary hardware controls, then run the
restore-first trial. native-loader-v6 failed host linking (no trial);
v7/native-preload-v4 passed emulation but were not tried on hardware because
the FDT-source error was found first. Do not confuse those artifacts with v8.

The monitor SMC proof now seeds its lower SCTLR state from the linked A32
loader proof, rather than assuming the monitor's initial value survived the
loader. The A32 verifier starts from actual BL31's 0x30c00820 and models its
own changes. `native/verify.py --spl DIR` additionally consumes the exact
SPL-produced 232-byte record; v7 cross-stage proof passed. The vendor kernel
has built-in PSTORE_RAM/CONSOLE and EARLY_PRINTK if further early-Linux
diagnostics are needed (config captured in work/h700-loader/kernel-config.gz).

The user asked why work stopped. Continue autonomously; status questions do
not end the task. Step 1 is still incomplete and must be committed/installed
before step 2. No manual recovery is available. Existing explicit firmware
trial/restore authorization persists.

The architect/arena comparison selected cached SPL preload plus RAM-only A32
BL33. Independent judge scores A/B/C were 22/16/14 out of 25; parent agreed
on A. Contracts and gates are in DESIGN.md. New implementation lives in
native/ plus spl/native-boot.c, native-cache.c, native-cache-off.S and
native-handoff.h. Native build.py uses pinned upstream libfdt, the shared
bounded LZ4 decoder, environment/GPT-derived bootargs, SID serial and generated
per-model panel policy. The loader is 15 KiB; SPL still fits its 48 KiB code
and 16 KiB BSS limits. Compact firmware (monitor, DTBO, monitor DTB, loader)
is 269,312 bytes at p4+40 MiB; the original 8,644,608-byte LZ4 Android image
is at p4+48 MiB. Recovery TOC and primary gzip image stay intact.

Fixed record at 0x4a300000 is 232 bytes, containing data only, generation,
RAM/type/trained parameters, timestamps and FNV checks. SPL EL3 tables are
0x4a400000..0x4a403000; own A32 tables at 0x4a0f8000, stack ends 0x4a0f0000.
Actual vendor first entry and SMC dispatch do not touch the record/table arena.
The A32 binary reproduces 17,686,536 kernel bytes, embedded-DTB transformations,
both PE6 outcomes and the exact A32 SMC arguments. Actual vendor instructions
then ERET to 0x40080000/SPSR0x3c5/x0=0x44000000/x1..3=0. This is emulation,
not yet a complete hardware Linux boot.

The native selector now normalizes detected geometry words 6/7 for both
monitor and Linux. The vendor routine at boot0+0x4910 confirms the encoding.
verify-vendor-geometry.py passed 480 vendor-instruction cases (240 native
vectors across two boot0 binaries) and six invalid inputs. Existing misleading
legacy FDT names remain in their exact raw first-24-word order. Native warm
selection/timing proofs pass. Suspend acceptance remains open.

The cache proof explicitly models EL3 system registers and translation
descriptors. cache-visibility.py keeps poisoned PoC copies until a complete
modeled set/way clean; 12,052,712 loaded bytes passed. verify-cache.py executes
the actual cache transition and proves NOPing its cleanup call is rejected.
These model correctness, not hardware cache latency. An important harness fix:
Unicorn X29/X30 IDs are not contiguous with X0..X28. arm64-system.py now maps
FP/LR correctly; the actual vendor SMC vector exposed the old mistake.

First native hardware trial **brom-native-loader-v1 failed native Linux** and
recovered automatically to healthy stock. It used native-loader-v4 (generation
2c90024e..., loader SHA58f3962e...) and mainline/native-preload-v2 (SPL SHA
5454b392...). Same-SPL scratch and primary restore hardware controls passed
in native-scratch-v2 / native-primary-v2. Native BROM execution reached stage34
after compact firmware/image read, hashes and DRAM setup. Actual 24 MHz counter
checkpoints: stage30 0.494374 s, DRAM ready stage31 0.528428 s, payload complete
stage32 0.954472 s, before cache cleanup/monitor stage34 0.955750 s. These are
partial diagnostic intervals, **not a complete boot benchmark**. No native
FDT marker reached Linux; fallback boot ID92eb50de-484e-4239-9bb8-868a56223386,
normal env/stock boot0 exact and checksum valid; GPU/Wi-Fi/framebuffer/frontend
health passed. No host restore/manual step needed. Observer had no early ADB
samples. Full evidence and candidate/stock snapshots are in brom-native-loader-v1/.

Current in-progress diagnostic candidate: native-loader-v5, generation
c03eb52862dca9176b0605c5ee95862fcf96699336e2a59a6b07255d8d4ef236,
loader SHAba1cdf1b..., with native SPL build mainline/native-preload-v3.
It writes GP15 only as temporary lab tracing: SPL34 before clean,35 after clean,
36 before RMR; A32 entry40; C entry41; before MMU42; after MMU43; before decode44;
after decode45; FDT complete46; before kernel call47; failure0x8000|error.
run-brom-control.py now gathers GP15 for native diagnostic candidates. Stock
GP15 was originally zero; restore it after diagnostics. Persistent RTC recovery
is NOT implemented or proved; do not use this trace word as an ownership claim.
bootreason remains unknown for restart-only bring-up and explicitly blocks
durable installation until PMIC/charger behavior is reproduced.

Next: complete v3/v5 instruction proofs and same-binary scratch/primary warm
controls, then a restore-first BROM trial to localize the stop. Continue to
native Linux, all-model generation/replay, both-device health/suspend,
persistent automatic recovery and matched benchmarks; commit/install step1,
then work on step2. Native architecture and current benchmark are distinct:
the last repeated measured fast path still retains boot0 (handoff5.213 s versus
5.868 s matched baseline; menu8.035 s versus8.739 s). No new native speedup
has been established. Physical power-button cold boots remain unperformed.
