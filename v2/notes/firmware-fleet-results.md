# H700 firmware candidate results

All nine profile candidates and real model payloads are built. All 155 offline
test groups passed. All new models remain **untested on hardware**.
RG35XX 2024 shares the rg35xxplus profile; there is no separate device ID.

| Model | Built? | Tests | Open risks | Owner's first checks |
|---|---|---|---|---|
| [RG34XX](../../work/v2/firmware/v2-rg34xx/INSTALL.md) | Yes | 18/18, emulated | Unknown panel/RAM revisions | Held-stock boot, then 720×480 logo/colors and Linux takeover |
| [RG34XX SP](../../work/v2/firmware/v2-rg34xxsp/INSTALL.md) | Yes | 18/18, emulated | Unknown revisions; lid/power untested | Held-stock boot, 720×480 takeover, then lid behavior |
| [RG35XX H](../../work/v2/firmware/v2-rg35xxh/INSTALL.md) | Yes | 18/18, emulated | Unknown revisions; earlier 1.x fallback reports | Held-stock recovery, 640×480 takeover, then sticks |
| [RG35XX Plus / RG35XX 2024](../../work/v2/firmware/v2-rg35xxplus/INSTALL.md) | Yes | 18/18, emulated | Shared Plus/2024 profile; qualify each revision | Exact stock identity on Plus/2024, then 640×480 takeover |
| [RG35XX Pro](../../work/v2/firmware/v2-rg35xxpro/INSTALL.md) | Yes | 18/18, emulated | Unknown revisions; controls/power untested | Held-stock boot, 640×480 takeover, then every control |
| [RG35XX SP](../../work/v2/firmware/v2-rg35xxsp/INSTALL.md) | Yes | 18/18, emulated | Unknown revisions; lid/power untested | Held-stock boot, 640×480 takeover, then lid behavior |
| [RG40XX H](../../work/v2/firmware/v2-rg40xxh/INSTALL.md) | Yes | 18/18, emulated | Unknown revisions; display/sticks untested | Held-stock recovery, 640×480 takeover, then sticks |
| [RG40XX V](../../work/v2/firmware/v2-rg40xxv/INSTALL.md) | Yes | 18/18, emulated | Unknown revisions; power/controls untested | Held-stock boot, 640×480 takeover, then buttons/power |
| [RG CubeXX](../../work/v2/firmware/v2-rgcubexx/INSTALL.md) | Yes | 11/11, emulated | Unknown BOE revisions; earlier 1.x fallback reports | Held-stock recovery, then 720×720 BOE picture and takeover |

Each variable-panel model passed both PE6 levels, all three stock bank records,
missing logo, invalid DRAM, wrong generation and wrong DTB. The selector also
selected every bank by index with simulated failure/panic unwind, including
same-type banks with different parameter words. Each capsule passed nonzero RTC
stock selection and its other integrity/watchdog emulation checks. CubeXX has
one fixed BOE branch. Test results explicitly label emulation. Payload and DTB
hashes are recorded in each candidate's tests.json and fleet-results.json.

All pinned prepared stock inputs were present and checked; RG34XX SP/RG40XX V
inventory fixtures were extracted locally from their pinned prepared inputs.
No real payload checks remain pending. Unknown physical panel/RAM revisions,
electrical behavior, watchdog reset/continuity, early RTC acknowledgment,
controls, power and suspend remain unqualified. Keep unknown revisions on
stock. Earlier H/Cube fallback reports concern 1.x, not these candidates.

Both fresh compatibility rebuilds match all five binaries and generation.txt:
v2a and v2-rgsp. Existing output files were not replaced. Comparison evidence:
work/v2/firmware/fleet-compatibility.json. Full artifact evidence:
work/v2/firmware/fleet-artifacts.json; combined results: fleet-results.json.

Each linked INSTALL.md contains exact sizes, hashes, offsets, stock recovery
identity and safe firmware/payload-before-capsule write order. Owner checks
continue with physical failed-trial/reset/stock recovery, known RAM/panel
revisions, RAM stress, input, charging, shutdown/restart, then suspend/resume.

No handheld access, flashing, backups, host package installs, commits or pushes.
All task outputs are below work/v2/firmware and work/v2/os. No SD staging occurred.
