import sys, json, re, hashlib, collections
sys.path.insert(0, '/tmp/uboot-delays/scripts')
from inventory import Inv, T, COMMON_END
import fdt

OUT = '/tmp/uboot-delays'
HEXK = {'site', 'func', 'target', 'loop_branch', 'loop_head', 'compare_at', 'struct', 'open_func', 'cfg_panel_info', 'open_flow',
        'close_flow', 'branch_at', 'taken_target', 'fall_target'}
def hexify(o, k=None):
    if isinstance(o, dict): return {kk: hexify(v, kk) for kk, v in o.items()}
    if isinstance(o, (list, tuple)):
        if k in ('path', 'pe_data_reads'): return [hex(x) for x in o]
        return [hexify(v) for v in o]
    if k in HEXK and isinstance(o, int): return hex(o)
    return o

def dt_panel(t):
    b = open(f'/Users/pvaibhav/Code/Me/BaseOS/work/uboot-trace/work/{t}/boot-gzip/boot-package.bin', 'rb').read()
    d = fdt.parse(b, 0x11b800)
    g = lambda s: [v for k, v in d.items() if 'lcd0@' in k and k.endswith(':' + s)][0]
    return g('lcd_driver_name').rstrip(b'\0').decode(), int.from_bytes(g('lcd_x'), 'big'), int.from_bytes(g('lcd_y'), 'big')

def ms(d):
    v = d['r0']
    if not isinstance(v, int): return None
    return v / 1000.0 if d['unit'] == 'us' else float(v)

def iters(d):
    if not d.get('in_loop'): return 1
    lb, lc = d.get('loop_bound'), d.get('loop_cond') or ''
    if isinstance(lb, int) and lb > 0 and lc.startswith('countdown'):
        return lb - 1 if 'at head' in lc else lb
    return None

Inv.REF = Inv('rgsp')
R = {}; INV = {}
for t in T:
    inv = Inv.REF if t == 'rgsp' else Inv(t)
    name, x, y = dt_panel(t)
    r = inv.run(name)
    r['dt_lcd'] = dict(lcd_driver_name=name, lcd_x=x, lcd_y=y)
    # annotate region and ms
    for d in r['delay_sites']:
        d['region'] = 'common' if d['func'] < COMMON_END else 'board'
        d['const_ms'] = ms(d)
        d['loop_iterations'] = iters(d) if d['in_loop'] else 1
    for d in r['timeout_sites']: d['region'] = 'common' if d['func'] < COMMON_END else 'board'
    R[t] = r; INV[t] = inv
    json.dump(hexify(r), open(f'{OUT}/inventory-{t}.json', 'w'), indent=1)
    print('wrote', t, file=sys.stderr)

# ---------- per-build summary
def panel_of(r):
    return [p for p in r['panels'] if p['selected_by_dt']][0]

def flow_summary(r):
    p = panel_of(r)
    gaps = [s['delay_ms'] for s in p['steps']]
    init = [s for s in p['steps'] if 'strap' in s]
    out = dict(panel=p['name'], gaps=gaps, gap_sum=sum(gaps), steps=[(s['func'], s['delay_ms'], [(d['site'], d['r0']) for d in s['delay_sites']]) for s in p['steps']])
    if init:
        s = init[0]; st = s['strap']
        allsites = [(d['site'], d['r0']) for d in s['delay_sites'] if d['func'] == s['func']]
        if st['branches']:
            br = st['branches'][0]
            pre = sum(v for a, v in br['before']); sh = sum(v for a, v in br['shared_after'])
            out['init'] = dict(func=s['func'], prefix=br['before'], cond=br['cond'] + ' ; ' + br['insn'], derivation=br['derivation'],
                               fall=br['fall_delays'], taken=br['taken_delays'], shared=br['shared_after'],
                               total_fall=pre + sum(v for a, v in br['fall_delays']) + sh, total_taken=pre + sum(v for a, v in br['taken_delays']) + sh)
        else:
            tot = sum(v for a, v in allsites if isinstance(v, int))
            out['init'] = dict(func=s['func'], linear=allsites, total=tot)
    # delays in other step functions (not the panel init)
    out['other_step_delays'] = [(s['func'], [(d['site'], d['r0'], d['target_name']) for d in s['delay_sites']]) for s in p['steps'] if 'strap' not in s and s['delay_sites']]
    return out

def region_sum(r, region, post_only=False, exclude_funcs=()):
    ds = [d for d in r['delay_sites'] if d['region'] == region and d['func'] not in exclude_funcs and (not post_only or d['post_logo_reachable'])]
    # skip the primitive-internal forwarding sites (wrappers forwarding r0)
    ds = [d for d in ds if d['func'] not in INVP[r['build']]]
    const = [d for d in ds if d['const_ms'] is not None]
    var = [d for d in ds if d['const_ms'] is None]
    s1 = sum(d['const_ms'] for d in const)
    worst = 0.0; unb = 0
    for d in const:
        it = d['loop_iterations']
        if it is None: unb += 1; worst += d['const_ms']
        else: worst += d['const_ms'] * it
    return dict(n_sites=len(ds), n_const=len(const), n_variable=len(var), sum_once_ms=round(s1, 3), sum_with_loop_counts_ms=round(worst, 3), loops_unbounded=unb,
                variable_sites=[(hex(d['site']), hex(d['func']), d['target_name']) for d in var])

INVP = {t: set(INV[t].prims) for t in T}
SUM = {}
for t in T:
    r = R[t]; fs = flow_summary(r)
    pinit = fs['init']['func'] if 'init' in fs else None
    to = [d for d in r['timeout_sites'] if isinstance(d.get('limit_ms'), int)]
    tv = [d for d in r['timeout_sites'] if d.get('limit_ms') == 'variable']
    SUM[t] = dict(flow=fs, board=region_sum(r, 'board', exclude_funcs=(pinit,)), common=region_sum(r, 'common'),
                  board_post=region_sum(r, 'board', True, (pinit,)), common_post=region_sum(r, 'common', True),
                  timeouts=[(hex(d['site']), hex(d['func']), d['limit_ms'], d['compare'], d['region'], d['post_logo_reachable']) for d in to],
                  timeouts_variable=[(hex(d['site']), hex(d['func']), d['compare'], d['region']) for d in tv],
                  add_timer=[(hex(d['site']), hex(d['func']), d['expires_ms'], d['store']) for d in r['add_timer_sites']],
                  udelay=hex(INV[t].udelay), lcd_delay=[hex(k) for k, v in INV[t].prims.items() if v[0] == 'sunxi_lcd_delay_ms'])
json.dump(SUM, open(f'{OUT}/summary.json', 'w'), indent=1, default=hex)

# ---------- function keys for cross-build matching
def fkey(t, f):
    inv = INV[t]; b = inv.b
    strs = tuple(b.strings_of(f))
    from inventory import mnem_sig
    sig = hashlib.sha1('\n'.join(mnem_sig(b, f)).encode()).hexdigest()[:10]
    return strs if strs else ('sig:' + sig,)
KEYS = {}
for t in T:
    m = collections.defaultdict(list)
    for d in R[t]['delay_sites']:
        if d['func'] in INVP[t]: continue
        m[fkey(t, d['func'])].append(d)
    KEYS[t] = m
json.dump({t: len(KEYS[t]) for t in T}, sys.stderr)
import pickle
pickle.dump(dict(R=R, SUM=SUM, KEYS={t: {k: [(d['site'], d['func'], d['target_name'], d['r0'], d['in_loop'], d.get('loop_bound'), d['region'], d['post_logo_reachable']) for d in v] for k, v in KEYS[t].items()} for t in T}), open(f'{OUT}/scripts/state.pkl', 'wb'))
