#!/usr/bin/env python3
"""Compiled raw capsule instruction, MMIO ordering, refusal and context proof."""
from pathlib import Path
import argparse,hashlib,json,random,struct,time
import capstone,unicorn as u
from unicorn import arm_const as a
DIRECTORY=None;REGS=[getattr(a,'UC_ARM_REG_R'+str(i)) for i in range(13)]+[a.UC_ARM_REG_SP,a.UC_ARM_REG_LR]
RTC=0x07000000;GP=[RTC+0x100+4*i for i in range(16)];COOKIE,MARKER=GP[14:16];RET=RTC+0x1f8;WDT=0x03009000;CFG=WDT+0xb4;MODE=WDT+0xb8;CTRL=WDT+0xb0
sha=lambda b:hashlib.sha256(b).hexdigest();inputs={}
def wake_block(outer,stock):
 o=struct.unpack('<32I',outer);types={struct.unpack_from('<I',stock,at+4)[0] for at in [0x38]+[0x374+k*0x80 for k in range(15)]}
 return ('outer',bytes(outer)) if o[0] and o[1] in types else ('built-in',bytes(stock[0x38:0xb8]))
def symbols(e,mapping=False):
 shoff=struct.unpack_from('<I',e,32)[0];entsz,count,_=struct.unpack_from('<3H',e,46);ss=[struct.unpack_from('<10I',e,shoff+i*entsz) for i in range(count)];out={};marks=[]
 for h in ss:
  if h[1]!=2:continue
  st=ss[h[6]];names=e[st[4]:st[4]+st[5]]
  for at in range(h[4],h[4]+h[5],h[9]):
   n,v,sz,info,other,sec=struct.unpack_from('<IIIBBH',e,at)
   if n:out[names[n:].split(b'\0')[0].decode()]=v
   if n and names[n:n+2] in (b'$a',b'$d'):marks.append((v,chr(names[n+1])))
 return sorted(marks) if mapping else out

def run(variant,name,route='native',pair=(0,0),seed=1,media=0,mutation=None,sctlr=0x30c50030,mode=0x13,fault=None,initial_wdt=0xb0,retention=None):
 B=DIRECTORY;blob=(B/'capsule.egon.bin').read_bytes();manifest=json.loads((B/'manifest.json').read_text());syms=symbols((B/'capsule.elf').read_bytes());assert sha(blob)==manifest['raw_image_sha256'];assert len(blob)==0x20000 and syms['entry']==0x20400
 h=bytearray(blob);struct.pack_into('<I',h,12,0x5f0a6c39);assert sum(struct.unpack('<32768I',h))&0xffffffff==struct.unpack_from('<I',blob,12)[0]
 assert blob[0x1000:0x11000]==inputs['stock'] and blob[0x11000:0x1d000]==inputs['native']
 m=u.Uc(u.UC_ARCH_ARM,u.UC_MODE_ARM);m.ctl_set_cpu_model(a.UC_CPU_ARM_CORTEX_A15)
 m.mem_map(0x20000,0x38000);m.mem_write(0x20000,blob);m.mem_map(RTC,0x1000);m.mem_map(WDT,0x1000)
 rng=random.Random(seed);initial=[rng.getrandbits(32) for _ in REGS];initial[13]=0xdead0000+seed*0x100;initial[14]=rng.getrandbits(30)*4
 cpsr=(rng.getrandbits(4)<<28)|(rng.getrandbits(1)<<27)|(rng.getrandbits(4)<<16)|(rng.getrandbits(3)<<6)|mode
 m.reg_write(a.UC_ARM_REG_CPSR,cpsr)
 for reg,v in zip(REGS,initial):m.reg_write(reg,v)
 cpsr=m.reg_read(a.UC_ARM_REG_CPSR)
 def put(at,v):m.mem_write(at,struct.pack('<I',v&0xffffffff))
 def get(at):return struct.unpack('<I',m.mem_read(at,4))[0]
 regs={at:rng.getrandbits(32) for at in GP+[RTC+0x1f4,RET,RTC+0x1fc]};regs.update({COOKIE:pair[0],MARKER:pair[1],RET:retention if retention is not None else 0x10000 if route=='resume' else 0,CFG:1,MODE:initial_wdt,CTRL:0})
 if fault=='drop-config':regs[CFG]=0
 for at,v in regs.items():put(at,v)
 m.mem_write(0x20028,bytes([media]))
 if mutation:mutation(m,syms)
 outer_dram=bytes(m.mem_read(0x20038,128))
 md=capstone.Cs(capstone.CS_ARCH_ARM,capstone.CS_MODE_ARM);barriers=[];priv=[]
 marks=symbols((B/'capsule.elf').read_bytes(),mapping=True)
 code_word=lambda ad:[k for v,k in marks if v<=ad][-1:]==['a']
 for ad in [ad for ad in range(syms['entry'],syms['copy_end'],4) if code_word(ad)]:
  ins=next(md.disasm(bytes(m.mem_read(ad,4)),ad),None)
  if ins and ins.mnemonic in ['dsb','isb']:barriers.append(ad)
  if ins and ins.mnemonic in ['mcr','mcrr','smc','hvc']:raise AssertionError('forbidden architectural write/transition in outer')
 barriers += [0x50000 + pc - syms['copy_start'] for pc in barriers if syms['copy_start'] <= pc < syms['copy_end']]
 state=dict(name=name,variant=variant,outcome=None,events=[],write_ranges={},initial_pair=list(map(hex,pair)),media_byte=media,min_stack=0x57000,privileged=[],fault=fault)
 copied=[False];counts={};drops={};faults=set((fault or '').split('+'));allowed_writes={COOKIE,MARKER,CFG,MODE,CTRL} if variant=='protocol' else set()
 stub_size=syms['copy_end']-syms['copy_start']
 def event(kind,pc,address=None,value=None):
  state['events'].append(dict(kind=kind,pc=hex(pc),**({'address':hex(address),'value':hex(value)} if address is not None else {})))
 def code(mc,pc,size,data):
  if pc==syms['refuse']:state['outcome']='refused';mc.emu_stop();return
  if pc==0x50000:copied[0]=True
  if pc==0x20000 and copied[0]:state['outcome']='inner-entry';mc.emu_stop();return
  if pc==syms['entry']+20:
   assert bytes(mc.mem_read(pc,4))==bytes.fromhex('101f11ee');mc.reg_write(a.UC_ARM_REG_R1,sctlr);mc.reg_write(a.UC_ARM_REG_PC,pc+4);state['privileged'].append(dict(pc=hex(pc),op='read-SCTLR',value=hex(sctlr)))
  elif pc in barriers:event('barrier',pc)
 def read(mc,access,at,size,value,data):
  if not (RTC<=at<RTC+0x1000 or WDT<=at<WDT+0x1000):return
  assert at in set(GP+[RTC+0x1f4,RET,RTC+0x1fc,CFG,MODE,CTRL]),hex(at)
  counts[at]=counts.get(at,0)+1
  if at in drops:put(at,drops.pop(at))
  if 'pair-race' in faults and at==COOKIE and counts[at]==2:put(at,0x12345678)
  event('read',mc.reg_read(a.UC_ARM_REG_PC),at,get(at))
 def write(mc,access,at,size,value,data):
  if RTC<=at<RTC+0x1000 or WDT<=at<WDT+0x1000:
   assert at in allowed_writes,('unowned MMIO store',hex(at),name)
   event('write',mc.reg_read(a.UC_ARM_REG_PC),at,value)
   if ('drop-cookie' in faults and at==COOKIE) or ('drop-marker' in faults and at==MARKER) or ('drop-mode' in faults and at==MODE and value==0xb1) or ('drop-config' in faults and at==CFG) or ('drop-stop' in faults and at==MODE and value==0):drops[at]=get(at)
   return
  if syms['saved_context']<=at and at+size<=syms['saved_context']+72:region='entry-context'
  elif 0x56000<=at and at+size<=0x57000:region='private-stack';state['min_stack']=min(state['min_stack'],at)
  elif 0x50000<=at and at+size<=0x50000+stub_size:region='copy-stub'
  elif 0x52000<=at and at+size<=0x52048:region='copy-context'
  elif copied[0] and 0x20000<=at and at+size<=0x30000:region='selected-output'
  elif route=='resume' and not copied[0] and 0x21038<=at and at+size<=0x210b8:region='wake-dram-block'
  else:raise AssertionError(('out-of-bounds SRAM store',hex(at),hex(mc.reg_read(a.UC_ARM_REG_PC)),size,name))
  lo,hi=state['write_ranges'].get(region,(at,at+size));state['write_ranges'][region]=(min(lo,at),max(hi,at+size))
 def invalid(mc,access,at,size,value,data):raise AssertionError(('unmapped access',hex(at),hex(mc.reg_read(a.UC_ARM_REG_PC)),name))
 handles=[]
 for pc in set([0x20000,0x50000,syms['refuse'],syms['entry']+20]+barriers):handles.append(m.hook_add(u.UC_HOOK_CODE,code,begin=pc,end=pc))
 handles.extend([m.hook_add(u.UC_HOOK_MEM_WRITE,write),m.hook_add(u.UC_HOOK_MEM_READ,read,begin=RTC,end=RTC+0xfff),m.hook_add(u.UC_HOOK_MEM_READ,read,begin=WDT,end=WDT+0xfff),m.hook_add(u.UC_HOOK_MEM_INVALID,invalid)])
 m._raw_handles=handles;started=time.monotonic();m.emu_start(0x20000,0,count=20000000);state['elapsed_seconds']=time.monotonic()-started
 assert state['outcome'],(name,hex(m.reg_read(a.UC_ARM_REG_PC)))
 state['final_pair']=[hex(get(COOKIE)),hex(get(MARKER))];state['final_watchdog']={hex(at):hex(get(at)) for at in [CFG,MODE,CTRL]};state['mmio_writes']=[x for x in state['events'] if x['kind']=='write']
 for at,v in regs.items():
  if at not in allowed_writes:assert get(at)==v,('unowned register changed',hex(at))
 if route=='resume' or any(pair):assert not state['mmio_writes'],('stock route wrote MMIO',name)
 if state['outcome']=='inner-entry':
  selected='native' if bytes(m.mem_read(0x20000,4))==inputs['native'][:4] else 'stock';wanted=bytearray(inputs[selected]);wanted[0x28]=media
  if route=='resume':state['wake_block'],wanted[0x38:0xb8]=wake_block(outer_dram,inputs['stock'])
  assert bytes(m.mem_read(0x20000,len(wanted)))==wanted,('selected output differs',name)
  assert [m.reg_read(r) for r in REGS]==initial,('registers changed',name)
  assert m.reg_read(a.UC_ARM_REG_CPSR)==cpsr,('CPSR changed',name)
  assert struct.unpack('<I',m.mem_read(0x52040,4))[0]==sctlr
  state.update(selected=selected,output_sha256=sha(wanted),all_output_bytes_exact_except_explicit_media_transfer=True,r0_r14_and_cpsr_exact=True,original_sp_restored_verbatim=True,sctlr_unchanged_no_mcr=True,mvbar_scr_untouched_no_reads_or_writes=True)
  if selected=='native' and variant=='protocol':assert get(COOKIE)==manifest['generation_cookie'] and get(MARKER)==0x42535031 and get(CFG)==1 and get(MODE)&0xf1==0xb1
 else:assert not copied[0],name
 state['write_ranges']={n:list(map(hex,r)) for n,r in state['write_ranges'].items()};state['passed']=True
 return state,m,initial,cpsr,syms

def validate_normal_order(row,cookie):
 events=row['events'];writes=[(i,e) for i,e in enumerate(events) if e['kind']=='write']
 assert [(int(e['address'],16),int(e['value'],16)) for i,e in writes]==[(CFG,1),(MODE,0xb1),(CTRL,0x14af),(COOKIE,cookie),(MARKER,0x42535031)]
 for (left,_),(right,_) in zip(writes,writes[1:]):assert any(e['kind']=='barrier' for e in events[left+1:right])
 ci=writes[3][0];mi=writes[4][0]
 before=[(int(e['address'],16),int(e['value'],16)) for e in events[:ci] if e['kind']=='read' and int(e['address'],16) in [COOKIE,MARKER]]
 staged=[(int(e['address'],16),int(e['value'],16)) for e in events[ci+1:mi] if e['kind']=='read' and int(e['address'],16) in [COOKIE,MARKER]]
 pending=[(int(e['address'],16),int(e['value'],16)) for e in events[mi+1:] if e['kind']=='read' and int(e['address'],16) in [COOKIE,MARKER]]
 assert before==[(COOKIE,0),(MARKER,0)]*3 and staged==[(COOKIE,cookie),(MARKER,0)] and pending==[(COOKIE,cookie),(MARKER,0x42535031)]
 for wi,addr,value in [(writes[0][0],CFG,1),(writes[1][0],MODE,0xb1)]:
  next_write=next(i for i,e in writes if i>wi);assert any(e['kind']=='read' and int(e['address'],16)==addr and int(e['value'],16)==value for e in events[wi+1:next_write])
 assert any(e['kind']=='barrier' and int(e['pc'],16)>=0x50000 for e in events[mi+1:])
 row['strict_commit_and_watchdog_readback_order_proven']=True

def main():
 global DIRECTORY,inputs
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);parser.add_argument('--diagnostic',action='store_true');args=parser.parse_args();DIRECTORY=args.directory.resolve()
 inputs={n:(DIRECTORY/f'{n}.bin').read_bytes() for n in ['native','stock']}
 manifest=json.loads((DIRECTORY/'manifest.json').read_text());cookie=manifest['generation_cookie'];assert manifest['normal_protocol_compiled'] and bool(manifest['diagnostic'])==args.diagnostic
 cases=[]
 for variant in ['protocol']:
  for media in [0,1,0x10,0x12,0xff]:
   for route,pair in [('native',(0,0)),('resume',(0xaabbccdd,0x11223344)),('latched',(0,0x42535031))]:
    row,*_=run(variant,f'{variant}-{route}-media{media:x}',route,pair,seed=10+media,media=media);assert row['outcome']=='inner-entry' and row['selected']==('native' if route=='native' else 'stock');cases.append(row)
  for pair in [(1,0),(0,1),(cookie,0x42535031),(0x12345678,0xdeadbeef)]:
   row,*_=run(variant,f'{variant}-latched-{pair}',pair=pair);assert row['selected']=='stock';cases.append(row)
  for bit in [1,4,0x1000]:
   row,*_=run(variant,f'{variant}-cache-refusal-{bit}',sctlr=0x30c50030|bit);assert row['outcome']=='refused' and not row['mmio_writes'];cases.append(row)
  row,*_=run(variant,variant+'-mode-refusal',mode=0x1f);assert row['outcome']=='refused';cases.append(row)
  row,*_=run(variant,variant+'-endian-refusal',mode=0x213);assert row['outcome']=='refused';cases.append(row)
  mutations=[('outer-length',lambda m,s:m.mem_write(0x20010,struct.pack('<I',0x1ffff)),'refused'),('outer-magic',lambda m,s:m.mem_write(0x20004,b'x'),'refused'),('stock-integrity',lambda m,s:m.mem_write(0x22000,bytes([m.mem_read(0x22000,1)[0]^1])),'refused'),('native-integrity',lambda m,s:m.mem_write(0x32000,bytes([m.mem_read(0x32000,1)[0]^1])),'stock')]
  for name,mut,expected in mutations:
   row,*_=run(variant,variant+'-'+name,mutation=mut);assert row['outcome']=='refused' if expected=='refused' else row['selected']==expected;assert not row['mmio_writes'];cases.append(row)
 for fault in ['drop-config','drop-mode','drop-cookie','drop-marker','pair-race']:
  row,*_=run('protocol',fault,fault=fault);assert row['selected']=='stock' and int(row['final_watchdog'][hex(MODE)],16)&1==0;cases.append(row)
 row,*_=run('protocol','preexisting-watchdog',initial_wdt=0xb1);assert row['selected']=='stock' and not row['mmio_writes'];cases.append(row)
 row,*_=run('protocol','drop-marker-and-stop',fault='drop-marker+drop-stop');assert row['outcome']=='refused' and int(row['final_watchdog'][hex(MODE)],16)&1==1;cases.append(row)
 failed_pair=tuple(int(v,16) for v in row['final_pair']);assert failed_pair[0] and not failed_pair[1]
 row,*_=run('protocol','post-reset-latched-stock',pair=failed_pair,initial_wdt=0xb0);assert row['selected']=='stock' and not row['mmio_writes'];cases.append(row)
 # A wrong-address implementation must be rejected by the independently derived MMIO oracle.
 def wrong_address(m,s):
  for function in ['rtc_read','rtc_write']:
   for off in [0,16]:
    at=s[function]+off;word=struct.unpack('<I',m.mem_read(at,4))[0]
    assert word&0x0ff00fff==0x03000138,(function,off,hex(word))
    m.mem_write(at,struct.pack('<I',(word&~0xfff)|0x1d8))
 try:run('protocol','mutation-wrong-RTC-address',mutation=wrong_address)
 except AssertionError as e:assert 'unowned' in str(e) or '0x70001d8' in str(e);wrong_address_rejected=True
 else:raise AssertionError('wrong RTC mutation accepted')
 for row in cases:
  if row.get('selected')=='native' and row['variant']=='protocol':validate_normal_order(row,cookie)
 report=dict(passed=True,capsule_sha256=manifest['raw_image_sha256'],generation=manifest['generation'],cases=cases,wrong_RTC_implementation_mutation_rejected=wrong_address_rejected,device_access=False,hardware_qualified=False,durable_install_eligible=False,models=['Actual compiled ARM entry, selectors, integrity loops, exact copied protocol header implementation, fences, watchdog MMIO stores, overlapping forward copy, header media transfer and complete register/CPSR restoration execute. No function bodies are replaced.','Initial SCTLR read is supplied; all DRAM and unrelated MMIO pages unmapped. A32 Cortex-A15-compatible ISA engine executes Cortex-A53-targeted instructions; physical cache/security/reset/selector ownership not modeled.','Failure fixtures suppress selected MMIO write visibility at following readback or change a pair between selection and commit; no real hardware failure/reset is induced.','Inner entry/adoption is separately verified; no physical reset/security/cache/ownership behavior or hardware eligibility is claimed.'])
 (DIRECTORY/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(dict(passed=True,cases=len(cases),wrong_RTC_mutation_rejected=wrong_address_rejected)))
if __name__=='__main__':main()
