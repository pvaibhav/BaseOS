import sys,struct
a=open('/tmp/uboot-delays/bin/'+sys.argv[1]+'.bin','rb').read(); b=open('/tmp/uboot-delays/bin/'+sys.argv[2]+'.bin','rb').read()
end=int(sys.argv[3],0)
W=lambda u,o: struct.unpack_from('<I',u,o)[0]
bad=[]
for o in range(0,end,4):
    if a[o:o+4]!=b[o:o+4]:
        x,y=W(a,o),W(b,o)
        if 0x4a000000<=x<0x4a100000 and 0x4a000000<=y<0x4a100000 and abs(x-y)<=16: continue
        bad.append((hex(o),a[o:o+4].hex(),b[o:o+4].hex()))
print(len(bad),bad[:20])
