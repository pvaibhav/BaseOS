#!/usr/bin/env python3
"""RG SP compiled-chain emulation, using only the RG SP card and payload."""
import json
import hashlib
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run as suite
FW=suite.FW
suite.CARD=suite.ROOT/'work/rgsp'
V='v2-rgsp'
suite.LOGS.mkdir(parents=True,exist_ok=True)
repo=lambda p:'/repo/'+str(p.relative_to(suite.ROOT))
retry='--retry-failed' in sys.argv
generation=json.loads((FW/V/'manifest.json').read_text())['generation']
previous=json.loads((FW/V/'tests.json').read_text()) if retry else {}
if retry and previous.get('generation') != generation:
    raise SystemExit('Generation changed; run the complete suite')
results=previous['results'] if retry else {}
spl=FW/'build'/V/'spl'
(spl/'manifest.json').write_text(json.dumps(dict(native_dram=True,
    spl_sha256=hashlib.sha256((spl/'spl.bin').read_bytes()).hexdigest(), candidate_types=[7,7,8])))
results['dram-selector']=suite.container('rgsp-dram-selector',
    '/repo/experiments/h700-boot-speed/spl/verify-dram-select.py',repo(spl))[0]
for script in ('verify.py','verify-entry.py'):
    if retry and results.get(script)=='pass': continue
    results[script]=suite.container('rgsp-'+script,'/repo/experiments/h700-boot-speed/native/capsule/'+script,repo(FW/'build'/V/'capsule'))[0]
for pe6 in (1,0):
    for case in ('normal','bank0','bank1','missing-logo','dram','generation','dtb'):
        args=[repo(suite.HERE/'chain_replay.py'),V,'/repo/work/v2/os/v2-rgsp/image.bin','--pe6',str(pe6)]
        if case!='missing-logo': args+=['--card','/card/boot-prefix.img']
        if case.startswith('bank'): args+=['--bank',case[-1]]
        elif case not in ('normal','missing-logo'): args+=['--fault',case]
        name=f'rgsp-pe6-{pe6}-{case}'
        if retry and results.get(name,{}).get('status')=='pass': continue
        status,out=suite.container(name,*args)
        results[name]=dict(status=status,report=suite.last_json(out) if status=='pass' else None)
failed=[k for k,v in results.items() if (v if isinstance(v,str) else v['status'])!='pass']
(FW/V/'tests.json').write_text(json.dumps(dict(emulated=True,generation=generation,results=results,failed=failed),indent=2)+'\n')
sys.exit(bool(failed))
