# Remaining H700 firmware candidates

All nine model profiles have their own build targets. RG35XX Plus and RG35XX
2024 share `rg35xxplus` in devices.json; no separate 2024 profile exists.
All new candidates are untested on hardware. No handheld was accessed or flashed.

Build and verify with the existing container images:

```
python3 v2/firmware/build-fleet.py
python3 v2/firmware/verify-fleet.py
python3 v2/firmware/write-fleet-install.py
```

Each model can also be built with `build.py v2-<id> --target <id>`, followed by
`v2/os/build.py payload --firmware v2-<id> --dtb work/v2/kernel/<id>.dtb` and
`v2/firmware/tests/run.py v2-<id>`. Build logs, test logs and intermediates live
under work/v2/firmware; candidates have private v2-<id> directories and payloads
live under work/v2/os/v2-<id>. Nothing is staged on an SD card.

The builder checks each stock boot0 and package hash, stable model ID, logo
size, panel rule, pull-up and framebuffer offset. Display generators check the
pinned vendor register operations and panel streams. RG34XX SP and RG40XX V
had no old extracted inventory folders: the builder extracts their GPT prefix,
boot0 and environment from the model's prepared stock prefix into local build
inputs, then applies the same complete-source and environment hash checks.
No stock evidence is fabricated. Every model uses its own parameter words.
No voltage settings were added or changed.

New models use helper-owned panel startup, per-model independent DTB compatible
checks before that startup, the stock LPDDR3/LPDDR4 record boundary and the RG SP
bounded calibration loops. Missing logo deliberately succeeds with a black
frame. Wrong generation, DTB or DRAM type refuses the trial and keeps pending;
the separately emulated capsule selects stock on a subsequent nonzero RTC pair.
The chain emulator does not claim to perform a physical watchdog reset.

Per-model tests exercise both panel straps where applicable and all three stock
banks. The bank-index harness selects even banks sharing a DRAM type, verifies
all preserved words and existing voltage requests, and simulates failure and
panic unwind before later candidates. Full-chain tests check stock logo size,
GPIO/SPI frames, reset and command delays, display enable/backlight waits,
final DTB metadata and BL31 entry. Training, geometry, PMIC, card and registers
are emulated. Fleet replays advance the simulated counter by 100 microseconds
per read and report that choice; they make no measured timing claims. Existing
legacy test runs retain the one-microsecond default.

If a real DTB/payload is absent, fleet.py makes explicitly named
SYNTHETIC-TEST-ONLY fixtures inside that candidate directory and reports
real_payload_checked=false. Those are never installable payloads. Real model
payload verification supersedes those smoke checks once the DTB arrives.
Tests.json records the payload and DTB hashes actually exercised.

RG28XX generation behavior is unchanged. RG SP retains its four original
recorded generator hashes for the legacy recipe, while new targets hash the
current generators. This explicit compatibility exception prevents adding
unrelated targets from changing RG SP's generation. Fresh output comparisons
are in work/v2/firmware/fleet-compatibility.json; all five binaries and generation
must match for v2a and v2-rgsp. Existing candidate output files are not replaced.

Unknown panel/RAM revisions cannot be identified reliably by PE6 or the PMIC
path. A matching model name does not qualify unknown hardware. Keep unknown
revisions on stock. Physical RAM behavior, power, watchdog continuity, early OS
health acknowledgment, visible takeover and suspend/resume remain open. Prior
BaseOS 1.x H/Cube fallback reports are reasons to check held-stock recovery and
display takeover first, not evidence of BaseOS 2 success or a new diagnosis.

Each candidate's INSTALL.md supplies its generation, sizes, hashes, card offsets,
stock recovery identity, firmware/payload-before-capsule write order and ordered
owner checks. Hardware trials belong to the owner. Preserve games, saves,
settings and credentials in place; use device /tmp and remove exact staging
files. Do not leave backups, release archives or installer clutter on an SD.
