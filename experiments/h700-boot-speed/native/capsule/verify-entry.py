#!/usr/bin/env python3
"""Continue the actual copied native A32 shim through its RMR3 request; host only."""
from pathlib import Path
import argparse,importlib.util,json,struct
import unicorn as u
from unicorn import arm_const as a
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('capsule_entry_model',HERE/'verify.py');prove=importlib.util.module_from_spec(spec);spec.loader.exec_module(prove)
def native_prefix(m,initial,cpsr,revision):
 for handle in getattr(m,'_raw_handles',[]):m.hook_del(handle)
 for at in [0x03000000,0x08100000,0x09010000]:m.mem_map(at,0x1000)
 m.mem_write(0x03000024,struct.pack('<I',revision));result={'revision':revision,'rvbar':[],'rmr':[],'mmio_writes':[]}
 before={pa:bytes(m.mem_read(pa,4)) for pa in prove.GP+[prove.RET,prove.CFG,prove.MODE,prove.CTRL]}
 def code(mc,pc,size,data):
  word=struct.unpack('<I',mc.mem_read(pc,4))[0]
  if word==0xe101e300:mc.reg_write(a.UC_ARM_REG_LR,0x21400)
  elif word==0xee11ef10:mc.reg_write(a.UC_ARM_REG_LR,0x30c50030)
  elif word==0xee1cef10:mc.reg_write(a.UC_ARM_REG_LR,0)
  elif word==0xee1c0f50:mc.reg_write(a.UC_ARM_REG_R0,0)
  elif word==0xee0c0f50:result['rmr'].append(mc.reg_read(a.UC_ARM_REG_R0));mc.emu_stop();return
  else:return
  mc.reg_write(a.UC_ARM_REG_PC,pc+4)
 def write(mc,access,pa,size,value,data):
  assert not prove.RTC<=pa<prove.RTC+0x1000 and not prove.WDT<=pa<prove.WDT+0x1000,('inner shim modifies RTC/watchdog',hex(pa))
  if pa in [0x08100040,0x09010040]:result['rvbar'].append([hex(pa),hex(value)])
  elif pa>=0x01000000:result['mmio_writes'].append([hex(pa),hex(value)])
  else:assert 0x20000<=pa and pa+size<=0x58000
 m.hook_add(u.UC_HOOK_CODE,code,begin=0x20000,end=0x20190);m.hook_add(u.UC_HOOK_MEM_WRITE,write)
 m.emu_start(0x20000,0,count=10000)
 assert result['rmr']==[3] and result['rvbar']==[[hex(0x08100040 if revision else 0x09010040),'0x20060']]
 assert all(bytes(m.mem_read(pa,4))==value for pa,value in before.items())
 native=prove.inputs['native'];stash=0x200e0+struct.unpack_from('<I',native,0xe0)[0];values=list(struct.unpack('<6I',m.mem_read(stash,24)))
 assert values==[initial[13],initial[14],cpsr,0x30c50030,0,0x21400]
 result.update(stash_pa=hex(stash),stash_words=list(map(hex,values)),pending_and_watchdog_unchanged_through_RMR3_request=True,physical_reset_not_executed=True)
 return result


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);parser.add_argument('--diagnostic',action='store_true');args=parser.parse_args();directory=args.directory.resolve()
 manifest=json.loads((directory/'manifest.json').read_text());assert bool(manifest['diagnostic'])==args.diagnostic
 prove.DIRECTORY=directory;prove.inputs={name:(directory/f'{name}.bin').read_bytes() for name in ('stock','native')}
 results=[]
 for revision in (0,2):
  row,m,initial,cpsr,_=prove.run('protocol',f'native-entry-revision{revision}',seed=61+revision,media=0x10)
  prove.validate_normal_order(row,manifest['generation_cookie'])
  results.append(native_prefix(m,initial,cpsr,revision))
 report=dict(passed=True,capsule_sha256=manifest['raw_image_sha256'],generation=manifest['generation'],diagnostic=args.diagnostic,cases=results,physical_reset_or_WDT_retention_not_proven=True,hardware_qualified=False,durable_install_eligible=False)
 (directory/'entry-verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
