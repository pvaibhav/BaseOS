#!/usr/bin/env python3
"""Generate owner handoffs for the untested fleet, with no device access."""
import hashlib
import json
from pathlib import Path
R=Path(__file__).resolve().parents[2]
W=R/'work/v2/firmware'
profiles={p['id']:p for p in json.loads((R/'devices.json').read_text())['targets']}
for t,spec in json.loads((R/'v2/firmware/targets.json').read_text()).items():
 if t in ('rg28xx','rgsp'):continue
 v='v2-'+t;out=W/v
 if not (out/'manifest.json').exists():continue
 m=json.loads((out/'manifest.json').read_text())
 m.update(hardware_tested=False,release_status='untested candidate')
 (out/'manifest.json').write_text(json.dumps(m,indent=2)+'\n')
 image=R/f'work/v2/os/{v}/image.bin'
 checks=json.loads((out/'tests.json').read_text()) if (out/'tests.json').exists() else {}
 sha=lambda b:hashlib.sha256(b).hexdigest()
 cap=(out/'capsule.egon.bin').read_bytes();profile=profiles[t]
 rows=[]
 for n in ('firmware.bin','capsule.egon.bin'):
  b=(out/n).read_bytes();slot=m['card']['firmware' if n=='firmware.bin' else 'capsule'];lba=slot.get('disk_lba',slot.get('lba'))
  rows.append(f'| {n} | {len(b)} | {len(b)//512} | {lba} | `{sha(b)}` |')
 if image.exists():
  b=image.read_bytes();assert b[32:64].hex()==m['generation']
  rows.append(f'| image.bin | {len(b)} | {len(b)//512} | {m["card"]["image"]["disk_lba"]} | `{sha(b)}` |')
 payload_status='Matching payload built; see its hash below.' if image.exists() else 'Real model DTB/payload checks pending. No installable image.bin is supplied. SYNTHETIC-TEST-ONLY files are emulator fixtures; never write them to a card.'
 panel='fixed boe' if spec['panel_rule']==1 else 'PE6 high=old, low=boe'
 (out/'INSTALL.md').write_text(f'''# {profile['model']} BaseOS 2 candidate: {v}

UNTESTED ON HARDWARE. Build and offline emulation only.
{payload_status}

## Identity

Generation: `{m['generation']}`; RTC cookie `{m['cookie']}`; pending `0x42535031`.
Stock recovery boot0: 65536 bytes, SHA-256 `{spec['boot0_sha256']}`.
Stock TOC package SHA-256: `{spec['package_sha256']}`.
The capsule embeds this model's full stock boot0 at byte 4096, and its 49152-byte
SPL at byte 69632; capsule size is 131072 bytes. Firmware FNV: `{m['files']['firmware.bin']['fnv']}`.
BL31/helper extents: `{json.dumps(m['firmware_regions'])}`.
Panel: {panel}; PE6 pull-up {spec['pull_up']}. Physical logo {spec['logo'][0]}×{spec['logo'][1]},
artwork rotation {profile['panel_rotation_ccw']} degrees CCW. Framebuffer is RAM end minus {spec['framebuffer_from_ram_end']}.
Uses every deduplicated stock parameter bank, the existing 672 MHz clocks and
stock-path LPDDR3/LPDDR4 voltage requests (1200/1100 mV); no new voltages.

Build the real payload when its DTB is available:

    python3 v2/os/build.py payload --firmware {v} --dtb work/v2/kernel/{t}.dtb

Expected payload: `work/v2/os/{v}/image.bin`, BOS3 v1, independent DTB with root
compatible `{spec['compatible']}`, empty Android DTB and matching generation.
Regenerate this handoff after building it: `python3 v2/firmware/write-fleet-install.py`.

## Exact locations

Check p4 starts at disk LBA {m['p4_first_lba']}. Firmware goes to p4 sector 81920,
payload to p4 sector 98304. All sectors are 512 bytes.

| File | Bytes | Sectors | Disk LBA | SHA-256 |
|---|---:|---:|---:|---|
{chr(10).join(rows)}

Capsule body (byte 512 onward) SHA-256: `{sha(cap[512:])}`.
Capsule disk LBAs 16–271. Keep secondary stock boot0 LBAs 512–639 untouched.

## Owner write order — no writes performed here

1. Confirm the exact model, known RAM/panel revision, pinned stock recovery
   identity and card geometry. Unknown revisions must stay on stock. Have a real,
   verified model payload before proceeding. Confirm held-stock boots first.
2. Stage only firmware.bin, real image.bin and capsule.egon.bin in a new device
   `/tmp` directory. Verify the sizes and hashes above. Preserve games, saves,
   save states, credentials and settings in place; create no backups.
3. Hold stock with `/usr/sbin/native-health --hold-stock` and confirm
   `--stock-latch` ends in ` held`. Keep it held throughout writes and readback.
4. Write firmware then payload at the p4 sectors above, using 512-byte blocks,
   `conv=notrunc,fsync` and direct I/O; read back their exact lengths and hashes.
5. Write capsule last: source sector 1 to disk sector 17, count 255; verify body
   hash, then source sector 0 to disk sector 16, count 1; verify whole hash.
   Do not restart on any mismatch. Verify secondary stock remains untouched.
6. Release with `/usr/sbin/native-health --release-stock`; confirm latch ` idle`.
   Remove the exact `/tmp` staging directory. Leave no archives, manifests,
   screenshots, installer scripts or rollback files on the SD card.

Interrupted primary writes can require external card repair. BROM fallback to
LBA 512 is unproved. Pinned prepared stock boot0 can be used for external repair.

## Offline evidence and open risks

`tests.json`: emulated={checks.get('emulated',False)}, failed={checks.get('failed','not run')},
real payload checked={checks.get('real_payload_checked',False)}. All results model
DRAM training, PMIC, clocks, card and system registers. Capsule emulation checks
nonzero RTC pairs choose stock; refusal keeps pending for the next entry.
A physical watchdog reset, RAM electrical behavior and a visible picture are
not proved. Missing logo intentionally succeeds with a black frame.

Stock inputs are pinned and checked; PE6 cannot identify unknown panel revisions,
and AXP717 has no trustworthy revision discriminator in this SPL path. No claim
of automatic refusal of every unknown physical revision is possible. Unknown
owners/revisions remain on stock. OS still clears RTC early in rcS. Linux
watchdog continuity, late health acknowledgment, suspend and power behavior
remain unqualified. Real DTB integration remains pending if a synthetic fixture
is named in tests.json. Prior BaseOS 1.x evidence is not a BaseOS 2 hardware pass.

## First owner checks, in order

1. Verify held-stock startup; make one supervised cold trial, checking visible
   {spec['logo'][0]}×{spec['logo'][1]} logo, colors/orientation and Linux display takeover.
2. Check detected RAM words/size, panel token, boot reason, simplefb reservation
   and retained watchdog. Repeat cold starts and restarts on each known RAM bank
   and physical panel revision; emulating a strap does not qualify a panel.
3. Deliberately refuse a mismatched payload, then confirm physical reset and stock
   fallback. Restore the matching payload while held.
4. Check buttons, sticks where fitted, volume, lid where fitted, battery/charging,
   charging-only start, shutdown/restart and RAM stress.
5. Test suspend/resume last, including display, input and audio after wake.
''')
 print(out/'INSTALL.md')
