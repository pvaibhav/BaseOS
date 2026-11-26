import sys; sys.path.insert(0,'/tmp/uboot-delays/scripts')
from inventory import *
Inv.REF=Inv('rgsp'); inv=Inv.REF; r=inv.run()
p=[p for p in r['panels'] if p['name']=='rg34xxsp_v1'][0]
for s in p['steps']:
    print('step',hex(s['func']),s['delay_ms'])
    for d in inv.reach_delays(s['func'],maxd=5):
        print('   ',hex(d['site']),hex(d['func']),d['target_name'],d['r0'],[hex(x) for x in d['path']])
