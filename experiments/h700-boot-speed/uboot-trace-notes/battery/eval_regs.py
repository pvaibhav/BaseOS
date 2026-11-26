#!/usr/bin/env python3
"""Evaluate the RG SP U-Boot battery checks from an AXP2202/AXP717 register dump.

Input (file argument or stdin), either format:
  regmap debugfs:  "a4: 5c"           (one register per line)
  i2cdump -y N 0x34:  "a0: 00 11 22 ... ff"   (16 per row)
DT values are the ones in the RG SP boot package (all eleven builds agree).
"""
import re, sys

SAFE_VOL, SAFE_RATIO = 3450, 1          # /soc/charger0 pmu_safe_vol, pmu_safe_ratio (absent -> 1)
NTC_STATUS, CHARGE_MODE = 0, 1          # /soc/power_sply ntc_status, charge_mode (absent -> 1)
BATTERY_EXIST_DT, BAT_BMP_TYPE = 1, 0   # absent -> 1 / 0
SAFE_TEMP_H, SAFE_TEMP_L = 600, 0       # absent -> 600 / 0 (0.1 degC)
TEMP_PARA = [-1] * 16                   # /soc/charger0/pmu_bat_temp_paraN absent -> -1

def parse(text):
    r = {}
    for line in text.splitlines():
        m = re.match(r'\s*([0-9a-fA-F]{1,2}):\s*((?:[0-9a-fA-FxX]{2}\s*)+)', line)
        if not m: continue
        base, vals = int(m.group(1), 16), m.group(2).split()
        if len(vals) == 1: r[base] = int(vals[0], 16)
        else:
            for i, v in enumerate(vals[:16]):
                if v.upper() != 'XX': r[base + i] = int(v, 16)
    return r

def temp(raw14, t):
    """0x3bf6c: returns 0.1 degC."""
    v = raw14 >> 1
    pts = [-250, -150, -100, -50, 0, 50, 100, 200, 300, 400, 450, 500, 550, 600, 700, 800]
    if v < t[15]: return 800
    for k in range(14, -1, -1):           # interval (t[k+1], t[k]] -> pts[k]..pts[k+1]
        if v <= t[k]:
            return pts[k] + int((pts[k + 1] - pts[k]) * (t[k] - v) / ((t[k] - t[k + 1]) or 1))
    return -250

def main():
    r = parse(open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read())
    need = [0x00, 0x20, 0xa4, 0xc4, 0xc5, 0xf0]
    miss = [hex(x) for x in need if x not in r]
    if miss: sys.exit(f'missing registers: {miss}')
    bat = (r[0x00] >> 3) & 1; vbus = (r[0x00] >> 5) & 1
    vol = ((r[0xc4] & 0x3f) << 8) | r[0xc5]; soc = r[0xa4]
    print(f'reg00={r[0]:#04x} battery_present={bat} vbus_good={vbus}  bat_vol={vol} mV  soc(reg a4)={soc}')
    # probe 0x3c324
    print('probe reset 0x3c324 (2.2 s):', 'YES' if BATTERY_EXIST_DT and not bat else 'no', '(reg00 bit3 at probe time)')
    # gauge reset 0x3c4dc, first SOC read in 0x7660
    if not bat: g = 'skipped (no battery)'
    elif soc == 0 and vbus: g = 'YES, 1000 ms (soc==0 with VBUS)'
    elif soc == 0 and vol > 3700: g = 'reset, no wait (soc==0, no VBUS, vol>3700)'
    elif soc > 60 and vol <= 3499: g = 'YES, 1000 ms (soc>60, vol<=3499)'
    else: g = 'no (pass)'
    print('gauge reset 0x3c4dc:', g)
    # 0x7660 -> gd->0xf0
    if not bat: f0 = 3
    elif SAFE_RATIO > soc: f0 = 2 if vbus else 1
    elif SAFE_VOL > vol: f0 = 4
    else: f0 = 3
    print('power-init result gd->0xf0 =', f0, {1: 'soc 0, no VBUS', 2: 'soc 0, VBUS', 3: 'ok', 4: f'bat_vol < {SAFE_VOL}'}[f0])
    # 0x3c2d2 / 0x7d50 -> gd->0xd0
    src = {0x01: 0, 0x02: 1, 0x04: (3 if bat else 2), 0x08: 3, 0x10: 4}.get(r[0x20], -1)
    names = {0: 'button', 1: 'irq', 2: 'usb', 3: 'charger', 4: 'battery', -1: 'unknown'}
    if src == -1 and (r[0xf0] & 8): name, d0 = 'charger (reg f0 bit3)', 1
    else: name, d0 = names[src], int(src == 3)
    d0 = d0 if BATTERY_EXIST_DT else 0
    print(f'reg20={r[0x20]:#04x} bootreason={name} -> gd->0xd0 (charger mode) = {d0}')
    # 0x7b44
    if not bat: print('0x7b44: no battery -> returns at once, charger mode cleared'); return
    if NTC_STATUS > 0:
        if 0xce in r and 0xcf in r:
            t = temp(((r[0xce] & 0x3f) << 8) | r[0xcf], TEMP_PARA)
            print('temperature', t)
    else:
        print('0x7b44: ntc_status=0 -> temperature check skipped, TS not read')
    if d0 == 1:
        if f0 in (1, 2, 4): print('0x7b44: charger mode + low battery -> 0x776c loop: 10 ms per pass until '
                                 f'bat_vol>={SAFE_VOL} and soc>={SAFE_RATIO}; power off after 3 s if VBUS goes away')
        elif CHARGE_MODE == 2: print('0x7b44: charge_mode 2 -> 0x7904 wait for power key')
        else: print('0x7b44: charger mode, battery ok -> returns at once (bootargs get androidboot.mode=charger)')
    elif f0 in (1, 4): print('0x7b44: draws bat\\bat0.bmp, waits 3000 ms, powers off (reg 27 bit0)')
    else: print('0x7b44: returns at once')

if __name__ == '__main__':
    main()
