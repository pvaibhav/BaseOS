#!/usr/bin/env python3
"""Run isolated linked SPL + actual PIO instructions; no device access."""
from pathlib import Path
from collections import Counter
import argparse,capstone,hashlib,importlib.util,json,re,struct,sys,time
import unicorn as u
from unicorn import arm64_const as a
sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
R=HERE.parents[2];S=HERE
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory',type=Path)
parser.add_argument('--native-loader',type=Path,required=True)
parser.add_argument('--full',action='store_true',help='load full firmware/image and reach actual native RMR handoff')
args=parser.parse_args();T=args.directory.resolve();native_dir=args.native_loader.resolve()
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
E=module('existing_early_mmc',S/'verify-early-mmc.py');system_model=E.system_model
manifest=json.loads((T/'manifest.json').read_text());native=json.loads((native_dir/'manifest.json').read_text())
assert manifest['native_loader']==native and native.get('persistent_boot') and not native['native_diagnostic']
for relative,digest in native['persistent_source_hashes'].items():
 pinned=(R/relative).resolve();assert pinned.is_relative_to(R)
 assert hashlib.sha256(pinned.read_bytes()).hexdigest()==digest,relative
blob=(T/'spl.bin').read_bytes();source=(T/'restore-source.bin').read_bytes()
assert hashlib.sha256(blob).hexdigest()==manifest['spl_sha256']
assert hashlib.sha256(source).hexdigest()==manifest['restore_source_sha256']
assert hashlib.sha256((T/'persistent-journal.bin').read_bytes()).hexdigest()==manifest['immutable_journal_sha256']
cookie=E.fnv(bytes.fromhex(native['generation']));assert cookie==manifest['generation_cookie'] and cookie
modeled_geometry,modeled_ram_bytes,expected_parameters=E.geometry_model(source)
symbols={name:int(ad,16) for ad,name in re.findall(r'^([0-9a-f]+) [a-zA-Z] (\w+)$',(T/'symbols.txt').read_text(),re.M)}
assert len(blob)<=0xc000 and symbols['__image_copy_end']<=0x2c000
md=capstone.Cs(capstone.CS_ARCH_ARM64,capstone.CS_MODE_ARM);priv={};special={}
for off in range(0,len(blob)-3,4):
 ins=next(md.disasm(blob[off:off+4],0x20000+off),None)
 if ins and ins.mnemonic=='mrs' and any(x in ins.op_str for x in ['cntpct_el0','cntfrq_el0']):special[ins.address]=ins
 elif ins and ins.mnemonic in ['mrs','msr','dc','ic','tlbi']:priv[ins.address]=ins
stub=(T/'stock-resume64.bin').read_bytes();assert hashlib.sha256(stub).hexdigest()==manifest['resume_trampoline_sha256']
for ins in md.disasm(stub,0x50000):
 if ins.mnemonic in ['mrs','msr','dc','ic','tlbi']:priv[ins.address]=ins

wfi_addresses={0x20000+off for off in range(0,len(blob)-3,4) if blob[off:off+4]==bytes.fromhex('7f2003d5')}
wfi_addresses.update(0x50000+off for off in range(0,len(stub)-3,4) if stub[off:off+4]==bytes.fromhex('7f2003d5'))

def run(name,pair=(0,0),resume=False,journal='persistent',damage_source=False,full=False,destination=16):
 m=u.Uc(u.UC_ARCH_ARM64,u.UC_MODE_ARM);m.ctl_set_cpu_model(a.UC_CPU_ARM64_A53)
 m.mem_map(0x20000,0x38000);m.mem_map(0x03000000,0x08000000)
 if full:m.mem_map(0x40000000,0x10000000)
 m.mem_write(0x20000,blob);m.reg_write(a.UC_ARM64_REG_PSTATE,0x3cd)
 initial_gp=[0x70000000+i for i in range(14)]+list(pair)
 m.mem_write(0x07000100,struct.pack('<16I',*initial_gp));m.mem_write(0x070001f8,struct.pack('<I',0x10000 if resume else 0))
 system=system_model.SystemRegisters(3);clock=E.t.Clock()
 class Tracer:
  def next_expiry(self):return None
 tracer=Tracer();tracer.clock=clock
 card=E.SparseCard(source,restore_primary=destination==16)
 if journal=='persistent':card.blocks[0x56000]=(T/'persistent-journal.bin').read_bytes()
 elif journal=='lab':
  j=bytearray(card.blocks[0x56000]);struct.pack_into('<I',j,45*4,16 if destination==16 else 0);struct.pack_into('<I',j,508,E.fnv(j[:508]));card.blocks[0x56000]=bytes(j)
 elif journal=='bad':card.blocks[0x56000]=bytes(512)
 original_journal=card.blocks[0x56000]
 if damage_source:
  broken=bytearray(card.blocks[0x56001]);broken[0x100]^=1;card.blocks[0x56001]=bytes(broken)
 if full:
  for which,offset in [('firmware',40),('image',48)]:
   payload=(native_dir/f'{which}.bin').read_bytes();assert hashlib.sha256(payload).hexdigest()==native[which+'_sha256']
   start=native['boot_partition_lba']+offset*2048
   card.blocks.update({start+i:payload[i*512:(i+1)*512] for i in range(len(payload)//512)})
 host=E.PioHost(tracer,card,restore_primary=destination==16)
 events=[];counter=0;outcome=[];counts=Counter();console=[];min_sp=0x58000
 def get(ad):return struct.unpack('<I',m.mem_read(ad,4))[0]
 def put(ad,v):m.mem_write(ad,struct.pack('<I',v))
 def read(uc,access,ad,size,value,data):
  counts[ad]+=1
  if 0x04020000<=ad<0x04021000:put(ad,host.read(ad-0x04020000))
  elif ad==0x05000014:put(ad,0x60)
  elif ad in (0x03001000,0x03001010,0x03001020):put(ad,get(ad)|(1<<28))
 def write(uc,access,ad,size,value,data):
  if 0x04020000<=ad<0x04021000:
   if ad==0x04020018 and value&(1<<31):events.append(['sd-command',counter,value&63,get(0x0402001c),get(0x04020014)])
   host.write(ad-0x04020000,value)
  elif ad==0x05000000 and size in (1,4):console.append(chr(value&255))
  elif 0x07000100<=ad<0x07000200:events.append(['rtc',counter,hex(ad),hex(value)])
  elif ad in (0x030090b0,0x030090b4,0x030090b8):events.append(['wdt',counter,hex(ad),hex(value)])
  elif 0x03001000<=ad<0x03002000:events.append(['clock',counter,hex(ad),hex(value)])
 def returned(v):m.reg_write(a.UC_ARM64_REG_X0,v);m.reg_write(a.UC_ARM64_REG_PC,m.reg_read(a.UC_ARM64_REG_X30))
 def code(uc,ad,size,data):
  nonlocal counter,min_sp
  counter+=1;clock.ticks+=24
  sp=m.reg_read(a.UC_ARM64_REG_SP)
  if 0x48000<=sp<=0x58000:min_sp=min(min_sp,sp)
  if ad==symbols['sunxi_dram_init']:
   events.append(['native-dram',counter,hex(ad)])
   if not full:outcome.append('native-dram');m.emu_stop();return
  if full:
   if ad==symbols['mctl_core_init']:returned(1);return
   if ad==symbols['mctl_auto_detect_dram_size']:m.mem_write(m.reg_read(a.UC_ARM64_REG_X1),bytes(modeled_geometry));returned(0);return
   if ad==symbols['mctl_check_memory']:returned(1);return
  if ad in [symbols.get(x) for x in ('axp_init','axp_set_dcdc2','axp_set_dcdc3')]:returned(0);return
  if full and ad==symbols.get('pmic_bus_read'):
   reg=m.reg_read(a.UC_ARM64_REG_X0);assert reg in (0x20,0,0xf0)
   m.mem_write(m.reg_read(a.UC_ARM64_REG_X1),bytes(1));events.append(['pmic-bootreason-read',counter,reg,0]);returned(0);return
  if full and ad==symbols.get('pmic_bus_write'):raise AssertionError('zero-f0 bootreason fixture must not write PMIC')
  if ad in special:
   ins=special[ad];target,src=ins.op_str.split(', ');m.reg_write(system_model.xreg(int(target[1:])),clock.ticks if src=='cntpct_el0' else 24000000);m.reg_write(a.UC_ARM64_REG_PC,ad+4);return
  if ad in priv:
   ins=priv[ad]
   if ins.mnemonic=='msr' and ins.op_str.startswith('daifset'):m.reg_write(a.UC_ARM64_REG_PC,ad+4);return
   if system.execute(m,ins):
    if ins.mnemonic=='msr' and ins.op_str.startswith('rmr_el3'):
     outcome.append('rmr'+str(system.registers['rmr_el3']));m.emu_stop()
    return
  if ad in wfi_addresses:
   outcome.append('wfi');m.emu_stop()
 m.hook_add(u.UC_HOOK_MEM_READ,read,begin=0x03000000,end=0x0affffff);m.hook_add(u.UC_HOOK_MEM_WRITE,write,begin=0x03000000,end=0x0affffff)
 targets=set(special)|set(priv)|wfi_addresses
 targets.update(symbols[name] for name in ('sunxi_dram_init','mctl_core_init','mctl_auto_detect_dram_size','mctl_check_memory','axp_init','axp_set_dcdc2','axp_set_dcdc3'))
 targets.update(symbols[name] for name in ('pmic_bus_read','pmic_bus_write') if name in symbols)
 for target in targets:m.hook_add(u.UC_HOOK_CODE,code,begin=target,end=target)
 error=None;start=time.monotonic()
 try:m.emu_start(0x20060,0x58000,count=300000000 if full else 10000000)
 except Exception as ex:error=str(ex)
 gp=struct.unpack('<16I',m.mem_read(0x07000100,64))
 row=dict(name=name,initial_pair=[hex(x) for x in pair],resume=resume,journal=journal,full=full,damage_source=damage_source,
 outcome=outcome,pc=hex(m.reg_read(a.UC_ARM64_REG_PC)),error=error,elapsed_s=round(time.monotonic()-start,3),hook_steps=counter,
 final_pair=[hex(x) for x in gp[14:]],vendor_gp_preserved=list(gp[:14])==initial_gp[:14],rtc_events=[x for x in events if x[0]=='rtc'],
 wdt_events=[x for x in events if x[0]=='wdt'],clock_events=[x for x in events if x[0]=='clock'],sd_commands=host.commands,media_writes=host.writes,
 source_reads=[x for x in host.commands if x['cmd'] in (17,18) and x['arg'] in (0x56000,0x56001)],journal_unchanged=card.blocks[0x56000]==original_journal,
 wdt_final=[hex(get(0x03009000+o)) for o in (0xb4,0xb8,0xb0)],sctlr=hex(system.registers['sctlr_el3']),min_sampled_stack=hex(min_sp),console=''.join(console))
 assert not error and row['vendor_gp_preserved'],row
 if resume:
  assert not any(c['cmd'] in (24,25) for c in host.commands),row
  assert outcome==['rmr2'] and tuple(gp[14:])==pair and not row['rtc_events'] and not row['wdt_events'] and not host.writes,row
  assert bytes(m.mem_read(0x20000,65536))==source and bytes(m.mem_read(0x50000,188))==stub
  assert get(0x09010040)==0x20000 and get(0x09010044)==0
  assert {x[2] for x in row['clock_events']}<= {'0x300184c','0x3001830'},row
  assert all((int(x[3],16)>>24)&3==0 for x in row['clock_events'] if x[2]=='0x3001830'),row
 elif name.startswith('healthy'):
  assert not any(c['cmd'] in (24,25) for c in host.commands),row
  assert outcome==(['rmr3'] if full else ['native-dram']) and tuple(gp[14:])==(cookie,0x42535031) and not host.writes and row['journal_unchanged'],row
  assert [x[2:] for x in row['rtc_events']]==[['0x7000138',hex(cookie)],['0x700013c','0x42535031']]
  first_sd=next(x[1] for x in events if x[0]=='sd-command');assert row['rtc_events'][-1][1]<first_sd
  assert [(x['arg'],x['bytes']) for x in row['source_reads']]==[(0x56000,512),(0x56001,512)]
  if full:
   assert bytes(m.mem_read(0x45000000,native['image_bytes']))==(native_dir/'image.bin').read_bytes()
   record=bytes(m.mem_read(0x4a300000,native.get('native_record_bytes',232)));assert record[200:232]==bytes.fromhex(native['generation'])
   assert struct.unpack_from('<2I',record,4)==(native.get('native_record_version',1),len(record))
   if native.get('native_record_version')==2:assert struct.unpack_from('<2I',record,232)==(5,0)
   assert struct.unpack_from('<Q',record,16)[0]==modeled_ram_bytes and record[52:180]==expected_parameters
   assert not system.registers['sctlr_el3']&0x1005
 elif name.startswith('lab-success'):
  assert outcome==['wfi'] and tuple(gp[14:])==(0,0) and host.writes[-1]==(0x56000,512)
  assert host.writes.count((destination,65536))==1 and b''.join(card.blocks[destination+i] for i in range(128))==source
  assert int(row['wdt_final'][1],16)==0x31
 elif name.startswith('lab-bad'):
  assert outcome==['wfi'] and tuple(gp[14:])==(cookie,0x42534c32) and not host.writes
 elif damage_source:
  assert outcome==['wfi'] and not host.writes,row
 else:
  assert outcome==['wfi'] and host.writes==[(16,65536)] and row['journal_unchanged'],row
  assert b''.join(card.blocks[16+i] for i in range(128))==source
  assert row['wdt_final']==['0x1','0x1','0x14af'] and not any(x[0]=='native-dram' for x in events),row
 return row

rows=[]
cases=[('healthy-predram',{}),('prior-pending',{'pair':(cookie,0x42535031)}),('torn-cookie',{'pair':(cookie,0)}),('torn-magic',{'pair':(0,0x42535031)}),('corrupt-pair',{'pair':(0x11111111,0x22222222)}),('old-op2-not-lab',{'journal':'lab'}),('lab-success-primary',{'pair':(cookie,0x42534c31),'journal':'lab'}),('lab-success-mirror',{'pair':(cookie,0x42534c31),'journal':'lab','destination':0x56200}),('lab-bad-journal',{'pair':(cookie,0x42534c31),'journal':'bad'}),('lab-running-restart',{'pair':(cookie,0x42534c32),'journal':'lab'}),('bad-backup-refused',{'pair':(cookie,0x42535031),'damage_source':True}),('resume-priority',{'pair':(cookie,0x42535031),'resume':True})]
if args.full:cases=[('healthy-full',{'full':True})]
for name,case_arguments in cases:
 row=run(name,**case_arguments);rows.append(row);print(json.dumps({k:row[k] for k in ['name','outcome','elapsed_s','final_pair','media_writes','error']}),flush=True)
report=dict(passed=True,spl_sha256=hashlib.sha256(blob).hexdigest(),spl_bytes=len(blob),image_copy_end=hex(symbols['__image_copy_end']),native_generation=native['generation'],generation_bound_to_integration=True,durable_install_eligible=False,scope='actual linked A64 SPL/PIO; explicit EL3/MMC/PMIC/DRAM peripheral models',cases=rows)
(T/('persistent-full-verification.json' if args.full else 'persistent-verification.json')).write_text(json.dumps(report,indent=2)+'\n')
