# U2 preparation helper

Standalone AArch64 EL3 preparation stage. `entry64.S` owns SP/VBAR/DAIF, the
identity map and the register-only cache-off transition. `helper.c` owns bounded
Android/BOL4 decoding and descriptor production. `fdt-final.c` finalizes the
independent mainline tree and checks `/chosen/baseos,trial`, secure-monitor
coverage, SMC PSCI and absence of all `x-powers,` compatibles. No vendor tree is
used for mainline metadata. The existing display implementation and generated
per-model tables retain panel selection and sliced decoding during display waits.

The incoming record must be at 0x4a300000. A private copy is validated before RAM
size or payload extents are used. SPL owns complete payload media integrity;
this stage checks record/header integrity and generation, and the existing BOL4
stream decoder checks its own compressed extent. No RTC/WDT/PMIC stores are used.

Builder API (all path arguments are `Path`, generation is 32 raw bytes):

```python
describe_inputs(target, inventory, prepared)  # JSON-compatible input/policy map
build(target, inventory, prepared, generation, output)  # writes/returns manifest
```

`describe_inputs` reports absolute source/hash mappings, model policy, pinned
U-Boot archive, compiler/linker hashes and exact flags. No generation-dependent
header or payload input is needed to compute the final generation. Build output
must be fresh and below `work/mainline/helper/`. The archive is read in place.

Parent join commands (supply the final canonical generation):

```sh
python3 experiments/h700-mainline/helper/build.py --target rg28xx \
  --inventory /Users/pvaibhav/Code/Me/BaseOS/work/h700-loader/native-logo-v1/fleet-v1/inventories/rg28xx \
  --prepared-work /Users/pvaibhav/Code/Me/BaseOS/work --describe-inputs
python3 experiments/h700-mainline/helper/build.py --target rg28xx \
  --inventory /Users/pvaibhav/Code/Me/BaseOS/work/h700-loader/native-logo-v1/fleet-v1/inventories/rg28xx \
  --prepared-work /Users/pvaibhav/Code/Me/BaseOS/work \
  --generation "$FINAL_GENERATION" --output work/mainline/helper/u2-final
python3 tests/test-u2-helper.py work/mainline/helper/u2-final
docker run --rm \
  -v /Users/pvaibhav/.t3/worktrees/BaseOS/feature-h700-mainline:/src -w /src \
  -e PYTHONPATH=/src/work/mainline/payload/linux-deps bf5d8f9f5442 \
  python3 experiments/h700-mainline/verify-u2-helper.py work/mainline/helper/u2-final
```

`fixture-r3` uses repeated `11` bytes as a **non-final fixture generation**. Its
synthetic kernel/initrd, converted real U1 RG28XX tree and helper binaries are
host-test fixtures, not deployable components. G3 covers injected host arenas,
real libfdt and refusal cases. G4 executes compiled A64 code through the BL31
branch, models system registers/cache/MMU and display reads, checks bounded
stores, transition memory accesses, Image/descriptor/T4 output, mainline FDT
properties/reservations, and zero RTC/WDT stores. Timing and display properties
are checked against their actual runtime records rather than host-injected
values. No TF-A emulation or hardware/coherency qualification is claimed.

The shared validator extraction produces byte-identical allocated A32 object
sections in BSP and U1 modes (fixture-r2/compatibility.json). Parent must also
bind `native-checks.h` in the legacy native builder input list; I2 does not own
that builder. Final matched packaging, review, deployment and hardware gates
belong to the parent. The overall goal remains incomplete.
