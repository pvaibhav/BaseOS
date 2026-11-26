import sys,re,collections
def strs(p):
    u=open(p,'rb').read()
    return collections.Counter(m.group().decode() for m in re.finditer(rb'[\x20-\x7e\t\n]{3,}',u[0x72000:]))
a=strs('/tmp/uboot-delays/bin/'+sys.argv[1]+'.bin'); b=strs('/tmp/uboot-delays/bin/'+sys.argv[2]+'.bin')
print('only in',sys.argv[1]); [print('  ',repr(k),v) for k,v in (a-b).items()]
print('only in',sys.argv[2]); [print('  ',repr(k),v) for k,v in (b-a).items()]
