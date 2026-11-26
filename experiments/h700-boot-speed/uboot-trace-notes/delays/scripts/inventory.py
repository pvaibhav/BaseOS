import sys, json, re, collections
sys.path.insert(0, '/tmp/uboot-delays/scripts')
from ub import Build, BASE

T = "rg28xx rg34xx rg34xxsp rg35xxh rg35xxplus rg35xxpro rg35xxsp rg40xxh rg40xxv rgcubexx rgsp".split()
COMMON_END = 0x26000
FIXED = {0xd50: ('__udelay', 'us'), 0xd74: ('__usdelay', 'us'), 0xd78: ('__msdelay/mdelay', 'ms'),
         0xeac8: ('mdelay_loop', 'ms'), 0x24dd4: ('disp_delay_ms', 'ms'), 0x24dde: ('disp_delay_us', 'us')}
TIMERS = {0xd84: 'get_timer_masked', 0xde4: 'get_timer_masked_alias', 0xde8: 'get_timer'}
ADD_TIMER = 0xe14
LCD_OPEN_CB = 0x1fa98
SRC_OPS_FILL = 0x2008c   # disp_lcd_get_source_ops: stores op pointers into the panel driver's ops struct

def hx(v): return hex(v) if isinstance(v, int) else v

def mnem_sig(b, f):
    """position-independent signature of a function (mnemonics, immediates kept, addresses dropped)"""
    _, insns, _ = b.explore(f)
    out = []
    for a in sorted(insns):
        i = insns[a]; m = i.mnemonic; o = i.op_str
        if m.split('.')[0] in ('bl', 'blx', 'b', 'cbz', 'cbnz') or m[:1] == 'b' and '#' in o: o = re.sub(r'#0x[0-9a-f]+', '#T', o)
        if '[pc' in o: o = 'LIT'
        out.append(m + ' ' + o)
    return out

class Inv:
    def __init__(self, name):
        self.name = name
        b = self.b = Build(name, f'/tmp/uboot-delays/bin/{name}.bin')
        self.notes = []
        self.prims = {}  # offset -> (name, unit, how)
        for o, (n, u) in FIXED.items(): self.prims[o] = (n, u, 'common primitive')
        self._verify_fixed()
        self._find_udelay()
        self._src_ops()
        self._wrappers()

    def _verify_fixed(self):
        ref = Inv.REF if hasattr(Inv, 'REF') else None
        for o in list(FIXED) + list(TIMERS) + [ADD_TIMER, LCD_OPEN_CB, SRC_OPS_FILL]:
            if ref is not None and mnem_sig(self.b, o) != mnem_sig(ref.b, o):
                self.notes.append(f'WARNING: {hex(o)} differs from rgsp')

    def _find_udelay(self):
        b = self.b; cands = []
        for s, k in b.callers.get(0xd50, []):
            f = b.owner(s)
            if f is None or f < COMMON_END: continue
            R, _ = b.regconsts(f, s, back=40)
            _, insns, _ = b.explore(f)
            if any(i.mnemonic.startswith('ldr') and '[pc' in i.op_str and b.w(b.litaddr(i)) == 10000000 for i in insns.values()):
                cands.append(f)
        cands = sorted(set(cands))
        assert len(cands) == 1, (self.name, cands)
        self.udelay = cands[0]
        self.prims[self.udelay] = ('udelay', 'us', 'lib/time.c udelay: chunks of 10,000,000 us -> __udelay')

    def _src_ops(self):
        b = self.b
        # which pointer does the panel driver pass to disp_lcd_get_source_ops?
        self.srcops_ptr = []
        for s, k in b.callers.get(SRC_OPS_FILL, []):
            f = b.owner(s); R, _ = b.regconsts(f, s)
            if isinstance(R.get('r0'), int): self.srcops_ptr.append((f, R['r0']))
        # op offset -> function, from the stores in 0x2008c
        _, insns, _ = b.explore(SRC_OPS_FILL)
        R = {}; self.ops = {}
        for a in sorted(insns):
            i = insns[a]
            m = re.match(r'r(\d), \[r4(?:, #(0x[0-9a-f]+|\d+))?\]$', i.op_str)
            if i.mnemonic == 'str' and m and isinstance(R.get('r' + m.group(1)), int):
                self.ops[int(m.group(2) or '0', 0)] = (R['r' + m.group(1)] - BASE) & ~1
            b._step(R, i)

    def _wrappers(self):
        """thin wrappers: `ldr rX,=P; ldr rX,[rX,#k]; (cbz); bx rX` through the panel src_ops, and
        small functions whose only call is a tail/BL to a primitive with r0 passed through"""
        b = self.b
        ptrs = set(p for f, p in self.srcops_ptr)
        self.opwrap = {}  # wrapper offset -> op offset
        for f in b.starts:
            if f < COMMON_END: continue
            i0 = b.ins(f); i1 = b.ins(f + i0.size) if i0 else None
            if not (i0 and i1): continue
            if i0.mnemonic == 'ldr' and '[pc' in i0.op_str and b.w(b.litaddr(i0)) in ptrs:
                m = re.match(r'(r\d), \[(r\d)(?:, #(0x[0-9a-f]+|\d+))?\]$', i1.op_str)
                if i1.mnemonic == 'ldr' and m:
                    k = int(m.group(3) or '0', 0)
                    self.opwrap[f] = k
        changed = True
        while changed:
            changed = False
            for f, k in self.opwrap.items():
                tgt = self.ops.get(k)
                if tgt in self.prims and f not in self.prims:
                    n, u, _ = self.prims[tgt]
                    nm = {'disp_delay_ms': 'sunxi_lcd_delay_ms', 'disp_delay_us': 'sunxi_lcd_delay_us'}.get(n, n + '_wrapper')
                    self.prims[f] = (nm, u, f'bx *(src_ops@{hex(self.srcops_ptr[0][1])} + {hex(k)}) -> {hex(tgt)} {n}; pointer filled at runtime by {hex(SRC_OPS_FILL)} called from {hex(self.srcops_ptr[0][0])} (bss, initial word in image is not meaningful)')
                    changed = True
            for f in b.starts:
                if f in self.prims: continue
                calls, insns, br = b.explore(f)
                if len(insns) > 8 or len(calls) != 1: continue
                s, t, k = calls[0]
                if t in self.prims:
                    # r0 must not be modified before the call except by a constant multiply
                    mods = [i for a, i in sorted(insns.items()) if a < s and i.op_str.startswith('r0,')]
                    n, u, _ = self.prims[t]
                    if not mods:
                        self.prims[f] = (f'{n}_wrap_{hex(f)}', u, f'thin wrapper: {k} {hex(t)} with r0 unchanged'); changed = True
                    else:
                        self.notes.append(f'small function {hex(f)} calls {n} with r0 modified: ' + '; '.join(i.mnemonic + ' ' + i.op_str for i in mods))

    def r0(self, f, s):
        R, run = self.b.regconsts(f, s)
        v = R.get('r0')
        if isinstance(v, int): return self.b.sgn(v)
        if isinstance(v, tuple): return f'conditional({hx(v[1])})'
        return 'variable'

    def delay_sites(self):
        b = self.b; out = []
        seen = set()
        for t in self.prims:
            for s, k in b.callers.get(t, []):
                f = b.owner(s)
                covered = f is not None and s in b.explore(f)[1]
                if not covered: continue
                if f == t: continue
                seen.add(s)
                n, u, _ = self.prims[t]
                lp, ls, lt = b.in_loop(f, s)
                lb = b.loop_bound(f, ls, lt) if lp else (None, None)
                out.append(dict(site=s, func=f, kind=k, target=t, target_name=n, unit=u, r0=self.r0(f, s),
                                in_loop=lp, loop_branch=ls, loop_head=lt, loop_bound=lb[0], loop_cond=lb[1]))
        # tail calls found by recursive descent but not by the raw scan (should be none)
        out.sort(key=lambda d: d['site'])
        return out

    def timer_sites(self):
        b = self.b; out = []
        for t, n in TIMERS.items():
            for s, k in b.callers.get(t, []):
                f = b.owner(s)
                if f is None or s not in b.explore(f)[1] or f in TIMERS: continue
                res = self._timeout_after(f, s)
                lp, ls, lt = b.in_loop(f, s)
                out.append(dict(site=s, func=f, kind=k, target=t, target_name=n, r0_arg=self.r0(f, s), in_loop=lp,
                                loop_branch=ls, **res))
        # a timeout loop is a site whose result is compared (elapsed or deadline); attach limit to the start call too

        out.sort(key=lambda d: d['site'])
        return out

    def _timeout_after(self, f, s):
        b = self.b
        R, _ = b.regconsts(f, s)
        _, insns, br = b.explore(f)
        T = {'r0'}; a = s + b.ins(s).size; steps = 0; deadline = None
        R = {k: v for k, v in R.items() if k not in ('r0', 'r1', 'r2', 'r3', 'r12', 'ip', 'lr')}
        while steps < 24 and a in insns:
            i = insns[a]; m = i.mnemonic; base = m.split('.')[0]
            ops = [o.strip() for o in i.op_str.split(',')]
            if base in ('cmp', 'cmn') and len(ops) == 2:
                x, y = ops
                if x in T or y in T:
                    o = y if x in T else x
                    v = b._val(R, o)
                    kind = 'elapsed' if not deadline else 'deadline'
                    return dict(compare_at=a, compare=m + ' ' + i.op_str, limit_ms=b.sgn(v) if v is not None else 'variable', timer_value=kind)
            if base in ('bl', 'blx'):
                return dict(compare_at=None, compare=f'result passed on / call at {hex(a)}', limit_ms=None, timer_value=None)
            if base in ('mov', 'movs') and len(ops) == 2 and ops[1] in T: T.add(ops[0])
            elif base in ('subs', 'sub') and len(ops) == 3 and ops[1] in T: T.add(ops[0])
            elif base in ('adds', 'add') and len(ops) == 3 and ops[1] in T and ops[2].startswith('#'):
                deadline = int(ops[2][1:], 0)
                return dict(compare_at=a, compare=f'deadline = now + {deadline}', limit_ms=deadline, timer_value='deadline')
            elif base in ('add',) and len(ops) == 3 and ops[1] in T:
                v = b._val(R, ops[2])
                return dict(compare_at=a, compare=f'deadline = now + {ops[2]}', limit_ms=v if v is not None else 'variable', timer_value='deadline')
            elif base.startswith('str') and ops[0] in T:
                return dict(compare_at=None, compare=f'stored {m} {i.op_str}', limit_ms=None, timer_value='stored')
            else:
                if ops and ops[0] in T and not base.startswith(('cmp', 'tst', 'str')): T.discard(ops[0])
                b._step(R, i)
            if not T: break
            if base in ('b', 'b.w') and i.op_str.startswith('#'):
                a = int(i.op_str[1:], 16)
            elif base in ('pop', 'bx') or (base == 'b'): break
            else: a += i.size
            steps += 1
        return dict(compare_at=None, compare='no compare found', limit_ms=None, timer_value=None)

    def add_timer_sites(self):
        b = self.b; out = []
        for s, k in b.callers.get(ADD_TIMER, []):
            f = b.owner(s)
            if f is None or s not in b.explore(f)[1]: continue
            R, run = b.regconsts(f, s, back=40)
            _, insns, _ = b.explore(f)
            exp = 'variable'; src = None
            # find store to [base] (offset 0) and its value
            RR = {}
            for a in run:
                i = insns[a]
                m = re.match(r'(r\d), \[(r\d)(?:, (r\d))?\]$', i.op_str)
                if i.mnemonic == 'str' and m:
                    v = RR.get(m.group(1)); exp = b.sgn(v) if isinstance(v, int) else 'variable'; src = f'{hex(a)}: str {i.op_str}'
                b._step(RR, i)
            if f == LCD_OPEN_CB: exp = 'from LCD open flow table (flow[i].delay)'
            out.append(dict(site=s, func=f, kind=k, expires_ms=exp, store=src))
        return out

    def panels(self):
        """panel structs: name[32] followed by thumb code pointers (cfg_panel_info, cfg_open_flow, cfg_close_flow, ...)"""
        b = self.b; out = []
        for a in range(b.code_end, len(b.U) - 0x40, 4):
            nm = b.cstr(a, 4)
            if not nm or not re.match(r'^[a-z0-9_]+$', nm) or len(nm) >= 32: continue
            if b.U[a + len(nm):a + 32].strip(b'\0'): continue
            if a >= 4 and b.U[a - 1] not in (0,): continue
            ptrs = [b.w(a + 32 + 4 * k) for k in range(4)]
            if not all(p & 1 and BASE <= p < BASE + b.code_end for p in ptrs[:3]): continue
            ofl = (ptrs[1] - BASE) & ~1
            steps = self.flow(ofl)
            if not steps: continue
            out.append(dict(name=nm, struct=a, cfg_panel_info=(ptrs[0] - BASE) & ~1, open_flow=ofl,
                            close_flow=(ptrs[2] - BASE) & ~1, steps=steps))
        return out

    def flow(self, f):
        b = self.b; calls, insns, _ = b.explore(f); steps = []
        for s, t, k in calls:
            if t is None: continue
            R, _ = b.regconsts(f, s)
            if isinstance(R.get('r1'), int) and BASE <= R['r1'] < BASE + b.code_end and isinstance(R.get('r2'), int):
                steps.append(dict(site=s, open_func=t, func=(R['r1'] - BASE) & ~1, delay_ms=R['r2']))
        if len(steps) >= 2 and len(set(x['open_func'] for x in steps)) == 1: return steps
        return None

    def reach_delays(self, f, maxd=4, skip=()):
        """delay sites reachable from f (BFS through direct calls and src_ops wrappers)"""
        b = self.b; from collections import deque
        q = deque([(f, 0, [f])]); seen = {f}; res = []
        sites = {}
        for d in self.DS: sites.setdefault(d['func'], []).append(d)
        while q:
            g, dep, path = q.popleft()
            for d in sites.get(g, []): res.append(dict(d, path=path))
            if dep >= maxd: continue
            calls, insns, _ = b.explore(g)
            nxt = [t for s, t, k in calls if t is not None]
            if g in self.opwrap and self.ops.get(self.opwrap[g]) is not None: nxt.append(self.ops[self.opwrap[g]])
            for t in nxt:
                if t in self.prims or t in seen or t in skip or t in TIMERS or t == ADD_TIMER: continue
                seen.add(t); q.append((t, dep + 1, path + [t]))
        return res

    POST_LOGO_ENTRIES = {0x2b22: 'board_late_init after logo (callees of 0x2b14 except 0x2a2c)', 0xf144: 'initr_net', 0xf13c: 'run_main_loop',
                         0xc1e8: 'do_sunxi_flash', 0x7e70: 'do_bootm', 0x19880: 'mmc_bread', 0x1bbf0: 'sunxi_mmc_send_cmd', 0x3fe04: 'sunxi_flash blk read'}

    def post_logo_funcs(self, maxd=8):
        b = self.b
        hubs = set(t for t, c in b.callers.items() if len(c) > 150)
        ent = [t for s, t, k in b.explore(0x2b14)[0] if t is not None and s > 0x2b1e] + [e for e in self.POST_LOGO_ENTRIES if e != 0x2b22]
        from collections import deque
        seen = set(ent); q = deque((e, 0) for e in ent)
        while q:
            g, d = q.popleft()
            if d >= maxd: continue
            for s_, t, k in b.explore(g)[0]:
                if t is None or t in seen or t in hubs or t in self.prims: continue
                seen.add(t); q.append((t, d + 1))
        return seen

    def strap(self, f):
        """panel init: delays before the PE strap branch, and in each branch"""
        b = self.b
        calls, insns, br = b.explore(f)
        pe = [a for a, i in insns.items() if i.mnemonic.startswith('ldr') and '[pc' in i.op_str and b.w(b.litaddr(i)) == 0x0300B0A0]
        dsites = {d['site']: d for d in self.DS if d['func'] == f}
        res = dict(pe_data_reads=pe, branches=[])
        if not pe: res['note'] = 'no PE data read'; return res
        after = [x for x in br if x[0] > max(pe) and x[2]]
        after.sort()
        for s, t, c in after:
            i = insns[s]; fall = s + i.size
            # skip printf-only small branches: want two big successors
            A = b.explore(t)[1]; B = b.explore(fall)[1]
            if len(A) < 50 or len(B) < 50: continue
            def info(S, other):
                ds = [(hex(a), dsites[a]['r0']) for a in sorted(dsites) if a in S and a not in other]
                strs = [st for st in b.strings_of_set(S) if st in ('boe', 'old')]
                return ds, sorted(set(strs))
            # the branch can be preceded by a "b" chain: follow unconditional b at fall
            pre = [(hex(a), dsites[a]['r0']) for a in sorted(dsites) if a < s and a not in A and a not in B]
            # condition text: preceding cmp
            cmpi = [insns[x] for x in sorted(insns) if s - 8 <= x < s and insns[x].mnemonic.startswith(('cmp', 'tst', 'lsls'))]
            taken = info(A, B); fallt = info(B, A)
            shared = [(hex(a), dsites[a]['r0']) for a in sorted(dsites) if a in A and a in B]
            deriv = [f'{x:#x}: {insns[x].mnemonic} {insns[x].op_str}' for x in sorted(insns) if max(pe) <= x <= s
                     and not insns[x].mnemonic.startswith(('dmb',))]
            res['branches'].append(dict(branch_at=s, insn=f'{i.mnemonic} {i.op_str}', derivation=deriv, cond=' ; '.join(x.mnemonic + ' ' + x.op_str for x in cmpi),
                                        taken_target=t, taken_delays=taken[0], taken_env=taken[1], fall_target=fall, fall_delays=fallt[0], fall_env=fallt[1],
                                        shared_after=shared, before=pre))
            break
        return res

    def run(self, dt_panel=None):
        b = self.b
        self.DS = self.delay_sites()
        TS = self.timer_sites(); AT = self.add_timer_sites(); P = self.panels()
        post = self.post_logo_funcs()
        for d in self.DS + TS: d['post_logo_reachable'] = d['func'] in post
        fnames = {}
        for d in self.DS + TS + AT:
            f = d['func']
            if f not in fnames: fnames[f] = b.strings_of(f)[:12]
        for p in P:
            p['selected_by_dt'] = (p['name'] == dt_panel)
            for st in p['steps']:
                st['delay_sites'] = [dict(site=d['site'], func=d['func'], target_name=d['target_name'], unit=d['unit'], r0=d['r0'], path=d['path'])
                                     for d in self.reach_delays(st['func'], maxd=5)]
                if any(d['func'] == st['func'] for d in st['delay_sites']) and len(b.explore(st['func'])[1]) > 200:
                    st['strap'] = self.strap(st['func'])
                    st['func_strings'] = b.strings_of(st['func'])
        return dict(build=self.name, dt_lcd_driver_name=dt_panel, code_end=b.code_end,
                    primitives={hex(k): dict(name=v[0], unit=v[1], how=v[2]) for k, v in sorted(self.prims.items())},
                    src_ops_ptr=[(hex(f), hex(p)) for f, p in self.srcops_ptr], src_ops={hex(k): hex(v) for k, v in sorted(self.ops.items())},
                    opwrappers={hex(k): hex(v) for k, v in self.opwrap.items()},
                    delay_sites=self.DS, timeout_sites=TS, add_timer_sites=AT, panels=P, func_strings={hex(k): v for k, v in fnames.items()}, notes=self.notes)

def jsonable(o):
    if isinstance(o, dict): return {k: jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [jsonable(v) for v in o]
    return o

