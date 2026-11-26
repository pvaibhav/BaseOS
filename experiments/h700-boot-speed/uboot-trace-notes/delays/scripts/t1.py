import sys, time; sys.path.insert(0,'/tmp/uboot-delays/scripts')
from ub import *
t=time.time()
T="rg28xx rg34xx rg34xxsp rg35xxh rg35xxplus rg35xxpro rg35xxsp rg40xxh rg40xxv rgcubexx rgsp".split()
for n in T:
    b=Build(n,f'/tmp/uboot-delays/bin/{n}.bin')
    print(n, hex(b.code_end), len(b.calls), len(b.starts), round(time.time()-t,1))
