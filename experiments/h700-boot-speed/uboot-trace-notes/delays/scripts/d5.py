import sys,struct
a=open('/tmp/uboot-delays/bin/rgsp.bin','rb').read(); b=open(f'/tmp/uboot-delays/bin/{sys.argv[1]}.bin','rb').read()
W=lambda u,o: struct.unpack_from('<I',u,o)[0]
runs=[];i=int(sys.argv[2],0)
while i<0x100000:
    o=i&~3
    if a[o:o+4]!=b[o:o+4]:
        x,y=W(a,o),W(b,o)
        if 0x4a000000<=x<0x4a100000 and 0x4a000000<=y<0x4a100000 and abs(x-y)<=64: i=o+4; continue
        runs.append(o); 
    i=o+4
# cluster
cl=[];s=p=None
for o in runs:
    if s is None: s=p=o
    elif o-p>32: cl.append((s,p+4)); s=p=o
    else: p=o
if s is not None: cl.append((s,p+4))
print(len(cl))
for s,e in cl[:int(sys.argv[3]) if len(sys.argv)>3 else 30]:
    print(hex(s),hex(e), a[s:min(e,s+80)], '\n      ', b[s:min(e,s+80)])
