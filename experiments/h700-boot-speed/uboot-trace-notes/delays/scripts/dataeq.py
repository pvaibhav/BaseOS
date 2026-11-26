"""Check that two builds' data beyond code_end differ only by (a) rodata pointer shift and (b) build date strings."""
import sys,struct
a=open('/tmp/uboot-delays/bin/'+sys.argv[1]+'.bin','rb').read(); b=open('/tmp/uboot-delays/bin/'+sys.argv[2]+'.bin','rb').read()
start=int(sys.argv[3],0)
W=lambda u,o: struct.unpack_from('<I',u,o)[0]
def isptr(x): return 0x4a000000<=x<0x4a100000
i=start; j=start; bad=[]; shifts=[]
while i<0x100000-4 and j<0x100000-4:
    if a[i]==b[j]: i+=1; j+=1; continue
    # pointer-tolerant aligned word
    oi=i&~3; oj=j&~3
    if i-oi==j-oj and isptr(W(a,oi)) and isptr(W(b,oj)) and abs(W(a,oi)-W(b,oj))<=64:
        i=oi+4; j=oj+4; continue
    # try resync with small shift
    found=None
    for d in sorted(range(-16,17),key=abs):
        if d==0: continue
        if all(a[i+k]==b[j+d+k] for k in range(0,24) if j+d+k<0x100000): found=d; break
    if found is not None:
        shifts.append((i,j,found)); j+=found; continue
    bad.append((i,j)); i+=1; j+=1
print('shifts',[(hex(x),hex(y),d) for x,y,d in shifts][:20], len(shifts))
cl=[]
for x,y in bad:
    if cl and x-cl[-1][1]<=16: cl[-1][1]=x
    else: cl.append([x,x,y])
print('unexplained clusters',len(cl))
for x,e,y in cl[:40]: print(hex(x),hex(e),a[x-8:e+8],'|',b[y-8:y+(e-x)+8])
