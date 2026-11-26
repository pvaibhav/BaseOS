import json, pickle, sys, collections
sys.path.insert(0, '/tmp/uboot-delays/scripts')
from inventory import T
import diffkeys
OUT = '/tmp/uboot-delays'
S = json.load(open(f'{OUT}/summary.json'))
INV = {t: json.load(open(f'{OUT}/inventory-{t}.json')) for t in T}

def initcols(t):
    i = S[t]['flow'].get('init', {})
    if 'total' in i: return f"{i['total']} (no strap read)", f"{i['total']} (no strap read)", i
    return str(i['total_fall']), str(i['total_taken']), i
def seq(l): return ' + '.join(str(v) for a, v in l) if l else '-'

L = []
L.append('# U-Boot delay inventory: per-build summary\n')
L.append('Static analysis of the U-Boot item (file offset 0x800, 1 MiB, linked at 0x4A000000) of each boot-package.bin. '
         'Scripts: /tmp/uboot-delays/scripts (ub.py = disassembly core, inventory.py = per-build inventory, gen.py = JSON + sums, report.py = these files). '
         'All offsets are relative to 0x4A000000. "board" = code at or above 0x26000, "common" = below 0x26000.\n')
L.append('Panel open flow: drv_lcd_open_callback 0x1fa98 runs flow step i, then arms add_timer(flow[i].delay). Steps after the first run in the timer IRQ handler, so '
         'the busy-wait delays inside a step (the panel init) stall whatever the CPU is doing at that time. The timer gaps do not block the CPU.\n')
L.append('Panel init: lcd_type = !(PE data bit 6) (eor 0x40, ubfx #6) in every build that reads the strap. "PE6 low" = lcd_type 1 = env lcd_type "boe" = fall-through of `cmp lcd_type,#1; bne`. '
         '"PE6 high" = lcd_type 0 = env "old" = taken branch.\n')
L.append('| build | DT lcd_driver_name (x*y) | flow gaps ms | panel init ms, PE6 low | panel init ms, PE6 high | other board delays: const sites / sum once ms / with loop counts ms | common: const sites / sum once / with loop counts | variable-arg sites (board+common) | timeout loops with const limit (>=500 ms) |')
L.append('|---|---|---|---|---|---|---|---|---|')
for t in T:
    s = S[t]; f = s['flow']; dt = INV[t]['dt_lcd']
    lo, hi, i = initcols(t)
    b = s['board']; c = s['common']
    big = [x for x in s['timeouts'] if x[2] >= 500]
    L.append(f"| {t} | {dt['lcd_driver_name']} ({dt['lcd_x']}x{dt['lcd_y']}) | {'/'.join(map(str, f['gaps']))} = {f['gap_sum']} | {lo} | {hi} | {b['n_const']} / {b['sum_once_ms']} / {b['sum_with_loop_counts_ms']} | {c['n_const']} / {c['sum_once_ms']} / {c['sum_with_loop_counts_ms']} | {b['n_variable']}+{c['n_variable']} | {len(s['timeouts'])} ({len(big)}) |")
L.append('\n"sum once" adds every constant delay site once. "with loop counts" multiplies sites in countdown loops by the resolved count; loops whose bound is a hardware/memory condition count once. These are static sums over all code, most of which never runs on a normal boot. The panel init function is excluded from the "other board" column.\n')
L.append('Panel flow total (busy + timer gaps) = gaps + panel init: see the table. The non-init flow steps (power on, tcon enable, backlight) contain no delay calls in any build (searched 5 call levels deep, through the lcd source-op pointers).\n')
for t in T:
    s = S[t]; f = s['flow']; i = f.get('init', {})
    L.append(f'\n## {t}\n')
    L.append(f"- udelay wrapper (lib/time.c): {s['udelay']}; sunxi_lcd_delay_ms: {', '.join(s['lcd_delay'])} (bx *(src_ops+0), pointer filled at run time by 0x2008c; it resolves to disp_delay_ms 0x24dd4)")
    pan = [p for p in INV[t]['panels'] if p['selected_by_dt']][0]
    L.append(f"- panel struct {pan['struct']} '{pan['name']}', open flow {pan['open_flow']}: " + ', '.join(f"{st['func']} then {st['delay_ms']} ms" for st in pan['steps']))
    L.append(f"- other panel structs in the build: " + '; '.join(f"{p['name']} flow {p['open_flow']} " + '/'.join(str(st['delay_ms']) for st in p['steps']) for p in INV[t]['panels'] if not p['selected_by_dt']))
    if 'total' in i:
        L.append(f"- panel init {i['func'] if isinstance(i['func'], str) else hex(i['func'])}: no strap read; delays in order: {seq([(a, v) for a, v in i['linear']])} = {i['total']} ms")
    else:
        L.append(f"- panel init {hex(i['func'])}: before the strap branch {seq(i['prefix'])}; PE6 low: {seq(i['fall'])}; PE6 high: {seq(i['taken'])}; after the join: {seq(i['shared'])}. Totals {i['total_fall']} / {i['total_taken']} ms. Branch: {i['cond']}")
    L.append(f"- variable-argument delay sites: " + ', '.join(f'{a} in {f_} ({n})' for a, f_, n in s['board']['variable_sites'] + s['common']['variable_sites']))
    L.append(f"- timeout loops with a constant limit (site, function, limit ms, compare, region, reachable after logo):")
    for x in s['timeouts']: L.append(f"  - {x[0]} in {x[1]}: {x[2]} ms ({x[3]}) {x[4]}{' post-logo' if x[5] else ''}")
    L.append(f"- timeout loops with a variable limit: " + ', '.join(f'{a} in {f_}' for a, f_, c_, r in s['timeouts_variable']))
    L.append(f"- add_timer sites: " + '; '.join(f'{a} in {f_}: {e}' for a, f_, e, st in s['add_timer']))
open(f'{OUT}/summary.md', 'w').write('\n'.join(L) + '\n')
print('summary.md written')

# ------------------------------------------------------------------ diff.md
K = diffkeys.K
def sig(lst): return sorted((n, r0, lp, lb) for (s, f, n, r0, lp, lb, reg, post) in lst)
def groupeq(a, b, keysA, keysB):
    A = sum((K[a][k] for k in keysA), []); B = sum((K[b][k] for k in keysB), [])
    return sig(A) == sig(B)
D = []
D.append('# Delay sites: rg34xxsp against rgsp, and all eleven builds\n')
D.append('## 1. rg34xxsp against rgsp: no difference\n')
D.append('The two U-Boot items are the same build of the same source and configuration, made on different days. Checked three ways (scripts in /tmp/uboot-delays/scripts):\n')
D.append('- codeeq.py: every 32-bit word in 0..code_end (0x73220 in both) is identical, except the item header checksum at 0xc and literal-pool words that point into .rodata. All but one of those differ by +3. The exception is 0x66b6c, a pointer to the string "39": rgsp 0x4a09c578, rg34xxsp 0x4a07c57e.')
D.append('- dataeq.py: .rodata/.data from 0x73000 to the end of the item match, apart from one 3-byte shift. The only differences in content are the build stamp strings: "U-Boot 2018.05 (Jun 24 2026 - 19:09:39 +0800)", "06/24/2026" and "19:09:39" in rgsp, against "U-Boot 2018.05 (May 26 2026 - 15:48:53 +0800)", "05/26/2026" and "15:48:53" in rg34xxsp. The shift has a simple cause. In rgsp the linker tail-merges the string "39" into "19:09:39". In rg34xxsp it cannot, so it emits a separate "39" at 0x7c57e. Three bytes of alignment padding at 0x9d815 in rgsp absorb the shift again.')
D.append('- Every delay site, timeout loop, add_timer site and panel-flow table entry is at the same offset with the same constant in both builds: inventory-rgsp.json and inventory-rg34xxsp.json are identical apart from the build name. Both builds carry the same three panel structs (default_lcd, bp101wx1 and rg34xxsp_v1 at 0xa40e8, flow 0x27bb4: 0x27c58 then 30 ms, 0x262e4 then 50 ms, 0x26230 then 100 ms, 0x27c18) and the same panel init 0x262e4. The DT of both selects rg34xxsp_v1 720x480.\n')
D.append('Therefore no delay constant, loop bound or timeout limit in U-Boot explains the 1.5 s. The same code must take a different path on the two devices, or wait on hardware for longer. Section 3 lists the waits in this identical code whose length depends on hardware state or data.\n')

D.append('## 2. All eleven builds\n')
D.append('Apart from the panel init function, every delay site, timeout limit and add_timer site in every build matches rgsp. Matching is by function (string set, or a position-independent mnemonic signature for functions without strings). Some functions are split differently between builds (for example the r_i2c init 0x3d2e4/0x3d368 in rgsp, merged into one function in the others) or have different string sets. These carry the same delay multiset and are listed as "equivalent". The sums in the summary table are therefore the same for all builds, except the panel init column.\n')
D.append('| build | DT panel | panel init function | delays before strap branch | PE6 low (lcd_type 1, "boe") | PE6 high (lcd_type 0, "old") | after join | total low / high ms | flow gaps ms |')
D.append('|---|---|---|---|---|---|---|---|---|')
for t in T:
    i = S[t]['flow'].get('init', {}); dt = INV[t]['dt_lcd']['lcd_driver_name']
    nm = [st for p in INV[t]['panels'] if p['selected_by_dt'] for st in p['steps'] if 'strap' in st][0]
    fstr = [x for x in nm['func_strings'] if 'init' in x or 'allen' in x][:2]
    if 'total' in i:
        D.append(f"| {t} | {dt} | {nm['func']} {fstr} | {seq(i['linear'])} (no PE read) | - | - | - | {i['total']} / {i['total']} | 180 |")
    else:
        D.append(f"| {t} | {dt} | {nm['func']} {fstr} | {seq(i['prefix'])} | {seq(i['fall'])} | {seq(i['taken'])} | {seq(i['shared'])} | {i['total_fall']} / {i['total_taken']} | 180 |")
D.append('\nIn every build that reads the strap, the delays before the branch are the panel reset sequence on PI14 (0x0300b130 bit 14): high, 15 ms; low, 50 ms; high, 100 ms. The delays after the branch are the sleep-out (0x11) and display-on (0x29) waits of the bit-banged init commands. Site offsets per build are in summary.md and in inventory-<build>.json under panels[].steps[].strap.\n')
D.append('### Differences from rgsp, per build\n')
for t in T:
    if t == 'rgsp': continue
    oa, ob, ch, pr = diffkeys.compare('rgsp', t)
    # group by first string to drop boundary artefacts
    lines = []
    real_a = []; real_b = []; equiv = []
    allk = set(oa) | set(ob) | set(ch)
    groups = collections.defaultdict(lambda: ([], []))
    for k in oa + ch: groups[k[0]][0].append(k)
    for k in ob + ch: groups[k[0]][1].append(k)
    for g, (ka, kb) in groups.items():
        ka2 = [k for k in K['rgsp'] if k[0] == g]; kb2 = [k for k in K[t] if k[0] == g]
        if groupeq('rgsp', t, ka2, kb2): equiv.append(g); continue
        lines.append((g, ka2, kb2))
    if t == 'rg34xxsp': D.append('**rg34xxsp**: identical to rgsp (section 1).'); continue
    D.append(f'**{t}**: ' + ('no delay difference outside the panel init.' if all('lcd_panel' in ' '.join(sum((list(k) for k in ka + kb), [])) or 'allen' in g for g, ka, kb in lines) else ''))
    for g, ka, kb in lines:
        A = sorted(sum((K['rgsp'][k] for k in ka), [])); B = sorted(sum((K[t][k] for k in kb), []))
        strs = sorted(set(sum((list(k) for k in ka + kb), [])))[:6]
        D.append(f"- function with strings {strs}: rgsp " + ', '.join(f'{x[0]:#x}:{x[3]}' for x in A) + f" | {t} " + ', '.join(f'{x[0]:#x}:{x[3]}' for x in B))
    if equiv or pr: D.append(f"- equivalent (same delays, different function boundary or strings): {[e[:40] for e in equiv]} " + ', '.join(f'{a[0][:30]!r}<->{b[0][:30]!r}' for a, b in pr))
D.append(open('/tmp/uboot-delays/scripts/diff-tail.md').read())
open(f'{OUT}/diff.md', 'w').write('\n'.join(D) + '\n')
print('diff.md part 1-2 written')
