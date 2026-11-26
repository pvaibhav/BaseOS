> Agent snapshot at STOP. [PROJECT-HANDOFF.md](../PROJECT-HANDOFF.md) is authoritative for the final restored device state; no diagnostic remains installed.

# Agent handoff — 2026-10-15, stopped at user request

No device access or reboot was performed by this agent. Implementation and new proofs stopped immediately. Only the already-running packer command was collected; it exited successfully. The proposed monitor-prefix replay below was **not run**. Root reports that the phase-diagnostic reboot was never performed.

## Immediate blocker: full vendor early-display prerequisites

RG40 logo-v3 noack returned to stock before Linux, without a native witness. The reconstructed helper is host-qualified, **not hardware-qualified**. Do not interpret the table oracle as proof that the entire original boot pipeline was reproduced. User explicitly wants the complete working vendor sequence/prerequisites, rather than more guesses.

The strongest concrete omission found before STOP: the original U-Boot display trace starts **after BL31/monitor platform initialization**, whereas the native early-display helper runs **before the monitor's initial entry**. Existing actual-monitor proofs show these additional writes, absent from the helper display oracle:

```
03008004..030080d4, step 0x10 = ffffffff
03001f00 = 7                 07010290 = 7
03002020 = fff               030080e0 = 1
09010010 = 01000000
07000544 = 18                07000540 = 18
07000500 = aa160001          0700050c = f
08110000 = 0                 08110004 = 0
```

The proofs also include UART setup at 0300190c and 05000000. The registers above may include security/resource/MBUS prerequisites relevant to nonsecure DE DMA; their actual function/callers and semantics remain **unidentified**. Do not blindly copy them. Source evidence is the `mmio_writes_except_gic_uart` field in:

- `work/h700-loader/native-logo-v1/artifacts-v3/mainline/rg40xxv/monitor-verification.json`
- `work/h700-loader/panel-release-artifacts-v2/mainline/rg40xxv/monitor-verification.json`
- Actual execution harness: `experiments/h700-boot-speed/spl/verify-monitor.py`.

Next narrow host action would be a work-only import/instrumentation of `verify-monitor.py`: collect PC alongside each existing `mmio_writes[hex(address)] = hex(value)` statement, run `verify(package, boot0, fresh_output)` **without native_loader**, then disassemble the exact monitor around the collected PCs. Inputs: `work/rg40xxv/boot-gzip/boot-package.bin` and `work/h700-loader/rg40xxv-inventory-v1/boot0.bin`. This runs real monitor initial entry through ERET to stock U-Boot and does not require a kernel decode. This action was NOT started before STOP.

Secondary, weaker hypothesis: PLL VIDEO0/DE output-enable bit27. Helper writes power31/lock28/29 but does not establish bit27 at CCU+040/+060. Post-Linux stock census has VIDEO0=88001703, DE=88001c00. However actual vendor boot0's nine-PLL block only ORs bit31; bit27 may already be reset state or set later by Linux. No evidence yet establishes a difference at helper entry. Root was asked to capture raw +040/+060 in phase diagnostics; no write was proposed. Relevant files: `spl/verify-vendor-plls.py`, `work/h700-loader/brom-native-loader-v8/stock-census/clocks.txt`, generated U-Boot `arch/arm/mach-sunxi/clock_sun50i_h6.c`. Vendor boot0 is Thumb; the nine-PLL block is 2242e..224be, not A32.

## Proven vendor display subsequence and its limits

Original U-Boot normal `init_sequence_r` prefix: board/PMIC helper +25d8; normal work-mode path +2614 calls +2394; +2394 clears SYSCTRL03000000 selectors (`&80000000`) and SYSCTRL03000004 bit24 (`&feffffff`), then clears 10001 at CCU1690/169c/1600/160c. Normal init +2a04 calls clocks +16848, tails through +297c to `drv_disp_init` +1fe00. Logo +2a2c opens display/layer, reads FAT bitmap and decodes it. Panel power wait30ms; reset15/50/100ms; panel SPI; wait50ms; TCON on; wait100ms; backlight. Real LCD IRQ96 drives dirty-block publication through +229e4 → +20e44 → +2385c → +241xx → +2820e → +288e8 → memcpy. A +2820a call is a no-op. Do not infer DE DBUF self-clear from modeled memory.

Finished work oracle is `work/h700-loader/native-logo-v1/early-display-analysis/`:

- `display-engine.json` SHA256 `9562d760c31475b768a3cf311a3114a7da6a21c4d9f991bc224c40c00e192593` (root copied identical canonical `native/display-engine.json`). Eleven models, 263 prepare / 115 publish / 5 enable / 15 backlight operations per model.
- `display-engine-proof.json`, seal `5d87976abaf282aff6945a3f799e6420c704bfb99b624dfd027e547084f8cd9f`.
- `engine-final-state-v2.json`: all-11 selected-domain final-state comparison, except typed framebuffer PA, clean GPIO, and explicit IOMMU safety addition.
- `actual-pixel-comparison.json`: original vendor BGR24 decode equals B,G,R,ff bytes at full geometry. RGSP 640x480 FAT asset on 720x480 panel uses full720 canvas, x=40, no scaling. Vendor margins are transparent zero; helper explicitly uses opaque black (visually equivalent over black).
- `active-display-takeover/iommu-clock-proof.json` and `iommu-disable-seeds.json` (sequential agent): exact kernel provider gate 030017bc bit0, no reset field; actual disable-master0 sets 030f0030 bit0. Helper addition is gate|1, DSB, bypass|1, DSB, bounded bit0 readback. This proves software semantics, not pre-monitor electrical accessibility.

Selected oracle domains cover DE, LCD0 TCON, their clocks, PWM and GPIO. HDMI/TVE/audio/LCD1 setup was intentionally excluded. Actual display-only trace contained no omitted write to a selected domain beyond GIC IRQ enable. This does **not** close monitor/board prerequisites. Arbitrary full-CCU all-ones seeds invalidate the vendor provider graph and are not claimed supported. Individual RMW windows preserve unrelated bits.

RG40 exact timing evidence: panel640x480; VIDEO0 factor1702 ->288MHz; TCON DCLK divider12; TCON044 high gates f0000000; TCON040 output31; TCONtop001c low nibbles=0x10. Actual current-line getter +2ffb8 reads 065110fc bits27:16. Root helper polls line advancement bounded100ms; no invented DBUF bit.

## Owned current source additions: sequential payload prototype

I own these NEW files; no further edits are pending:

| File | SHA256 |
| --- | --- |
| `experiments/h700-boot-speed/sequential/boot-blob.h` | `79ba4b3b5190005c348168017c091fe4dd4cde21bfa31bcb8eb8b5d6df79a5cc` |
| `experiments/h700-boot-speed/sequential/init` | `92e9a9af9302aaba07d446e7b2244865e9bfb786d8a91af24adf2233dec5fac8` |
| `experiments/h700-boot-speed/sequential/build-payload.py` | `66968cc9f9ff4d512465a0a3899b4f19c3257ce2872c01a96a225f6068ff428b` |
| `tools/boot_payload.py` | `cb491db6e226e4d21919edd5c9b2af41d51d2f1d5a1a71d2b8f276d7a1881546` |

`native/display-engine.json` was generated by me and promoted by root. Current `native/verify-fleet.py`, `native/verify-kernel-logo.py`, loader/build/verify, SPL sources, image/update files and `sequential/slot-reader.h` are now owned by other agents/root. Do not overwrite them from my earlier snapshots. The reader and `test-slot-reader.py` were explicitly left **untested/unqualified** by native_irq agent. Older `sequential/build-fixture.py`, `test-fixture.py`, README describe historical fixtures, not finished firmware integration.

Layout: keep p4 64MiB. Each 512MiB A/B root slot becomes exact472MiB ext4 plus40MiB raw tail; descriptor at selected GPT p5 start+472MiB, then one contiguous Android image. Existing GPT slot flip commits both root and tail. Never place tail over an existing full512MiB ext4; build a fresh472MiB filesystem.

Descriptor is little-endian512B, magic `BOSSEQ1\0`; version/header at8/12; model16; image bytes/FNV20/24; initrd bytes/FNV28/32; library bytes/FNV36/40; headerFNV44 (zeroed during hash); u64 slot/tail48/56; nativeABI32 at64; mutable root/payloadID32 at96; image/kernel/initrd/library SHA256 at128/160/192/224; zero reserved256..511. Header uses explicit ull constants for U-Boot compatibility. Image is 2048-aligned, <=tail minus512; initrd <=32MiB.

**Stable native ABI is distinct from capsule attempt generation.** `native_abi_id(manifest)->bytes32` hashes protocol/ABI version, model, unchanged kernel+base DTB, layout and initramfs contract. It excludes serial, stock boot0, capsule generation, rootfs and library. `/etc/baseos/native-abi` and FDT `baseos,native-abi` are new stable identity. Rootfs `/etc/baseos/boot-payload-id` is mutable raw32. RTC health acknowledgment still uses the separate exact capsule attempt generation; root must finish that integration.

Packer CLI:
```
python3 experiments/h700-boot-speed/sequential/build-payload.py NATIVE_DIR STAGED_ROOT_TAR LIBRARY_SQFS FRESH_OUTPUT
```
It emits descriptor, deterministic uncompressed newc, rebuilt Android image, contiguous boot-blob, raw ABI/rootID and manifest. It preserves original compressed kernel and DTB. Staged tar must already contain matching native-abi and no per-card native-generation or preexisting boot-payload-id. RootID hashes staged tar/library/BusyBox/source image/ABI/init+protocol sources, avoiding a self-hash cycle. Agent sequential installs emitted rootID before mkfs.

`tools/boot_payload.py` exposes constants, `native_abi_id`, `make_header`, `parse_header`, `android_parts`, `validate_image`, and `validate_slot(slot,target,native_abi,root_build_id)`. Slot validator requires a regular full512MiB file, actual ext4 geometry exactly472MiB, exact tail image hashes/FNV/bounds and actual raw32 marker files via debugfs (regular inode/size). No byte-grep or block-device writes.

Init mounts selected p5 read-only, matches raw32 rootID, copies library into a tmpfs under `/usr/share/baseos/boot-ram`, bind-mounts its live RAM file over existing `usr-lib.sqfs`, moves dev/proc/sys and switch_root to original `/init`. Existing rcS library mount path stays unchanged. Failures do not acknowledge/feed WDT. Target root must contain the boot-ram directory. This runtime init has not booted on hardware.

## Actual Step2 results and remaining seams

The in-flight real-asset pack completed PASS, collected after STOP:

- `work/h700-loader/sequential-v2/build-sp-v1/manifest.json`
- Actual SP library 27,357,184B; static BusyBox1,115,944B; new initrd28,476,928B; image37,126,144B; blob37,126,656B; tail free4,816,384B.
- ABI `22001b14d0f62571f5944c4a224e18d4cddba5c4c2b9820d6e21ffe57d77151c`.
- PayloadID `1b65171ce71390a8304de8757bfb925a749cb5fc7e3de43ef582abae2e3f9aef`.
- Private read-only source/staging assets `work/h700-loader/mainline/sequential-assets-v1/`; source.img is a hardlink to saved BaseOS image and was read only. No device involved.

Sequential agent's actual CLI/update test completed 6/6 PASS in36.721s, log `work/h700-loader/sequential-v2/update-host-v1/tests.log`: two genuine512MiB slots, genuine472MiB ext4, actual source kernel/static BusyBox, small genuine LZ4 squashfs; both old/boe bundles selected automatically, whole-slot readback and stubbed flip; refusals for bad running/panel policy, cross-panel/legacy, full512MiB ext4 overlap and single-panel release. Their owned integration: `build-image.sh`, `tools/slot_image.py`, `tools/mkupdate.py`, `overlay/usr/sbin/baseos-update`, tests `test-sequential-update.{py,sh}`. No hardware qualification.

Remaining required integration: qualify the actual GPT/tail C reader; link it in SPL; replace fixed Android read/size/FNV; prove selected GPT root and rootID transport; increase initrd/staging caps with lifetime/cache proof (proposed initrd42000000..44000000, FDT44000000, source45000000..<48000000); ensure no firmware temporary overlap survives; nativeABI/FDT/health protocol separate from RTC generation; actual initramfs boot, library mount and complete A/B update/failure behavior. No firmware or image deployment of Step2 occurred.

## Other logo evidence retained

Noack fixture `work/h700-loader/native-logo-v1/noack-fixture/`: schema3, strict status80/timing56/full cmdline, actual visible fb row hash <=4MiB before frontend. Last host seal `21bc409dd60026f1b57770cb11b162fe3f0edb10861626ecef31670e4fb115e0`; PASS13 startup +86 guards +10 schema +10 binder/canvas +10 compiled inspector cases. Root owns current model binding and physical runs. RG40 artwork pixelFNV003692a6 from live p2; artifacts do not imply early-display hardware success.

Actual ROM v3 15/15 PASS: `native-logo-v1/artifacts-v3/rom-summary.json`, `rom/*/*.json`, `prove-rom.py`. Tests bind rom-input-pins and promoted-source-pins; exercise DMA/PIO native/stock dispatch/checksum/secondary, not physical display. Earlier stage-review byte-backed lab.write_region/readback proof: `native-logo-v1/stage-review-v1/safety-facts.json`. Root owns current hardware state and all cleanup.

Host JIT execution requires escalated exec permission on this machine; `/tmp/uboot-venv/bin/python` has Unicorn/Capstone. No new command should be launched merely to refresh these facts after user STOP.
