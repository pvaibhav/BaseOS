#!/usr/bin/env python3
"""Write exact owner handoff notes from the built RG SP artifacts; no device access."""
import hashlib
import json
from pathlib import Path
R=Path(__file__).resolve().parents[2]
W=R/'work/v2/firmware/v2-rgsp'
m=json.loads((W/'manifest.json').read_text())
p=R/'work/v2/os/v2-rgsp/image.bin'
image=p.read_bytes()
assert image[32:64].hex()==m['generation']
sha=lambda b:hashlib.sha256(b).hexdigest()
cap=(W/'capsule.egon.bin').read_bytes()
spec=json.loads((R/'v2/firmware/targets.json').read_text())['rgsp']
rows=[]
for name,data,location in [('firmware.bin',(W/'firmware.bin').read_bytes(),'p4 sector 81920; disk LBA 385024'),('image.bin',image,'p4 sector 98304; disk LBA 401408'),('capsule.egon.bin',cap,'disk LBA 16; byte 8192')]:
 rows.append(f'| {name} | {len(data)} | {len(data)//512} | {location} | `{sha(data)}` |')
(W/'INSTALL.md').write_text(f'''# RG SP BaseOS 2 candidate: v2-rgsp

Build and offline emulation only. This candidate has never run on a handheld.
Use only on the known RG SP hardware covered by the stock evidence, after review.
PE6 high selects old, low selects boe, with pull-up. The stock LPDDR3/LPDDR3/LPDDR4
banks and existing 1200/1100 mV DRAM settings are retained. There is no reliable
new-board/panel discriminator. Unknown revisions must remain on stock.

## Identity and payload

Generation: `{m['generation']}`

RTC cookie: `{m['cookie']}`; pending: `0x42535031`.

Stock recovery boot0: 65536 bytes, SHA-256 `{spec['boot0_sha256']}`.
Stock TOC package SHA-256: `{spec['package_sha256']}`.
The capsule embeds the complete checked stock boot0 at offset 4096 (0x1000).
Its SPL is at offset 69632 (0x11000), length 49152; capsule length 131072.
Firmware FNV-1a: `{m['files']['firmware.bin']['fnv']}`.
BL31/helper extents: `{json.dumps(m['firmware_regions'])}`.

Build a matching payload:

    python3 v2/os/build.py payload --firmware v2-rgsp --dtb work/v2/kernel/rgsp.dtb

Payload: `work/v2/os/v2-rgsp/image.bin`, BOS3 v1, one independent RG SP DTB,
empty Android DTB. Firmware rejects a mismatched generation; helper rejects
an independent DTB without root compatible `anbernic,rgsp` before panel startup.
Do not restamp an RG28XX payload for this board.

## Exact write locations

p4 must start at disk LBA 303104. Check actual card geometry before any writes.

| File | Bytes | Sectors | Start | SHA-256 |
|---|---:|---:|---|---|
{chr(10).join(rows)}

Capsule body (bytes 512 onward) SHA-256: `{sha(cap[512:])}`.
Firmware occupies disk LBAs 385024–{385024+m['files']['firmware.bin']['bytes']//512-1};
payload occupies 401408–{401408+len(image)//512-1}; capsule occupies 16–271.
Keep the secondary stock boot0 at disk LBA 512–639 untouched.

## Owner write order (not performed by this build)

1. Verify this is the qualified RG SP stock installation and verify the exact
   stock recovery identity and p4 geometry. Stage only the three files above
   in a new device `/tmp` directory and check their hashes.
2. Hold stock with `/usr/sbin/native-health --hold-stock`; confirm
   `--stock-latch` ends in ` held`. Keep the hold through every write/check.
3. Write firmware to `/dev/mmcblk0p4` sector 81920, then payload to sector 98304,
   using 512-byte blocks, `conv=notrunc,fsync` and direct I/O. Read back exactly
   the sector counts above and compare SHA-256.
4. Write capsule last: source sector 1 to disk sector 17, count 255; verify the
   body hash above. Then source sector 0 to disk sector 16, count 1; verify the
   whole capsule hash. Do not restart on a mismatch.
5. Verify the secondary stock region is untouched. Release with
   `/usr/sbin/native-health --release-stock`; confirm `--stock-latch` ends in
   ` idle`. Remove the exact `/tmp` staging directory.

An interrupted primary capsule write can require external SD card repair.
BROM recovery from LBA 512 is unproved. Repair with the pinned stock boot0 at
LBA 16 from the prepared inputs; no backup copy is required or created.
Preserve games, saves, save states, credentials and settings in place. Leave
no archives, scripts, manifests or rollback copies on the card.

## Offline evidence and limits

`tests.json` beside this file reports compiled-chain emulation. DRAM training,
PMIC behavior, clocks, card and system registers are modeled. Refusal cases
retain RTC pending; the chain test stops at refusal, while separate capsule
emulation proves a nonzero pair selects stock on the next entry. This does not
prove a physical watchdog reset. Missing logo deliberately boots a black frame.
RG SP DTB provides the disabled simplefb template (720x480, stride 2880),
chosen trial marker, PSCI SMC and secure-memory reservation; finalization writes
RAM, trained words, serial, panel token, reason, boot-display and simplefb metadata.

No hardware results, electrical qualification, boot time or suspend claims.
The AXP717 SPL path has no trustworthy chip-ID discriminator. The existing OS
clears RTC early in rcS; that acknowledgment does not prove screen/input health.

## Ordered owner checks

1. Confirm held-stock boots before release, then one supervised cold trial;
   confirm visible logo, 720x480 colors/orientation and clean Linux takeover.
2. Confirm detected RAM, panel token, boot reason, simplefb reservation and
   retained watchdog; repeat cold boots/restarts. Test each available known
   RAM/panel revision (both PE6 choices require their physical hardware).
3. Deliberately refuse a trial with a mismatched payload; confirm watchdog reset
   and the following stock boot. Restore the matching payload while held.
4. Check every button, volume, lid, charging-only start, battery readings,
   shutdown/restart and RAM stress. Verify power before extended use.
5. Test suspend/resume and lid wake last; check screen, controls and audio after
   wake. Mainline/BL31 suspend behavior remains unqualified.
''')
print(W/'INSTALL.md')
