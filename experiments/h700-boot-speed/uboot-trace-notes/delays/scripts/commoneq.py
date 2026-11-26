import sys; sys.path.insert(0,'/tmp/uboot-delays/scripts')
from ub import *
from inventory import T
a=Build('rgsp','/tmp/uboot-delays/bin/rgsp.bin')
addrs={}
for f in a.starts:
    if f>=0x26000: continue
    for ad,i in a.explore(f)[1].items():
        if ad<0x26000: addrs[ad]=i
for t in T:
    b=Build(t,f'/tmp/uboot-delays/bin/{t}.bin'); bad=[]
    for ad,i in addrs.items():
        j=b.ins(ad)
        if j is None or j.mnemonic!=i.mnemonic: bad.append(ad); continue
        if i.op_str!=j.op_str:
            if i.mnemonic.split('.')[0] in ('bl','blx','b') and i.size==4:
                ta,tb=a.bt(ad),b.bt(ad)
                if ta>=0x26000 and tb>=0x26000: continue
            bad.append(ad)
    print(t,'common-region instructions compared',len(addrs),'mismatches',len(bad),[hex(x) for x in sorted(bad)[:8]])
