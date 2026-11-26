import sys
T="rg28xx rg34xx rg34xxsp rg35xxh rg35xxplus rg35xxpro rg35xxsp rg40xxh rg40xxv rgcubexx rgsp".split()
B={t:open(f'/tmp/uboot-delays/bin/{t}.bin','rb').read() for t in T}
r=B['rgsp']
for t in T:
    b=B[t]
    d=[i for i in range(len(b)) if b[i]!=r[i]]
    # trailing zeros
    end=len(b.rstrip(b'\0'))
    print(t, 'first diff %#x'%d[0] if d else 'same', 'ndiff',len(d),'end %#x'%end)
