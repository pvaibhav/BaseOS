#!/usr/bin/env python3
"""Prove canonical panel SPI9 transport against all11 pinned vendor U-Boots.

Host-only. Requires Clang, ctypes, Capstone and Unicorn; Unicorn JIT must be
permitted by the host. --output must be a fresh directory. No device access.
"""
from pathlib import Path
import ctypes as C
import argparse,hashlib,importlib.util,json,platform,shutil,struct,subprocess,sys
import capstone as cs
import unicorn as u
from unicorn import arm_const as ar,arm64_const as aa

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OUT=None
sys.dont_write_bytecode=True
BASE,STOP,STACK=0x4a000000,0x7000f000,0x4a0ff000
CFG,DAT,MASK,OUTPUT=0x0300b124,0x0300b130,0xff000fff,0x11000111
SIGNATURE=bytes.fromhex('5f4af0b5136853b95e490123')
sha=lambda b:hashlib.sha256(b).hexdigest()
md=cs.Cs(cs.CS_ARCH_ARM,cs.CS_MODE_THUMB);md.detail=True

READ=C.CFUNCTYPE(C.c_uint32,C.c_void_p,C.c_uint32)
WRITE=C.CFUNCTYPE(None,C.c_void_p,C.c_uint32,C.c_uint32)
BARRIER=C.CFUNCTYPE(None,C.c_void_p)
class IO(C.Structure):_fields_=[('context',C.c_void_p),('read',READ),('write',WRITE),('barrier',BARRIER)]
class Port(C.Structure):_fields_=[('io',IO),('configured',C.c_uint)]
lib=None

def host(byte,data,cfg,dat,configured):
 regs={CFG:cfg,DAT:dat};events=[]
 def rd(ctx,ad):assert ad in regs;events.append(['read',ad,regs[ad]]);return regs[ad]
 def wr(ctx,ad,value):assert ad in regs;regs[ad]=value;events.append(['write',ad,value])
 def barrier(ctx):events.append(['dmb-sy'])
 rd,wr,barrier=READ(rd),WRITE(wr),BARRIER(barrier)
 port=Port(IO(None,rd,wr,barrier),configured);lib.spi9_send(C.byref(port),byte,data)
 assert port.configured==1
 return events,regs

def validate(events,cfg,dat,configured,byte,data):
 assert len(events)==(116 if configured else 120),len(events)
 groups=[events[i:i+4] for i in range(0,len(events),4)]
 for group in groups:
  assert group[0][0]=='read' and group[1:3]==[['dmb-sy'],['dmb-sy']] and group[3][0]=='write'
  assert group[0][1]==group[3][1]
 writes=[e for e in events if e[0]=='write'];gpio=[e[2] for e in writes if e[1]==DAT]
 assert len(gpio)==29
 if configured:assert all(e[1]==DAT for e in writes)
 else:assert writes[0]==['write',CFG,(cfg&~MASK)|OUTPUT]
 previous=dat;bits=[]
 for value in gpio:
  assert value&~0x700==dat&~0x700
  if not previous&0x200 and value&0x200 and not value&0x100:bits.append(int(bool(value&0x400)))
  previous=value
 assert bits==[int(bool(data))]+[(byte>>bit)&1 for bit in range(7,-1,-1)],(byte,data,bits)
 assert not gpio[0]&0x100 and gpio[-1]&0x300==0x300
 return bits

class Vendor:
 def __init__(self,blob):
  self.blob=blob
  self.leaves=[off for off in range(0x262e4,len(blob),2) if blob[off:off+len(SIGNATURE)]==SIGNATURE]
  assert len(self.leaves)==2 and self.leaves[1]-self.leaves[0]==0x190,self.leaves
  self.machine=u.Uc(u.UC_ARCH_ARM,u.UC_MODE_THUMB);m=self.machine
  m.mem_map(BASE,0x100000);m.mem_write(BASE,blob);m.mem_map(0x0300b000,0x1000);m.mem_map(0x70000000,0x10000)
  self.events=[];self.regs={};self.count=0
  self.globals=[];self.barriers=set();self.literal_rows=[]
  for leaf in self.leaves:
   ins=next(md.disasm(blob[leaf:leaf+4],BASE+leaf));assert ins.mnemonic=='ldr' and ins.operands[1].mem.base==cs.arm.ARM_REG_PC
   offset=((leaf+4)&~3)+ins.operands[1].mem.disp
   self.globals.append(struct.unpack_from('<I',blob,offset)[0])
   literals=[]
   for ins in md.disasm(blob[leaf:leaf+0x180],BASE+leaf):
    if ins.mnemonic=='dmb':assert ins.op_str=='sy';self.barriers.add(ins.address)
    if ins.mnemonic=='ldr' and len(ins.operands)>1 and ins.operands[1].type==cs.arm.ARM_OP_MEM and ins.operands[1].mem.base==cs.arm.ARM_REG_PC:
     pos=(((ins.address-BASE)+4)&~3)+ins.operands[1].mem.disp
     literals.append(struct.unpack_from('<I',blob,pos)[0])
   assert {CFG,DAT,OUTPUT}<=set(literals)
   self.literal_rows.append(sorted(set(literals)))
  assert len(set(self.globals))==1
  self.flag=self.globals[0]
  def rd(uc,access,ad,size,value,ctx):
   assert size==4 and ad in self.regs
   value=self.regs[ad];self.events.append(['read',ad,value]);uc.mem_write(ad,struct.pack('<I',value))
  def wr(uc,access,ad,size,value,ctx):
   assert size==4 and ad in self.regs
   self.regs[ad]=value;self.events.append(['write',ad,value])
  def code(uc,pc,size,ctx):
   self.count+=1
   if pc in self.barriers:self.events.append(['dmb-sy'])
  m.hook_add(u.UC_HOOK_MEM_READ,rd,begin=0x0300b000,end=0x0300bfff)
  m.hook_add(u.UC_HOOK_MEM_WRITE,wr,begin=0x0300b000,end=0x0300bfff)
  m.hook_add(u.UC_HOOK_CODE,code,begin=BASE,end=BASE+0xfffff)
 def run(self,byte,data,cfg,dat,configured):
  m=self.machine;self.events=[];self.regs={CFG:cfg,DAT:dat};self.count=0
  m.mem_write(self.flag,struct.pack('<I',configured));m.reg_write(ar.UC_ARM_REG_SP,STACK);m.reg_write(ar.UC_ARM_REG_R0,byte);m.reg_write(ar.UC_ARM_REG_LR,STOP|1)
  m.emu_start(BASE+self.leaves[data]|1,STOP,count=2000)
  assert m.reg_read(ar.UC_ARM_REG_PC)==STOP and m.reg_read(ar.UC_ARM_REG_SP)==STACK
  assert struct.unpack('<I',m.mem_read(self.flag,4))[0]==1
  return self.events,self.regs,self.count

def elf_sections(blob):
 offset=struct.unpack_from('<Q',blob,40)[0];size,count,names_index=struct.unpack_from('<3H',blob,58)
 rows=[struct.unpack_from('<IIQQQQIIQQ',blob,offset+i*size) for i in range(count)]
 n=rows[names_index];names=blob[n[4]:n[4]+n[5]]
 return {names[h[0]:].split(b'\0',1)[0].decode():{'header':h,'data':blob[h[4]:h[4]+h[5]]} for h in rows}

class ActualA64:
 def __init__(self):
  self.blob=(OUT/'spi9-a64.o').read_bytes();sections=elf_sections(self.blob)
  assert not any(name.startswith('.rela') for name in sections),'object needs relocation before replay'
  self.text=sections['.text']['data'];self.sections={name:row['header'][5] for name,row in sections.items()}
  symbols=sections['.symtab'];strings=sections['.strtab']['data'];found=[]
  for off in range(0,len(symbols['data']),24):
   n,info,other,index,value,size=struct.unpack_from('<IBBHQQ',symbols['data'],off)
   if strings[n:].split(b'\0',1)[0]==b'spi9_send':found.append(value)
  assert len(found)==1;self.entry=0x40000000+found[0]
  self.m=u.Uc(u.UC_ARCH_ARM64,u.UC_MODE_ARM);m=self.m;m.ctl_set_cpu_model(aa.UC_CPU_ARM64_A53)
  m.mem_map(0x40000000,0x1000);m.mem_write(0x40000000,self.text);m.mem_map(0x41000000,0x20000);m.mem_map(0x42000000,0x1000)
  # Raw callback bodies are modeled; the barrier callback executes actual DMB SY.
  m.mem_write(0x42000000,bytes.fromhex('c0035fd6c0035fd6bf3f03d5c0035fd6'))
  check=cs.Cs(cs.CS_ARCH_ARM64,cs.CS_MODE_ARM);assert next(check.disasm(bytes.fromhex('bf3f03d5'),0)).mnemonic=='dmb'
  self.events=[];self.regs={};self.count=0
  def hook(uc,pc,size,ctx):
   self.count+=1
   if pc==0x42000000:
    ad=uc.reg_read(aa.UC_ARM64_REG_X1);assert ad in self.regs;self.events.append(['read',ad,self.regs[ad]]);uc.reg_write(aa.UC_ARM64_REG_X0,self.regs[ad])
   elif pc==0x42000004:
    ad=uc.reg_read(aa.UC_ARM64_REG_X1);value=uc.reg_read(aa.UC_ARM64_REG_X2)&0xffffffff;assert ad in self.regs;self.regs[ad]=value;self.events.append(['write',ad,value])
   elif pc==0x42000008:self.events.append(['dmb-sy'])
  m.hook_add(u.UC_HOOK_CODE,hook)
 def run(self,byte,data,cfg,dat,configured):
  self.events=[];self.regs={CFG:cfg,DAT:dat};self.count=0;m=self.m
  m.mem_write(0x41000000,struct.pack('<4QI4x',0,0x42000000,0x42000004,0x42000008,configured));m.reg_write(aa.UC_ARM64_REG_X0,0x41000000);m.reg_write(aa.UC_ARM64_REG_X1,byte);m.reg_write(aa.UC_ARM64_REG_X2,data);m.reg_write(aa.UC_ARM64_REG_X30,0x42000100);m.reg_write(aa.UC_ARM64_REG_SP,0x4101ff00)
  m.emu_start(self.entry,0x42000100,count=5000)
  assert m.reg_read(aa.UC_ARM64_REG_PC)==0x42000100 and m.reg_read(aa.UC_ARM64_REG_SP)==0x4101ff00
  assert struct.unpack('<I',m.mem_read(0x41000020,4))[0]==1
  return self.events,self.regs,self.count

def main():
 global OUT,lib
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--output',required=True,type=Path,help='fresh proof/build output directory')
 parser.add_argument('--compiler',default='clang',help='Clang executable for host and freestanding A64')
 args=parser.parse_args()
 if not __debug__:parser.error('run without Python -O: proof assertions must remain enabled')
 OUT=args.output.expanduser().absolute()
 if OUT.exists() or OUT.is_symlink():parser.error('use a fresh output directory')
 compiler=shutil.which(args.compiler)
 if not compiler:parser.error('Clang compiler not found')
 version=subprocess.run([compiler,'--version'],check=True,text=True,capture_output=True).stdout
 if 'clang' not in version.lower():parser.error('--compiler must identify a Clang toolchain')
 if sys.platform not in ('darwin','linux'):parser.error('host library build supports macOS/Linux')
 header_path=HERE/'panel-spi9.h';header=header_path.read_bytes()
 contract_path=HERE/'panel-contract.json';contract_bytes=contract_path.read_bytes();contracts=json.loads(contract_bytes)['models']
 expected={'rg28xx','rg34xx','rg34xxsp','rg35xxh','rg35xxplus','rg35xxpro','rg35xxsp','rg40xxh','rg40xxv','rgcubexx','rgsp'}
 assert set(contracts)==expected,'canonical panel contract must cover exactly all11 targets'
 toc_path=HERE/'prepare-recovery-toc.py'
 spec=importlib.util.spec_from_file_location('panel_spi9_toc',toc_path);toc=importlib.util.module_from_spec(spec);spec.loader.exec_module(toc)
 source_hashes={str(path.relative_to(ROOT)):sha(path.read_bytes()) for path in (Path(__file__).resolve(),header_path,contract_path,toc_path)}
 inputs={};uboots={}
 for target,policy in contracts.items():
  package_path=ROOT/'work'/target/'boot-gzip/boot-package.bin';package=package_path.read_bytes()
  assert sha(package)==policy['package_sha256'],('prepared package pin differs',target)
  length,items=toc.parse(package);offset,size=items['u-boot'];uboot=package[offset:offset+size]
  assert sha(uboot)==policy['vendor_uboot_sha256'],('U-Boot pin differs',target)
  source_hashes[str(package_path.relative_to(ROOT))]=sha(package);uboots[target]=uboot
  inputs[target]=dict(package=str(package_path.relative_to(ROOT)),package_sha256=sha(package),package_bytes=len(package),declared_bytes=length,uboot_offset=offset,uboot_bytes=size,uboot_sha256=sha(uboot),TOC_and_Uboot_checksums_valid=True)
 OUT.mkdir(parents=True,exist_ok=False);(OUT/'source').mkdir();(OUT/'vendor').mkdir()
 for path in (Path(__file__).resolve(),header_path,contract_path,toc_path):(OUT/'source'/path.name).write_bytes(path.read_bytes())
 vendor_paths={}
 for target,uboot in uboots.items():
  folder=OUT/'vendor'/target;folder.mkdir();vendor_paths[target]=folder/'uboot.bin';vendor_paths[target].write_bytes(uboot)
 wrapper=OUT/'wrapper.c'
 wrapper.write_text('#include <panel-spi9.h>\nvoid spi9_send(struct baseos_panel_spi9 *port, uint8_t byte, unsigned int data)\n{\n    baseos_panel_spi9_send(port, byte, data);\n}\n')
 library=OUT/('spi9.dylib' if sys.platform=='darwin' else 'spi9.so')
 common=[compiler,'-std=c11','-Os','-Wall','-Wextra','-Werror','-I',str(HERE)]
 host_command=common+(['-dynamiclib'] if sys.platform=='darwin' else ['-shared','-fPIC'])+['-MMD','-MF',str(OUT/'spi9-host.d'),str(wrapper),'-o',str(library)]
 a64_command=common+['-target','aarch64-none-elf','-mcpu=cortex-a53','-ffreestanding','-fno-stack-protector','-fstack-usage','-MMD','-MF',str(OUT/'spi9-a64.d'),'-c',str(wrapper),'-o',str(OUT/'spi9-a64.o')]
 commands=[host_command,a64_command]
 for command in commands:
  result=subprocess.run(command,text=True,capture_output=True)
  with (OUT/'compile.log').open('a') as log:log.write(json.dumps(command)+'\n'+result.stdout+result.stderr)
  if result.returncode:raise RuntimeError('compiler failed; see output/compile.log')
 for dep in ('spi9-host.d','spi9-a64.d'):
  dependency=(OUT/dep).read_text();assert str(header_path) in dependency
  assert str(OUT/'source'/'panel-spi9.h') not in dependency,'frozen header shadowed canonical source'
 (OUT/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
 (OUT/'build.json').write_text(json.dumps(dict(compiler=compiler,version=version,commands=commands,python=sys.version,unicorn=u.__version__,capstone=cs.__version__,canonical_header_dependency_verified=True),indent=2)+'\n')
 lib=C.CDLL(str(library));lib.spi9_send.argtypes=[C.POINTER(Port),C.c_uint8,C.c_uint];lib.spi9_send.restype=None
 assert C.sizeof(Port)==40 and Port.configured.offset==32,'this proof requires a64-compatible64bit host state ABI'

 legal=[(0,0),(OUTPUT,0xffffffff),((0xa5c35a69&~MASK)|0x10000001,0x5a6bc39d)]
 # All owned fields here independently contain only zero/one, the vendor OR's actual precondition.
 assert all(all((cfg>>(n*4)&15) in (0,1) for n in (0,1,2,6,7)) for cfg,_ in legal)
 a64=ActualA64();a64_count=0;a64_range=[100000,0];a64_hash=hashlib.sha256()
 for cfg,dat in legal+[(0xffffffff,0xa5a55a5a)]:
  for configured in (0,1):
   for data in (0,1):
    for byte in range(256):
     c_events,c_regs=host(byte,data,cfg,dat,configured);validate(c_events,cfg,dat,configured,byte,data)
     events,regs,count=a64.run(byte,data,cfg,dat,configured);assert events==c_events and regs==c_regs
     a64_hash.update(json.dumps(events,separators=(',',':')).encode());a64_count+=1;a64_range=[min(a64_range[0],count),max(a64_range[1],count)]
 print(json.dumps({'actual_compiled_A64_cases':a64_count,'text_bytes':len(a64.text),'passed':True}),flush=True)
 rows=[]
 for target,policy in contracts.items():
  path=vendor_paths[target];blob=path.read_bytes();assert sha(blob)==policy['vendor_uboot_sha256']
  vendor=Vendor(blob);count_cases=0;instruction_range=[100000,0];trace_hash=hashlib.sha256()
  for cfg,dat in legal:
   for configured in (0,1):
    for data in (0,1):
     for byte in range(256):
      expected,expected_regs=host(byte,data,cfg,dat,configured);events,regs,count=vendor.run(byte,data,cfg,dat,configured)
      assert events==expected and regs==expected_regs,(target,byte,data,cfg,dat,configured)
      validate(events,cfg,dat,configured,byte,data);trace_hash.update(json.dumps(events,separators=(',',':')).encode());count_cases+=1;instruction_range=[min(instruction_range[0],count),max(instruction_range[1],count)]
  # A mux value such as 0xf cannot be repaired by OR1. The candidate deliberately differs only at the owned mux fields.
  differences=0
  for cfg,dat in [(0xffffffff,0xa5a55a5a),(0x22222222,0x12345678)]:
   for data in (0,1):
    for byte in range(256):
     events,regs,count=vendor.run(byte,data,cfg,dat,0);expected,c_regs=host(byte,data,cfg,dat,0)
     assert events[:3]==expected[:3] and events[4:]==expected[4:]
     assert events[3]==['write',CFG,cfg|OUTPUT]
     assert expected[3]==['write',CFG,(cfg&~MASK)|OUTPUT]
     assert (regs[CFG]^c_regs[CFG])&~MASK==0 and regs[DAT]==c_regs[DAT]
     validate(expected,cfg,dat,0,byte,data);differences+=1
  row=dict(target=target,passed=True,uboot_sha256=sha(blob),leaf_offsets=[hex(x) for x in vendor.leaves],leaf_sha256=[sha(blob[x:x+0x190]) for x in vendor.leaves],vendor_init_flag_address=hex(vendor.flag),literal_values=[[hex(x) for x in values] for values in vendor.literal_rows],exact_event_cases=count_cases,intentional_mux_normalization_cases=differences,vendor_instruction_range=instruction_range,event_digest=trace_hash.hexdigest())
  rows.append(row);print(json.dumps({k:row[k] for k in ('target','passed','exact_event_cases','intentional_mux_normalization_cases')}),flush=True)
 assert all(sha((ROOT/path).read_bytes())==digest for path,digest in source_hashes.items()),'source changed during proof'
 report=dict(passed=True,canonical_header_sha256=sha(header),canonical_header_dependency_verified=True,prepared_package_inputs=inputs,compiler=compiler,compiler_version=version,scope='actual Thumb vendor leaves for all11, host compiled C callback sender, and actual compiled A64 sender with raw GPIO callback models and actual DMB SY callback',source_hashes=source_hashes,models=rows,model_count=len(rows),exact_vendor_cases=sum(row['exact_event_cases'] for row in rows),intentional_mux_normalization_cases=sum(row['intentional_mux_normalization_cases'] for row in rows),a64_cases=a64_count,a64_instruction_range=a64_range,a64_event_digest=a64_hash.hexdigest(),a64_sections=a64.sections,protocol={'config_register':hex(CFG),'data_register':hex(DAT),'mux_owned_mask':hex(MASK),'mux_output_value':hex(OUTPUT),'cs':8,'clock':9,'mosi':10,'bits':'command0/data1 then byte MSB first','gpio_writes':29,'gpio_reads':29,'barriers':58,'additional_first_use':{'reads':1,'writes':1,'barriers':2},'explicit_delay_instructions':0},limitations=['No electrical timing or panel-hardware qualification. DMB orders accesses; it does not specify a minimum clock period.','The callbacks and state require a single exclusive owner. configured must start zero; nonzero means this helper has already configured the mux.','Vendor first-use OR matches only owned mux nibbles initially0/1. Candidate forcibly normalizes the five owned nibbles to1; all unrelated mux and GPIO bits are preserved.'],device_access=False,hardware_qualified=False)
 (OUT/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
 pins={str(path.relative_to(OUT)):sha(path.read_bytes()) for path in OUT.rglob('*') if path.is_file() and path.name!='seal.json'}
 (OUT/'seal.json').write_text(json.dumps(dict(passed=True,artifacts=pins,sources=source_hashes,device_access=False),indent=2)+'\n')
 print(json.dumps({'passed':True,'vendor_cases':report['exact_vendor_cases'],'mux_cases':report['intentional_mux_normalization_cases'],'a64_cases':a64_count,'report':str(OUT/'verification.json')}),flush=True)

if __name__=='__main__':
 try:main()
 except (AssertionError,OSError,RuntimeError,ValueError) as error:raise SystemExit(f'panel-spi9 proof refused: {error}')
