import sys; sys.path.insert(0,'/tmp/uboot-delays/scripts')
from ub import *
b=Build(sys.argv[1],f'/tmp/uboot-delays/bin/{sys.argv[1]}.bin')
DEL={0x2621c}
for e in sys.argv[2:]:
    e=int(e,16); calls,ins,br=b.explore(e)
    ds=[(hex(s), b.regconsts(e,s)[0].get('r0')) for s,t,k in calls if t in DEL]
    print(hex(e),'insns',len(ins),'range',hex(min(ins)),hex(max(ins)),'delays',ds, 'ncalls',len(calls), 'other calls',sorted(set(hex(t) for s,t,k in calls if t not in DEL and t not in (0x27e3c,0x27fcc) and t)))
