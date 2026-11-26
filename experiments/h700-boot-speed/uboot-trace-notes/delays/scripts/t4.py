import sys,json; sys.path.insert(0,'/tmp/uboot-delays/scripts')
from inventory import *
Inv.REF=Inv('rgsp'); inv=Inv.REF; r=inv.run('rg34xxsp_v1')
for d in r['delay_sites']+r['timeout_sites']:
    if d['post_logo_reachable']: print(hex(d['site']),hex(d['func']),d['target_name'],d.get('r0',d.get('limit_ms')),d['in_loop'],d.get('loop_bound'),d.get('loop_cond'),r['func_strings'][hex(d['func'])][:2])
print('loops')
for d in r['delay_sites']:
    if d['in_loop']: print(hex(d['site']),hex(d['func']),d['r0'],d['loop_bound'],d['loop_cond'])
