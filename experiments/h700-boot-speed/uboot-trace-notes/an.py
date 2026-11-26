import struct, bisect, re
from capstone import *
U=open('/tmp/ubt/uboot.bin','rb').read()
BASE=0x4A000000
md=Cs(CS_ARCH_ARM, CS_MODE_THUMB); md.detail=False
def w(a): return struct.unpack_from('<I',U,a)[0]
def h(a): return struct.unpack_from('<H',U,a)[0]
def bt(at):
    first, second = struct.unpack_from("<HH", U, at)
    if first & 0xF800 != 0xF000 or not (second & 0xC000 == 0xC000 or second & 0xD000 == 0x9000): return None
    sign = (first >> 10) & 1
    i1, i2 = 1 - (((second >> 13) & 1) ^ sign), 1 - (((second >> 11) & 1) ^ sign)
    off = (sign << 24) | (i1 << 23) | (i2 << 22) | ((first & 0x3FF) << 12) | ((second & 0x7FF) << 1)
    if sign: off -= 1 << 25
    t= at+4+off
    if second & 0xD000 == 0xC000: t &= ~3  # blx
    return t
def kind(at):
    s=h(at+2)
    return 'bl' if s&0xD000==0xD000 else ('blx' if s&0xD000==0xC000 else 'b.w')
def cstr(a):
    if not (0<=a<len(U)): return None
    e=U.find(b'\0',a)
    if e<0 or e-a>300: return None
    s=U[a:e]
    if len(s)<1: return None
    try: t=s.decode('ascii')
    except: return None
    if not all(32<=ord(c)<127 or c in '\n\r\t' for c in t): return None
    return t
def lits():
    """16-bit ldr rX,[pc,#] and 32-bit ldr.w rX,[pc,#+-] -> (site, reg, value)"""
    out=[]
    for at in range(0,CODE_END,2):
        x=h(at)
        if x&0xF800==0x4800:
            la=((at+4)&~3)+(x&0xFF)*4
            if la+4<=len(U): out.append((at,(x>>8)&7,w(la),la))
        elif x&0xFF7F==0xF85F:
            y=h(at+2); imm=y&0xFFF; up=x&0x80
            la=((at+4)&~3)+(imm if up else -imm)
            if 0<=la<len(U)-4: out.append((at,y>>12,w(la),la))
    return out
CODE_END=0x7b000
def dis(start,end):
    out=[]
    for i in md.disasm(U[start:end],start):
        out.append(i)
    return out
def pr(start,end):
    a=start
    while a<end:
        got=False
        for i in md.disasm(U[a:end],a):
            extra=''
            if i.mnemonic.startswith('ldr') and '[pc' in i.op_str:
                x=h(i.address)
                if i.size==2: la=((i.address+4)&~3)+(x&0xFF)*4
                else:
                    y=h(i.address+2); la=((i.address+4)&~3)+((y&0xFFF) if x&0x80 else -(y&0xFFF))
                v=w(la); s=cstr(v-BASE) if BASE<=v<BASE+len(U) else None
                extra=f'   ; ={v:#x}'+(f' "{s[:60]!r}"' if s else '')
                if v-BASE in NAMES: extra+=' '+NAMES[v-BASE]
            if i.mnemonic in('bl','blx','b.w') or (i.mnemonic.startswith('b') and '#' in i.op_str and i.size==4):
                t=bt(i.address)
                if t is not None and t in NAMES: extra+=' -> '+NAMES[t]
            print(f'{i.address:6x}: {i.mnemonic:8} {i.op_str}{extra}')
            a=i.address+i.size; got=True
        if a<end:
            print(f'{a:6x}: .short {h(a):#06x}'); a+=2
NAMES={}
CODE_END=0x74000
import pickle, os
def build():
    calls=[]  # (site,target,kind)
    for at in range(0,CODE_END,2):
        t=bt(at)
        if t is not None and 0<=t<CODE_END: calls.append((at,t,kind(at)))
    L=lits()
    return calls,L
if os.path.exists('/tmp/ubt/db.pkl'):
    CALLS,LITS=pickle.load(open('/tmp/ubt/db.pkl','rb'))
else:
    CALLS,LITS=build(); pickle.dump((CALLS,LITS),open('/tmp/ubt/db.pkl','wb'))
BLT=set(t for s,t,k in CALLS if k!='b.w')
# push-with-lr starts
PUSH=set()
for at in range(0,CODE_END,2):
    x=h(at)
    if (x&0xFF00)==0xB500: PUSH.add(at)
    elif x==0xE92D and h(at+2)&0x4000: PUSH.add(at)
STARTS=sorted(BLT|PUSH)
def func(a):
    i=bisect.bisect_right(STARTS,a)-1
    return STARTS[i] if i>=0 else None
def strsites(sub):
    r=[]
    for at,reg,v,la in LITS:
        o=v-BASE
        if 0x73000<=o<len(U):
            s=cstr(o)
            if s and sub in s: r.append((at,s))
    return r
def callers(t): return [(s,k) for s,tt,k in CALLS if tt==t]
def callees(f,end=None):
    if end is None:
        i=STARTS.index(f) if f in STARTS else None
        end=STARTS[i+1] if i is not None and i+1<len(STARTS) else f+0x400
    return [(s,t,k) for s,t,k in CALLS if f<=s<end]
def strs_in(f,end):
    out=[]
    for at,reg,v,la in LITS:
        if f<=at<end:
            o=v-BASE; s=cstr(o) if 0x73000<=o<len(U) else None
            if s: out.append((at,s))
    return out
def r0arg(site):
    f=func(site)
    if f is None or site-f>0x3000: f=site-0x40
    last=None
    for i in md.disasm(U[f:site],f):
        ops=i.op_str
        if ops.startswith('r0,') :
            m=re.match(r'r0, #(-?0x[0-9a-f]+|-?\d+)$',ops)
            if i.mnemonic in('movs','mov','mov.w','movw') and m: last=int(m.group(1),0)
            elif i.mnemonic.startswith(('mov','ldr','add','sub','mul','lsl','orr','bic','and','rsb','asr','lsr')): last='?'+i.mnemonic+' '+ops
        if i.mnemonic in('bl','blx'): last=None if not ops else last  # r0 clobbered
        if i.mnemonic in ('bl','blx'): last='(ret)'
    return last
def callers_all(t):
    """direct callers plus callers of tail-call stubs"""
    return callers(t)
_bl=sorted(BLT)
STARTS=sorted(BLT|set(p for p in PUSH if not any((p-d) in BLT for d in (2,4,6,8))))
TIMING={0xd50:'__udelay',0xd74:'udelay_alias(b __udelay)',0xd78:'mdelay(__msdelay)',0xd84:'get_timer_masked',0xde4:'get_timer_masked_alias',0xde8:'get_timer',0xdf4:'get_ticks',0xe0a:'get_tbclk',0x69a88:'udelay',0xeac8:'mdelay_loop(udelay 1000)',0x24dd4:'disp_delay_ms',0x24dde:'disp_delay_us',0xe14:'add_timer',0xed0:'del_timer'}
NAMES.update(TIMING)
def fend(f):
    i=bisect.bisect_right(STARTS,f)
    return STARTS[i] if i<len(STARTS) else f+0x200
def summary(f,depth=2,ind='',seen=None):
    if seen is None: seen=set()
    e=fend(f)
    ss=[s.strip()[:50] for a,s in strs_in(f,e)]
    print(f'{ind}{f:#x} {NAMES.get(f,"")} size={e-f:#x} strs={ss[:8]}')
    if f in seen: return
    seen.add(f)
    for s,t,k in callees(f,e):
        if t in TIMING:
            print(f'{ind}   @{s:#x} {k} -> {TIMING[t]} r0={r0arg(s)}')
        elif depth>0 and t not in (0x6a7bc,0x6a750,0x111e8,0x110fa,0xfe0,0x1080,0x11728,0x115cc):
            print(f'{ind}   @{s:#x} {k} ->')
            summary(t,depth-1,ind+'      ',seen)
from functools import lru_cache
import sys
sys.setrecursionlimit(10000)
CALLMAP={}
for s,t,k in CALLS:
    CALLMAP.setdefault(func(s),[]).append((s,t,k))
def paths_to_timing(f,maxd=6):
    """BFS: returns dict timing_target -> list of call chains (site lists) shortest"""
    from collections import deque
    res={}
    q=deque([(f,[])]); seen={f}
    while q:
        g,path=q.popleft()
        if len(path)>=maxd: continue
        for s,t,k in CALLMAP.get(g,[]):
            if t in TIMING:
                res.setdefault(TIMING[t],[]).append(path+[(s,t,r0arg(s))])
            elif t not in seen and t not in (0x6a7bc,0x6a750,0x111e8,0x110fa):
                seen.add(t); q.append((t,path+[(s,t,None)]))
    return res
