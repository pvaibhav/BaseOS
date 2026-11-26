import sys
a=open('/tmp/uboot-delays/bin/rgsp.bin','rb').read(); b=open(f'/tmp/uboot-delays/bin/{sys.argv[1]}.bin','rb').read()
i=int(sys.argv[2],0); E=0x100000
# walk: find diff, then find shift resync
while i<E:
    if a[i]==b[i]: i+=1; continue
    print('diff at',hex(i), a[i-48:i+48], '\n   ', b[i-48:i+48])
    # try shifts
    best=None
    for sh in range(-64,65):
        if all(0<=i+k+sh<E and a[i+k]==b[i+k+sh] for k in range(8,72)): best=sh; break
    print('  shift', best)
    if best is None: i+=64; continue
    # advance until mismatch under shift
    j=i+8
    while j<E and j+best<E and a[j]==b[j+best]: j+=1
    print('  resync holds until',hex(j))
    if best==0: i=j; continue
    # compare remaining with shift: continue scan in shifted space
    i=j
    break
