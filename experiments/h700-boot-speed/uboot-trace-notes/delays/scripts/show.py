import sys; sys.path.insert(0,'/tmp/uboot-delays/scripts')
from inventory import *
Inv.REF=Inv('rgsp'); inv=Inv.REF if sys.argv[1]=='rgsp' else Inv(sys.argv[1]); r=inv.run()
for d in r['delay_sites']:
    print('D %6x f=%6x %-4s %-20s r0=%-14s %s %s'%(d['site'],d['func'],d['kind'],d['target_name'],d['r0'],'LOOP' if d['in_loop'] else '', r['func_strings'][hex(d['func'])][:3]))
for d in r['timeout_sites']:
    print('T %6x f=%6x %-22s arg=%-10s %s lim=%s %s %s'%(d['site'],d['func'],d['target_name'],d['r0_arg'],d['compare'],d['limit_ms'],'LOOP' if d['in_loop'] else '',r['func_strings'][hex(d['func'])][:2]))
for d in r['add_timer_sites']: print('A',hex(d['site']),hex(d['func']),d['kind'],d['expires_ms'],d['store'])
for p in r['panels']: print('P',p['name'],hex(p['struct']),hex(p['open_flow']),[(hex(s['func']),s['delay_ms']) for s in p['steps']])
print(r['src_ops'], r['opwrappers'])
