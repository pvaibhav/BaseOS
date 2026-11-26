import sys; sys.path.insert(0,'/tmp/uboot-delays/scripts')
from ub import *
b=Build(sys.argv[1],f'/tmp/uboot-delays/bin/{sys.argv[1]}.bin')
for rng in sys.argv[2:]:
    s,e=[int(x,16) for x in rng.split('-')]
    f=b.owner(s); _,ins,_=b.explore(f) if f is not None else (0,{},0)
    a=s
    print('--- func',hex(f) if f is not None else None)
    while a<e:
        i=ins.get(a) or b.ins(a)
        if i is None: print(hex(a),'.short',hex(b.h(a))); a+=2; continue
        x=''
        if i.mnemonic.startswith('ldr') and '[pc' in i.op_str:
            v=b.w(b.litaddr(i)); st=b.cstr(v-BASE,3) if BASE<=v<BASE+len(b.U) else None
            x=f' ; ={v:#x}'+(f' {st[:50]!r}' if st else '')
        if i.mnemonic.split('.')[0] in ('bl','blx','b') and i.size==4:
            t=b.bt(a); x+=f' -> {t:#x}' if t is not None else ''
        print(('  ' if a in ins else '? ')+f'{a:6x}: {i.mnemonic:8} {i.op_str}{x}')
        a+=i.size
