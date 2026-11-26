import sys; sys.path.insert(0,'/tmp/uboot-delays/scripts')
from ub import *
a=Build('rgsp','/tmp/uboot-delays/bin/rgsp.bin'); b=Build('rg34xxsp','/tmp/uboot-delays/bin/rg34xxsp.bin')
d=[i for i in range(0x40,0x100000) if a.U[i]!=b.U[i]]
runs=[];s=p=None
for i in d:
    if s is None: s=p=i
    elif i-p>16: runs.append((s,p)); s=p=i
    else: p=i
runs.append((s,p))
print(len(d),len(runs))
code=[r for r in runs if r[0]<a.code_end]
print('code runs',len(code))
for r in code[:80]:
    f=a.func(r[0]); print('%#x-%#x'%r, 'func %#x'%f, a.U[r[0]:r[1]+1][:16].hex(), b.U[r[0]:r[1]+1][:16].hex())
data=[r for r in runs if r[0]>=a.code_end]
print('data runs',len(data), ['%#x-%#x'%r for r in data[:40]])
