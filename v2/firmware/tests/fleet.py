#!/usr/bin/env python3
"""Per-model compiled-chain emulation. Synthetic fixtures never qualify a kernel DTB."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run as suite
sys.path.insert(0,str(suite.ROOT/'tools'))
import mainline_payload as payload

def fixture(target,spec,generation,out):
    mod=suite.build.load('fleet_fdt',suite.ROOT/'experiments/h700-boot-speed/fdt.py')
    blob=(suite.ROOT/'work/v2/kernel/rgsp.dtb').read_bytes()
    changes={}
    for path,key,off,value in mod.walk(blob):
        if path=='/' and key=='compatible': changes[off]=spec['compatible'].encode()+b'\0'
        if path.startswith('/chosen/framebuffer') and key in ('width','height','stride'):
            changes[off]=struct.pack('>I',{'width':spec['logo'][0],'height':spec['logo'][1],'stride':spec['logo'][0]*4}[key])
    header=list(struct.unpack_from('>10I',blob)); start,strings=header[2:4]
    data=bytearray(blob[start:strings])
    for off,value in sorted(changes.items(),reverse=True):
        at=off-start; old=struct.unpack_from('>I',data,at-8)[0]
        data[at-8:at-4]=struct.pack('>I',len(value))
        data[at:at+((old+3)&~3)]=value+bytes(-len(value)%4)
    delta=len(data)-(strings-start)
    header[1]+=delta; header[3]+=delta; header[9]+=delta
    dtb=bytearray(blob[:start])+data+blob[strings:]
    struct.pack_into('>10I',dtb,0,*header)
    (out/'SYNTHETIC-TEST-ONLY.dtb').write_bytes(dtb)
    original=(suite.FW/'v2-rgsp').parent.parent/'os/v2-rgsp/image.bin'
    source=original.read_bytes(); android,_=payload.parse(source,source[32:64])
    path=out/'SYNTHETIC-TEST-ONLY.bin';path.write_bytes(payload.pack(android,bytes(dtb),bytes.fromhex(generation)))
    return path

def main(target):
    v='v2-'+target; out=suite.FW/v
    spec=json.loads((suite.HERE.parent/'targets.json').read_text())[target]
    m=json.loads((out/'manifest.json').read_text());generation=m['generation']
    suite.CARD=suite.ROOT/'work'/target
    suite.LOGS.mkdir(parents=True,exist_ok=True)
    real=suite.ROOT/f'work/v2/os/{v}/image.bin'
    image=real if real.exists() else fixture(target,spec,generation,out)
    repo=lambda p:'/repo/'+str(p.relative_to(suite.ROOT))
    spl=suite.FW/'build'/v/'spl'
    words=(spl/'u-boot-2026.10/arch/arm/mach-sunxi/dram-candidates.h').read_text().split('dram_vendor_candidates[][32] = {')[1]
    import re
    banks=[[int(x,16) for x in re.findall(r'0x([0-9a-f]+)u',row)] for row in re.findall(r'\{([^}]+)\}',words)]
    (spl/'manifest.json').write_text(json.dumps(dict(native_dram=True,spl_sha256=hashlib.sha256((spl/'spl.bin').read_bytes()).hexdigest(),candidate_types=[b[1] for b in banks])))
    results={}
    results['dram-selector']=suite.container(v+'-dram-selector','/repo/experiments/h700-boot-speed/spl/verify-dram-select.py',repo(spl))[0]
    results['every-stock-bank']=suite.container(v+'-stock-banks',repo(suite.HERE/'stock-banks.py'),repo(spl))[0]
    for script in ('verify.py','verify-entry.py'):
        results[script]=suite.container(v+'-'+script,'/repo/experiments/h700-boot-speed/native/capsule/'+script,repo(suite.FW/'build'/v/'capsule'))[0]
    for pe6 in ((1,0) if spec['panel_rule']==2 else (1,)):
        for case in ('bank0','bank1','bank2','missing-logo','dram','generation','dtb'):
            args=[repo(suite.HERE/'chain_replay.py'),v,repo(image),'--counter-step-us','100','--pe6',str(pe6)]
            if case!='missing-logo':args+=['--card','/card/boot-prefix.img']
            if case.startswith('bank'):args+=['--bank',case[-1]]
            elif case!='missing-logo':args+=['--fault',case]
            name=f'{v}-pe6-{pe6}-{case}'
            status,stdout=suite.container(name,*args)
            results[name]=dict(status=status,report=suite.last_json(stdout) if status=='pass' else None)
    failed=[k for k,v in results.items() if (v if isinstance(v,str) else v['status'])!='pass']
    (out/'tests.json').write_text(json.dumps(dict(emulated=True,hardware_tested=False,generation=generation,payload_fixture=str(image),payload_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),dtb_sha256=hashlib.sha256(payload.parse(image.read_bytes(),bytes.fromhex(generation))[1]).hexdigest(),real_payload_checked=image==real,results=results,failed=failed),indent=2)+'\n')
    return bool(failed)
if __name__=='__main__':sys.exit(main(sys.argv[1]))
