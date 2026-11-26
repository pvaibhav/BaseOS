import sys,json; sys.path.insert(0,'/tmp/uboot-delays/scripts')
from inventory import *
Inv.REF=Inv('rgsp')
for n in sys.argv[1:]:
    inv=Inv.REF if n=='rgsp' else Inv(n); r=inv.run('x')
    for p in r['panels']:
        for st in p['steps']:
            if 'strap' in st: print(n,p['name'],hex(st['func']),json.dumps(st['strap'],default=hex))
    for d in r['delay_sites']:
        if d['in_loop']: pass
    print(sum(d['post_logo_reachable'] for d in r['delay_sites']), len(r['delay_sites']))
