T="rg28xx rg34xx rg34xxsp rg35xxh rg35xxplus rg35xxpro rg35xxsp rg40xxh rg40xxv rgcubexx rgsp".split()
B={t:open(f'/tmp/uboot-delays/bin/{t}.bin','rb').read() for t in T}
r=B['rgsp']
import struct
for t in T:
    b=B[t]
    d=[i for i in range(0x40,len(b)) if b[i]!=r[i]]
    # cluster
    runs=[];s=None;p=None
    for i in d:
        if s is None: s=p=i
        elif i-p>64: runs.append((s,p)); s=p=i
        else: p=i
    if s is not None: runs.append((s,p))
    print(t, len(runs), ['%#x-%#x'%x for x in runs[:8]], 'hdr', b[0x4:0x40][:0x20].hex())
