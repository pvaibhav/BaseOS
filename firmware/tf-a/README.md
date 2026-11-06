# H700 BaseOS TF-A T1/T2 and reset correction

Host-only U2 component, **not deployable or hardware qualified**. The full
open-firmware/deep-suspend goal remains incomplete. No T3 PMIC operations or T4
suspend changes are included. Native SYSTEM_RESET now reloads the watchdog
after setting its interval; other native PSCI and power functions are unchanged.

`source.json` preserves the official TF-A v2.15.0 annotated tag, peeled commit,
tree and epoch. Identity is checked with Git object hashes and fsck, not a
signature or an archive checksum. The pristine `work/mainline/firmware/source`
checkout is never patched. One active export, `source-u2`, receives the explicit
two-patch queue using strict forward-only, fuzz-zero patching.
There are no retained archive copies. The export stamp binds the queue to the
complete resulting content; reuse rejects modified, missing or unknown files.
Output reuse likewise checks all resulting files and the complete input policy.
Change inputs by removing/regenerating the owned active export/stamp or selecting
a fresh cache/output; never manually bless a changed stamp.

From the repository root:

```sh
docker run --rm -v "$PWD:/src" -w /src \
  ghcr.io/loveretro/h700-toolchain@sha256:bf5d8f9f5442cad789aa77176bc059ec823caefa3258bf30780eee681c777119 \
  python3 tools/build-mainline-firmware.py --output work/mainline/firmware/build-u2r --jobs 8
python3 tests/test-tf-a-handoff.py
cc -std=c11 -Wall -Wextra -Werror -Iexperiments/h700-mainline/include tests/test-u2-handoff.c -o /private/tmp/baseos-u2-handoff-check
/private/tmp/baseos-u2-handoff-check
```

The builder also runs directly on Linux with the pinned GCC 13.3/binutils.
`--cache`, `--source`, `--output`, `--cross-compile`, `--jobs` route inputs;
default output is now `CACHE/build-u2`, never the baseline `build`.
`BASEOS_INCLUDE` explicitly points to `experiments/h700-mainline/include` and
consumes the parent-owned `baseos-bl31-handoff.h` read-only. Build overrides,
compiler/linker executable hashes/versions, source pins, ABI/patch/builder hashes,
patched content digest and epoch are emitted as `validation.json.inputs` for I3.
These fields are deterministic within the recorded toolchain. ELF/binary hashes,
PT_LOAD bounds and BSS/end symbols describe the actual component output.

## Interface

The A64 helper enters BL31 at `0x40000000`, EL3, MMU/cache disabled, with x0
exactly `0x4a300400`. Early setup checks that address before any read, copies
exactly 256 bytes with aligned volatile 64-bit loads into secure static storage,
and calls the shared validator with `BL31_BASE`/`BL31_LIMIT`. Failure logs ERROR
and panics. U2 additionally requires the fixed final FDT address `0x4a200000`.
`sunxi_baseos_handoff()` returns a const pointer to that private copy for future
T3/T4 use. No U-Boot search or fallback remains.

With MMU enabled, header/total size/full structural validation precede FDT
traversal and PMIC setup. The descriptor supplies actual writable capacity.
The helper owns trial/secmon/SMC policy; TF-A additionally refuses a missing
trial marker, malformed compatible strings and every `x-powers,` compatible
before upstream PMIC detection, making PMIC bus setup unreachable for U2.
Reservation and H616 L2 fixups are retained; the H616 fixup now returns every
libfdt error, and preparation panics on all errors, packs and cleans the blob.
The existing 64 MiB NS mapping covers descriptor and FDT; no MMU map is changed.

The standard TF-A context/ERET path enters Linux at `0x40200000`, EL2h,
SPSR `0x3c9`, x0 final FDT `0x4a200000`, x1..x3 zero. Secure memory remains the
full `0x40000000..0x40040000` arena regardless of occupied ELF size.
Startup neither feeds/disables the adopted WDT nor writes RTC GP registers.
Native `sunxi_system_reset` writes WDT CFG/MODE/CTRL for SoC reset;
PMIC restart, full poweroff and suspend remain separate work.

Parent join: pass `--tfa-build work/mainline/firmware/build-u2r` to
`experiments/h700-mainline/build-u2.py` alongside its prepared-work, inventory,
target, kernel, initramfs, DTB and output arguments. Baseline outputs are not U2
inputs. Parent alone performs final integration, review and hardware trials.

## Recovery qualification

The first physical U2 trial entered Linux at EL2 and brought CPUs 1–3 online,
but failed automatic recovery after a software restart. A physical RESET
returned to stock. The original BL31 (`build-u2`) writes CFG and MODE without
reloading the already-running watchdog. Patch 0002 adds the reload used by
Linux and BaseOS SPL: CTRL=`0x14af`, after MODE. This is a probable failure
mechanism; the H616 counter behavior and the corrected physical reset remain
unproven. Keep `watchdog.stop_on_reboot=0` to cover shutdown hangs.

`tests/test-tf-a-reset-sequence.py BUILD --report OUTSIDE_BUILD.json` executes
only the real reset function, stopping before its first delay. Run it in the
pinned Linux container with the existing `work/mainline/payload/linux-deps` on
PYTHONPATH. It verifies ELF load bytes equal BL31, then checks the three actual
MMIO writes in order. The old build fails; `build-u2r` passes. GIC is skipped
and watchdog MMIO is modeled RAM: this is not a counter/reset/recovery test.

Next physical gates require the user present and the existing one-shot latch:

1. Online CPUs 1–3, stop supervisor feeding via its SIGTERM handler, confirm
   the stop log, then observe natural expiry. Do not request a restart.
2. Only after natural expiry returns to stock, re-arm the same candidate.
   Stop feeding again; request `reboot -f` three seconds after the stop
   acknowledgement, aborting that request if eight seconds have elapsed.
   A fast USB disappearance followed by a new stock boot ID distinguishes
   the shortened reset from the remaining natural watchdog interval.

`experiments/h700-mainline/observe-usb.py` samples the macOS USB registry by
serial independently of ADB. It controls nothing on the device. Failed queries
are unknown, not disconnects; each sample records monotonic-time bounds. Use
those bounds with separately recorded freeze/acknowledgement/request times
and the stock boot ID. USB disappearance alone does not establish reset.
No unattended trials resume until both physical gates pass on this RG28XX.
