> Agent snapshot at STOP. [PROJECT-HANDOFF.md](../PROJECT-HANDOFF.md) is authoritative for the final restored device state; no diagnostic remains installed.

# Sequential boot and verification handoff — stopped 2026-10-15

Parent/user requested an immediate stop and handoff. No implementation, new test, build, or device operation started after that request. The format2 host test already running completed successfully. All files below are frozen. This work is **host preparation only**, not a qualified firmware release or a measured boot improvement.

## Current production-source changes owned by this agent

Modified tracked files:

- `build-image.sh` — optional internal `--native-dir DIR --native-panel old|boe`; otherwise original legacy compose. Native output is `work/TARGET/baseos-TARGET-PANEL.img`. GPT slot remains exactly512MiB; ext4 is exactly472MiB; packer blob starts at slot+472MiB within the final40MiB. Source recovery/boot partition geometry and existing userdata/primary layout remain unchanged. Existing rcS is untouched.
- `build-update.sh` — optional `--sequential` bundles the two internal old/boe native images into one normal target/version `.bosupd`. Original one-argument legacy path remains.
- `tools/mkupdate.py` — reads actual ext4 inode policies and validates full format2 slot through shared `boot_payload.validate_slot`; produces a single two-panel format2 bundle. Existing format1 metadata and `rootfs.img.gz` remain for legacy/native-generation images.
- `overlay/usr/sbin/baseos-update` — chooses the panel variant automatically from the existing exact trusted policy, requires running/installed ABI and payload identity, then retains the existing complete-slot write/readback/hash/GPT flip and rollback logic.

New untracked production/test files, which must be preserved explicitly:

- `tools/slot_image.py` — stages ABI, format and internal panel policy; writes a deterministic sorted/mtime-zero tar; invokes the host packer; installs its raw32 root/payload ID before mkfs. Numeric owners, modes and links are preserved. It also exposes full-slot verification for composition.
- `tests/test-sequential-update.py` — actual saved kernel/static BusyBox inputs, genuine LZ4 squashfs, genuine ext4, real packager, and real updater CLI with private regular-file media/GPT/reboot stubs. Requires the disposable-container guard.
- `tests/test-sequential-update.sh` — runs that suite in a disposable Docker container using a read-only checkout. Takes native directory and rootfs tar paths inside the checkout.

Exact SHA256 pins for all seven files and shared dependencies are in `work/h700-loader/sequential-v2/update-host-v1/host-proof.json`. No native, SPL, native-health, installer, qualification, logo source, or pinned base runner was edited for this integration.

## Format2 contract

One slot is512MiB. Ext4 ends at472MiB; the descriptor occupies the next512 bytes and precedes one contiguous Android image. The remaining tail capacity is40MiB. Never append this tail to an existing full512MiB ext4 filesystem.

Rootfs has regular files:

```text
/etc/baseos/boot-payload-format   exact ASCII baseos-slot/2 followed by LF
/etc/baseos/native-abi            nonzero raw32 stable firmware ABI
/etc/baseos/boot-payload-id       nonzero raw32 mutable root/payload identity
/etc/baseos/native-panel          exact old+LF or boe+LF
```

Format2 rootfs explicitly excludes `/etc/baseos/native-generation`: current capsule generation binds per-card boot0/whole image and belongs to the RTC attempt protocol, not universal updates. Rootfs staging precedes ID derivation, avoiding a self-hash cycle. The original kernel/DTB and static BusyBox are checked by the packer. ABI derives from target/model, original kernel and base DTB hashes, and explicit protocol semantics, excluding serial/boot0/capsule attempt identity and libraries/rootfs.

`tools/boot_payload.py`, `experiments/h700-boot-speed/sequential/{boot-blob.h,build-payload.py,init}` belong to `/root/initcall_probe`, not this agent. Shared API used here:

```python
native_abi_id(native_manifest) -> bytes32
validate_slot(slot, target=target, native_abi=bytes32, root_build_id=bytes32) -> dict
```

The shared validator checks exact slot/ext4 geometry, bounded descriptor/image/newc, integrity hashes, and actual ext4 ABI/payload markers. See its source for the exact512B descriptor layout. Packer emits `native-abi`, `root-build-id`, `boot-blob.bin`, `image.bin`, `initramfs.cpio`, and `manifest.json`.

A single release archive contains:

```text
manifest                  format=baseos-update/2 and variants=old,boe
manifest-old              whole-slot metadata and old policy
rootfs-old.img.gz          exactly512MiB when decompressed
manifest-boe              whole-slot metadata and boe policy
rootfs-boe.img.gz          exactly512MiB when decompressed
```

Each selected manifest includes target/version/build, slot-sectors1048576, image-size536870912, image-sha256, boot-payload-format, native-abi, boot-payload-id, and native-panel. Both variants must share ABI. Their mutable IDs differ because panel policy is part of the staged rootfs.

The updater reads exact `/etc/baseos/native-panel` and selects fixed member names. Users neither configure nor download a panel variant. Before writing it requires installed format2, no per-card rootfs generation, executable native-health, exact panel policy, nonzero installed/running ABI and payload identities, and matching candidate ABI. The new candidate payload ID may differ. Required raw32 running properties are:

```text
/sys/firmware/devicetree/base/chosen/baseos,native-abi
/sys/firmware/devicetree/base/chosen/baseos,boot-payload-id
```

Missing/crossed running properties reject the update. Format1 cannot replace a sequential root, and format2 cannot migrate an old installed root automatically. The whole512MiB hash covers filesystem and tail; GPT entry5 remains the only commit/rollback selector.

## Completed host verification

- `sh tests/test-update-apply.sh` returned0: all9 original mkupdate inode tests plus the existing full updater refusal, write/readback/commit, history, rollback, cleanup, USB and logging matrix passed. Its output is in the tool transcript; no fresh legacy log was saved.
- Final `sh tests/test-sequential-update.sh work/h700-loader/native-logo-v1/artifacts-v3/rg34xxsp/native work/rg34xxsp/rootfs.tar` returned0:6 methods in36.721s. Log and exact source pins: `work/h700-loader/sequential-v2/update-host-v1/{tests.log,host-proof.json}`.
- The new suite built two real472MiB ext4/512MiB slot files and one combined archive with the actual unchanged SP source kernel and static BusyBox plus a tiny genuine LZ4 library fixture. The actual updater CLI automatically selected old and boe, wrote the selected complete512MiB, matched the complete readback hash, and called the stub flip exactly once per successful run. It rejected bad running ABI/ID, missing/malformed/symlink panel policy, missing installed format, cross-panel/legacy/incomplete bundles, and full512MiB ext4 overlapping the tail. No hardware was accessed.
- Shell syntax and Python compile checks passed before the final run; `git diff --check` was clean. Full production rootfs/image composition was **not run**. These test roots are deliberately minimal and are not installable release images.

## Remaining integration gates, owned by the next/root agent

1. Native/SPL must select the current GPT root slot, validate the descriptor/ABI/model and bounds, and read its tail; no second mutable boot selector. Existing fixed-image recovery/capsule behavior needs an explicit safe migration path.
2. Integrate the larger32MiB initrd/48MiB staged-image bounds with real linked cache/non-overlap/lifetime proofs, preserving the vendor4.9.170 kernel, monitor handoff, all11 model ABIs, RTC attempt state and240-byte native record.
3. Export the two raw32 FDT properties above only after the matching descriptor is validated. Keep the exact capsule attempt generation in `baseos,native-boot` and its RTC cookie separate from the stable ABI.
4. Root-owned native-health needs a strict format2 branch using ABI/payload/root/panel identity while preserving current exact FDT attempt-generation/RTC ownership and all legacy format1 checks. Current production health still requires the old rootfs native-generation and therefore cannot accept these new format2 roots yet.
5. Installer must safely replace a whole inactive slot with the472MiB+tail layout, verify it, and coordinate first installation with updated firmware. Never write a tail into the current512MiB ext4. Preserve original vendor/recovery backups, boot partition geometry and user data. The current installer is not integrated for this format.
6. Prove actual vendor initramfs `switch_root`/tmpfs bind lifetime and RAM-backed squashfs on the final kernel. Packer init copies libraries to `/newroot/usr/share/baseos/boot-ram`, binds that file over the existing sqfs path, and leaves production rcS unchanged. No step2 device test, timing result, all11 format2 proof, or release qualification exists.
7. Finish parent-owned early-logo physical-display work before any step2 device integration. Existing model policy/asset binding must remain exact: prepared and live FAT BMPs can differ. Release panel selection is automated by the bundle as above; internal image composition still deliberately takes old/boe policy inputs.

## Other frozen agent-owned artifacts to preserve

- Work-only cycle/sleep wrappers: `work/h700-loader/native-logo-v1/{verify-native-cycle.py,verify-native-sleep.py}`. Tests/proof/commands in `native-logo-v1/cycle-host-proof-v1/`;11 methods pass, including real frozen display parsing and real delegated `--check` dispatch with device import forbidden. Wrapper hashes: cycle `ad3e9b99c3f90275ec075f373ff7b8384e42cbb832cc34bf3c5db52d53c898b3`; sleep `cf5d49637ea1cf87ed7eef365bf567d4395ad032099091bdf7b2d7d72bb662ea`. Real new-input checks/runs are parent-owned; synthetic tests are not hardware qualification.
- Canonical logo verifiers previously frozen: `experiments/h700-boot-speed/native/{verify.py,verify-fleet.py,verify-kernel-logo.py}`. Hashes respectively `f59edb33ecfaf064ee18b00b8c26775ab2f3d2b56d8c445621fb1421a1173ca3`, `ed2fe64fe383a43194c0d5bff3ddbcec8dcf70ccd28b9c7cb38889ec727de9c9`, `2f7a2b42b6e9eb9ab7b9ad3fb41f5168a801ee4dee04fe6aaf3968f28db34f38`. Linked cold/warm/ready proofs and all-three source joins are under `native-logo-v1/linked-verifier/early-host-v1/`; parent now owns authoritative from-SPL outputs and qualification replay. Do not restore an older standalone FDT over final from-SPL trees.
- Canonical `experiments/h700-boot-speed/native/display-handoff.json` plus actual all11 vendor scalar provenance under `native-logo-v1/active-display-takeover/`. Scalars are stock boot_disp100, boot_disp1=01040000, boot_disp2=4; preserve exact target/package/kernel bindings. This directory also holds kernel reset/clock/IOMMU takeover proofs and uncertainties.
- Earlier RAM fixture in `experiments/h700-boot-speed/sequential/{build-fixture.py,test-fixture.py,README.md}` and driver `work/h700-loader/compare-ram-libraries.py` were previously host-tested/frozen and are not part of the new payload path. Parent owns further lab use. Earlier installer/panel work and qualifications remain parent-owned and were not edited in this phase.

No active command or owned background process remains after the final host test. No commit was made by this agent.
