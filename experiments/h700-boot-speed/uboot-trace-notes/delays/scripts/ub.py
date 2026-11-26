"""Per-build U-Boot static analysis core (reuses the approach of /tmp/ubt/an.py, parameterised by build)."""
import struct, bisect, re, functools
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
BASE = 0x4A000000
COND = ('eq','ne','hs','lo','cs','cc','mi','pl','vs','vc','hi','ls','ge','lt','gt','le','al')
_md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)

class Build:
    def __init__(self, name, path):
        self.name = name
        self.U = open(path, 'rb').read()
        self._ins = {}
        self._explore = {}
        self.code_end = self._find_code_end()
        self._scan()

    # ---- raw helpers
    def w(self, a): return struct.unpack_from('<I', self.U, a)[0]
    def h(self, a): return struct.unpack_from('<H', self.U, a)[0]
    def cstr(self, a, minlen=1):
        U = self.U
        if not (0 <= a < len(U)): return None
        e = U.find(b'\0', a)
        if e < 0 or e - a > 300 or e - a < minlen: return None
        try: t = U[a:e].decode('ascii')
        except UnicodeDecodeError: return None
        if not all(32 <= ord(c) < 127 or c in '\n\r\t' for c in t): return None
        return t
    def bt(self, at):
        first, second = struct.unpack_from('<HH', self.U, at)
        if first & 0xF800 != 0xF000 or not (second & 0xC000 == 0xC000 or second & 0xD000 == 0x9000): return None
        sign = (first >> 10) & 1
        i1, i2 = 1 - (((second >> 13) & 1) ^ sign), 1 - (((second >> 11) & 1) ^ sign)
        off = (sign << 24) | (i1 << 23) | (i2 << 22) | ((first & 0x3FF) << 12) | ((second & 0x7FF) << 1)
        if sign: off -= 1 << 25
        t = at + 4 + off
        if second & 0xD000 == 0xC000: t &= ~3
        return t
    def kind(self, at):
        s = self.h(at + 2)
        return 'bl' if s & 0xD000 == 0xD000 else ('blx' if s & 0xD000 == 0xC000 else 'b.w')
    def ins(self, a):
        if a in self._ins: return self._ins[a]
        i = next(_md.disasm(self.U[a:a + 4], a), None)
        self._ins[a] = i
        return i
    def litaddr(self, i):
        x = self.h(i.address)
        if i.size == 2: return ((i.address + 4) & ~3) + (x & 0xFF) * 4
        y = self.h(i.address + 2)
        return ((i.address + 4) & ~3) + ((y & 0xFFF) if x & 0x80 else -(y & 0xFFF))
    def is_push_lr(self, a):
        x = self.h(a)
        return (x & 0xFF00) == 0xB500 or (x == 0xE92D and self.h(a + 2) & 0x4000)

    def _find_code_end(self):
        # rodata starts at the lowest string referenced by a pc-relative literal beyond 0x60000
        lo = 0x100000
        for at in range(0, 0x90000, 2):
            x = self.h(at)
            if x & 0xF800 == 0x4800:
                la = ((at + 4) & ~3) + (x & 0xFF) * 4
                v = self.w(la) - BASE
                if 0x60000 <= v < lo and self.cstr(v, 4): lo = v
        return lo & ~0xF

    def _scan(self):
        U = self.U; E = self.code_end
        self.calls = []
        for at in range(0, E, 2):
            t = self.bt(at)
            if t is not None and 0 <= t < E: self.calls.append((at, t, self.kind(at)))
        self.lits = []
        for at in range(0, E, 2):
            x = self.h(at)
            if x & 0xF800 == 0x4800:
                la = ((at + 4) & ~3) + (x & 0xFF) * 4
                if la + 4 <= len(U): self.lits.append((at, (x >> 8) & 7, self.w(la), la))
            elif x & 0xFF7F == 0xF85F:
                y = self.h(at + 2); imm = y & 0xFFF
                la = ((at + 4) & ~3) + (imm if x & 0x80 else -imm)
                if 0 <= la < len(U) - 4: self.lits.append((at, y >> 12, self.w(la), la))
        self.blt = set(t for s, t, k in self.calls if k != 'b.w')
        push = set(a for a in range(0, E, 2) if self.is_push_lr(a))
        # thumb function pointers in data
        self.dataptrs = set()
        for a in range(E, len(U) - 3, 4):
            v = self.w(a)
            if v & 1 and BASE <= v < BASE + E:
                o = (v - BASE) & ~1
                if self.is_push_lr(o): self.dataptrs.add(o)
        for at, reg, v, la in self.lits:
            if v & 1 and BASE <= v < BASE + E and self.is_push_lr((v - BASE) & ~1): self.dataptrs.add((v - BASE) & ~1)
        self.starts = sorted(self.blt | self.dataptrs | set(p for p in push if not any((p - d) in self.blt for d in (2, 4, 6, 8))))
        self.callers = {}
        for s, t, k in self.calls: self.callers.setdefault(t, []).append((s, k))

    def func(self, a):
        i = bisect.bisect_right(self.starts, a) - 1
        return self.starts[i] if i >= 0 else None
    def fend(self, f):
        i = bisect.bisect_right(self.starts, f)
        return self.starts[i] if i < len(self.starts) else f + 0x200

    def explore(self, entry):
        """recursive descent; returns (calls, insns dict, branches[(site,target,cond)])"""
        if entry in self._explore: return self._explore[entry]
        seen = set(); calls = []; work = [entry]; insns = {}; br = []
        E = self.code_end
        while work:
            a = work.pop(); prev = []
            while a not in seen and 0 <= a < E:
                i = self.ins(a)
                if i is None: break
                seen.add(a); insns[a] = i
                m = i.mnemonic; ops = i.op_str; base = m.split('.')[0]
                if base in ('bl', 'blx') and ops.startswith('#'):
                    calls.append((a, self.bt(a), base))
                elif base in ('bl', 'blx'):
                    calls.append((a, None, 'blx_reg:' + ops))
                elif base == 'b' or (base[:1] == 'b' and base[1:] in COND and '#' in ops):
                    try: t = int(ops[1:], 16)
                    except ValueError: break
                    cond = base != 'b'
                    if base == 'b' and i.size == 4 and t in self.blt and t != entry and not (entry < t < max(seen | {entry}) + 0x200 and t not in self.blt):
                        calls.append((a, t, 'tail')); break
                    br.append((a, t, cond)); work.append(t)
                    if not cond: break
                elif base in ('cbz', 'cbnz'):
                    t = int(ops.split('#')[1], 16); br.append((a, t, True)); work.append(t)
                elif base == 'bx' or (base == 'pop' and 'pc' in ops) or (m.startswith('ldm') and 'pc' in ops) or (base == 'ldr' and ops.startswith('pc')) or (base == 'mov' and ops.startswith('pc')):
                    if base == 'bx' and ops != 'lr': calls.append((a, None, 'bx_reg:' + ops))
                    break
                elif base in ('tbb', 'tbh'):
                    n = None
                    for p in reversed(prev[-4:]):
                        mm = re.match(r'cmp(?:\.w)? r\d+, #(0x[0-9a-f]+|\d+)', p.mnemonic + ' ' + p.op_str)
                        if mm: n = int(mm.group(1), 0) + 1; break
                    tb = a + 4
                    for k in range(n or 1):
                        off = self.U[tb + k] if base == 'tbb' else self.h(tb + 2 * k)
                        br.append((a, tb + 2 * off, True)); work.append(tb + 2 * off)
                    break
                prev.append(i); a += i.size
        r = (calls, insns, br)
        self._explore[entry] = r
        return r

    def owner(self, site):
        """containing function: nearest start whose exploration covers site"""
        i = bisect.bisect_right(self.starts, site) - 1
        for j in range(i, max(i - 40, -1), -1):
            f = self.starts[j]
            if site in self.explore(f)[1]: return f
        return self.starts[i] if i >= 0 else None

    def strings_of(self, f):
        _, insns, _ = self.explore(f)
        out = []
        for a in sorted(insns):
            i = insns[a]
            if i.mnemonic.startswith('ldr') and '[pc' in i.op_str:
                v = self.w(self.litaddr(i)) - BASE
                s = self.cstr(v, 3) if self.code_end - 0x10 <= v < len(self.U) else None
                if s: out.append(s.strip())
        return out

    def strings_of_set(self, S):
        out = []
        for a in sorted(S):
            i = self.ins(a)
            if i is not None and i.mnemonic.startswith('ldr') and '[pc' in i.op_str:
                v = self.w(self.litaddr(i)) - BASE
                st = self.cstr(v, 3) if self.code_end - 0x10 <= v < len(self.U) else None
                if st: out.append(st.strip())
        return out

    # ---- register constant tracking
    def regconsts(self, f, site, back=24):
        """simulate a short straight run of f's instructions before site; returns dict reg->const or tag"""
        _, insns, br = self.explore(f)
        addrs = sorted(a for a in insns if a < site and site - a <= 0x60)
        # keep only contiguous run ending at site
        run = []; nxt = site
        for a in reversed(addrs):
            if a + insns[a].size != nxt: break
            m = insns[a].mnemonic
            if m in ('b', 'b.w') or (m.startswith('pop') and 'pc' in insns[a].op_str) or m == 'bx': break
            run.append(a); nxt = a
            if len(run) >= back: break
        run.reverse()
        R = {}
        for a in run: self._step(R, insns[a])
        return R, run

    def _val(self, R, op):
        op = op.strip()
        m = re.match(r'#(-?0x[0-9a-f]+|-?\d+)$', op)
        if m: return int(m.group(1), 0) & 0xFFFFFFFF
        v = R.get(op)
        return v if isinstance(v, int) else None

    def _step(self, R, i):
        m = i.mnemonic; ops = [o.strip() for o in i.op_str.split(',')]
        base = m.split('.')[0]
        if base in ('bl', 'blx'):
            for r in ('r0', 'r1', 'r2', 'r3', 'r12', 'ip', 'lr'): R[r] = 'ret' if r == 'r0' else None
            return
        if base in ('it', 'ite', 'itt', 'itee', 'itet', 'itte', 'ittt', 'ittee') or base.startswith('it'):
            R['_it'] = len(base) - 1; return
        cond = R.get('_it', 0) > 0
        if cond: R['_it'] -= 1
        if not ops or not re.match(r'(r\d+|ip|lr|sb|sl|fp)$', ops[0]):
            return
        d = ops[0]
        if base.startswith(('cmp', 'cmn', 'tst', 'teq', 'str', 'push', 'cbz', 'cbnz')): return
        val = None
        if base in ('movs', 'mov', 'movw') and len(ops) == 2:
            val = self._val(R, ops[1])
        elif base == 'movt' and isinstance(R.get(d), int):
            val = (R[d] & 0xFFFF) | (self._val(R, ops[1]) << 16)
        elif base == 'ldr' and '[pc' in i.op_str:
            val = self.w(self.litaddr(i))
        elif base in ('adds', 'add', 'subs', 'sub', 'lsls', 'lsl', 'muls', 'mul') and len(ops) in (2, 3):
            a_ = self._val(R, ops[1] if len(ops) == 3 else ops[0]); b_ = self._val(R, ops[-1])
            if a_ is not None and b_ is not None:
                val = {'add': a_ + b_, 'sub': a_ - b_, 'lsl': a_ << b_, 'mul': a_ * b_}[base.rstrip('s') if base != 'muls' else 'mul'] & 0xFFFFFFFF
        if cond: R[d] = ('cond', val)
        else: R[d] = val

    @staticmethod
    def sgn(v): return v - (1 << 32) if isinstance(v, int) and v & 0x80000000 else v

    def in_loop(self, f, site):
        """a backward branch after site to a point at/before site from which site is reachable again"""
        _, insns, br = self.explore(f)
        for s, t, c in br:
            if s > site and f <= t <= site and site in self.explore(t)[1] and s in self.explore(t)[1]:
                return True, s, t
        return False, None, None

    def loop_bound(self, f, ls, lt):
        """try to find the iteration bound of the loop closed by the branch at ls (head lt)"""
        _, insns, _ = self.explore(f)
        bi = insns[ls]
        prev = []; a = ls
        for _ in range(6):
            cands = [x for x in insns if x < a and x + insns[x].size == a]
            if not cands: break
            a = cands[0]; prev.append(insns[a])
        R, _ = self.regconsts(f, ls)
        if bi.mnemonic in ('b', 'b.w'):
            # out-of-line body: head at lt does the test and branches (conditionally) to the body containing the site
            a = lt; seq = []
            for _ in range(8):
                i = insns.get(a)
                if i is None: break
                seq.append(i)
                base = i.mnemonic.split('.')[0]
                if base[:1] == 'b' and base[1:] in ('ne', 'eq', 'lt', 'le', 'gt', 'ge', 'lo', 'hs', 'hi', 'ls', 'cc', 'cs', 'pl', 'mi'):
                    for p in reversed(seq[:-1]):
                        ops = [o.strip() for o in p.op_str.split(',')]
                        pb = p.mnemonic.split('.')[0]
                        if pb in ('subs', 'adds') and ops[-1] in ('#1', '#-1'):
                            Rh, _ = self.regconsts(f, lt)
                            v = Rh.get(ops[0])
                            return (self.sgn(v) if isinstance(v, int) else 'variable'), f'countdown {ops[0]} at head {lt:#x} ({p.mnemonic} {p.op_str}; {i.mnemonic})'
                        if pb in ('cmp', 'cmn') and len(ops) == 2:
                            Rh, _ = self.regconsts(f, p.address)
                            v = self._val(Rh, ops[1])
                            return (self.sgn(v) if v is not None else 'variable'), f'head {lt:#x}: {p.mnemonic} {p.op_str}; {i.mnemonic}'
                    break
                a += i.size
        for i in prev:
            ops = [o.strip() for o in i.op_str.split(',')]
            base = i.mnemonic.split('.')[0]
            if base in ('cmp', 'cmn') and len(ops) == 2:
                v = self._val(R, ops[1])
                return (self.sgn(v) if v is not None else 'variable'), f'{i.mnemonic} {i.op_str}; {bi.mnemonic}'
            if base in ('subs', 'adds') and (ops[-1] in ('#1', '#-1')) and bi.mnemonic.split('.')[0] in ('bne', 'bhs', 'bcs', 'bpl', 'bgt', 'bge'):
                Rh, _ = self.regconsts(f, lt)
                v = Rh.get(ops[0])
                return (self.sgn(v) if isinstance(v, int) else 'variable'), f'countdown {ops[0]} ({i.mnemonic} {i.op_str}; {bi.mnemonic})'
        if bi.mnemonic.startswith(('cbz', 'cbnz')): return 'variable', f'{bi.mnemonic} {bi.op_str} (condition from memory/hardware)'
        return 'variable', 'condition not resolved'

