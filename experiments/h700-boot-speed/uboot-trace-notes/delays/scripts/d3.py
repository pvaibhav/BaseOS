import sys; sys.path.insert(0,'/tmp/uboot-delays/scripts')
from ub import *
a=Build('rgsp','/tmp/uboot-delays/bin/rgsp.bin'); b=Build(sys.argv[1],f'/tmp/uboot-delays/bin/{sys.argv[1]}.bin')
diffs=[]
for f in a.starts:
    for ad,i in a.explore(f)[1].items():
        if a.U[ad:ad+i.size]!=b.U[ad:ad+i.size]: diffs.append(ad)
diffs=sorted(set(diffs)); print(len(diffs))
for ad in diffs[:60]:
    i=a.ins(ad); j=b.ins(ad)
    print(hex(ad),hex(a.owner(ad)), i.mnemonic,i.op_str,'|', j.mnemonic if j else None, j.op_str if j else '')
# literal diffs that are not string pointers shift
ld=[]
for at,reg,v,la in a.lits:
    v2=b.w(la)
    if v!=v2 and not (abs(v2-v)<=16 and v>=BASE+a.code_end): ld.append((at,v,v2))
print('literal diffs',len(ld))
for at,v,v2 in ld[:80]: print(hex(at),hex(a.owner(at)),hex(v),hex(v2), repr(a.cstr(v-BASE) if BASE<=v<BASE+0x100000 else ''), repr(b.cstr(v2-BASE) if BASE<=v2<BASE+0x100000 else ''))
