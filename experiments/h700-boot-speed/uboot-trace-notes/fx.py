import sys; sys.path.insert(0,'/tmp/ubt')
from an import *
import re
mdd=Cs(CS_ARCH_ARM, CS_MODE_THUMB)
COND=('eq','ne','hs','lo','cs','cc','mi','pl','vs','vc','hi','ls','ge','lt','gt','le')
def one(a):
    for i in mdd.disasm(U[a:a+4],a): return i
    return None
_cache={}
def explore(entry):
    if entry in _cache: return _cache[entry]
    seen=set(); calls=[]; work=[entry]; tails=[]; insns={}
    while work:
        a=work.pop()
        prev=[]
        while a not in seen and 0<=a<CODE_END:
            i=one(a)
            if i is None: break
            seen.add(a); insns[a]=i
            m=i.mnemonic; ops=i.op_str
            nxt=a+i.size
            base=m.split('.')[0]
            if base in('bl','blx') and ops.startswith('#'):
                t=bt(a); calls.append((a,t,base))
            elif base in('bl','blx'):
                calls.append((a,None,'blx_reg:'+ops))
            elif base=='b' or (base[:1]=='b' and base[1:] in COND):
                t=int(ops[1:],16)
                cond=base!='b'
                inIT=False
                if base=='b' and i.size==4 and t in BLT and t!=entry and not (entry<t<max(seen|{entry})+0x200 and t not in BLT):
                    tails.append((a,t)); calls.append((a,t,'tail'))
                    break
                work.append(t)
                if not cond: break
            elif base in('cbz','cbnz'):
                work.append(int(ops.split('#')[1],16))
            elif base=='bx' or (base.startswith('pop') and 'pc' in ops and base=='pop') or (m.startswith('ldm') and 'pc' in ops) or (base=='ldr' and ops.startswith('pc')) or (base=='mov' and ops.startswith('pc')):
                break
            elif base in('tbb','tbh'):
                n=None
                for p in reversed(prev[-4:]):
                    mm=re.match(r'cmp(?:\.w)? r\d+, #(0x[0-9a-f]+|\d+)',p.mnemonic+' '+p.op_str)
                    if mm: n=int(mm.group(1),0)+1; break
                if n is None: n=1
                tb=a+4
                for k in range(n):
                    off = U[tb+k] if base=='tbb' else h(tb+2*k)
                    work.append(tb+2*off)
                break
            prev.append(i)
            a=nxt
    _cache[entry]=(calls,seen,insns)
    return _cache[entry]
EXCL={0x6a7bc,0x6a750,0x111e8,0x110fa}
def reach(f,maxd=8,targets=None):
    from collections import deque
    targets=targets or TIMING
    res={}; q=deque([(f,[])]); seen={f}
    while q:
        g,path=q.popleft()
        if len(path)>=maxd: continue
        calls,_,_=explore(g)
        for s,t,k in calls:
            if t is None: continue
            if t in targets:
                res.setdefault(t,[]).append(path+[(s,t)])
            elif t not in seen and t not in EXCL:
                seen.add(t); q.append((t,path+[(s,t)]))
    return res
def r0at(f,site):
    """track r0 constant in f's straight-line code before site"""
    _,_,ins=explore(f)
    # walk backwards in address order
    addrs=sorted(a for a in ins if a<site and site-a<0x40)
    val=None
    for a in addrs:
        i=ins[a]; ops=i.op_str
        if ops.startswith('r0,') or i.mnemonic.startswith(('bl','blx')):
            mm=re.match(r'r0, #(-?0x[0-9a-f]+|-?\d+)$',ops)
            if mm and i.mnemonic.split('.')[0] in('movs','mov','movw'): val=int(mm.group(1),0)
            elif i.mnemonic.startswith(('bl','blx')): val='(ret)'
            elif not i.mnemonic.startswith(('cmp','str','tst','cbz','cbnz')): val='?'+i.mnemonic+' '+ops
    return val
