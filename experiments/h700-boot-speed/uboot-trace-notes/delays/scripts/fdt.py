import struct,sys
def parse(b,off):
    magic,tot,ost,ostr,omem,ver,lc,bc,sstr,sst=struct.unpack_from('>10I',b,off)
    p=off+ost; path=[]; out={}
    S=lambda o: b[off+ostr+o:b.index(b'\0',off+ostr+o)].decode()
    while True:
        tok=struct.unpack_from('>I',b,p)[0]; p+=4
        if tok==1:
            e=b.index(b'\0',p); path.append(b[p:e].decode()); p=(e+4)&~3
        elif tok==2: path.pop()
        elif tok==3:
            ln,no=struct.unpack_from('>II',b,p); p+=8
            out[('/'.join(path) or '/')+':'+S(no)]=b[p:p+ln]; p=(p+ln+3)&~3
        elif tok==4: continue
        elif tok==9: break
    return out
if __name__=='__main__':
    b=open(sys.argv[1],'rb').read()
    for off in map(lambda x:int(x,0),sys.argv[2:]):
        d=parse(b,off)
        for k,v in d.items():
            if 'lcd0' in k and any(s in k for s in ('driver_name','lcd_x','lcd_y','lcd_used','power','bl_en','pin','gpio','lcd_dclk')) or 'target' in k and 'delay' in k or 'power_delay' in k:
                val=v.rstrip(b'\0').decode() if v and all(32<=c<127 or c==0 for c in v) and v[-1:]==b'\0' else v.hex()
                print(hex(off),k,val)
