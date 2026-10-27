#!/usr/bin/env python3
"""Host replay of vendor framebuffer copy with cold or authenticated warm state."""
import argparse,hashlib,json,re,struct,sys
from pathlib import Path
import capstone as c
import unicorn as u
from unicorn import arm64_const as a
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'experiments/h700-boot-speed'))
import fdt
BASE=0xffffff8008080000
HEAP=0xffff800050000000
RETURN=HEAP+0x1ff0000
COPY_PREFIX=bytes.fromhex('ff0305d1fd7b02a9fd830091f55b04a9f50300aa')
REQUEST_PREFIX=bytes.fromhex('ff4307d123008052fd7bbaa9fd030091f35301a9f403002a')
md=c.Cs(c.CS_ARCH_ARM64,c.CS_MODE_ARM);md.detail=True
MASK=0xffffffffffffffff
sha=lambda b:hashlib.sha256(b).hexdigest()

def ins(blob,offset):return next(md.disasm(blob[offset:offset+4],BASE+offset))
def target(blob,offset):
 i=ins(blob,offset);assert i.mnemonic=='bl',(hex(offset),i.mnemonic,i.op_str)
 return i.operands[0].imm&MASK

def pair_address(blob,at,add):
 p=ins(blob,at);q=ins(blob,add)
 assert p.mnemonic=='adrp' and q.mnemonic=='add'
 return ((p.operands[1].imm&MASK)+q.operands[2].imm)&MASK

def run_case(directory,width,height,present=True,source_bpp=32,legacy_reserve=False,crop=None,pixels=None,property_value=None,warm=False,physical_address=None):
 manifest=json.loads((directory/'manifest.json').read_text());blob=(directory/'kernel.bin').read_bytes()
 assert sha(blob)==manifest['kernel_sha256']==manifest['source_kernel_sha256']
 if physical_address is None:physical_address=0x80000000-manifest['boot_logo']['framebuffer_from_ram_end']
 cf=blob.find(COPY_PREFIX);rq=blob.find(REQUEST_PREFIX)
 assert cf>=0 and rq==cf+0x640 and blob.find(COPY_PREFIX,cf+1)<0 and blob.find(REQUEST_PREFIX,rq+1)<0
 fbi=pair_address(blob,rq+0x1c,rq+0x44)
 managers=pair_address(blob,rq+0x1b0,rq+0x1b4)
 drv=managers-0x1f0
 prop=target(blob,cf+0x90)
 memstart_page=ins(blob,cf+0x3b4).operands[1].imm&MASK
 memstart_load=ins(blob,cf+0x3bc)
 assert memstart_load.mnemonic=='ldr'
 memstart=memstart_page+memstart_load.operands[1].mem.disp
 m=u.Uc(u.UC_ARCH_ARM64,u.UC_MODE_ARM)
 m.mem_map(BASE,0x2000000);m.mem_write(BASE,blob)
 m.mem_map(HEAP,0x2000000)
 info,args,manager,ion=HEAP+0x1000,HEAP+0x2000,HEAP+0x3000,HEAP+0x4000
 source,destination,array,text=HEAP+0x400000,HEAP+0x800000,HEAP+0xc00000,HEAP+0xd00000
 callback=HEAP+0xf00000
 if pixels is None:
  pixels=b''.join(struct.pack('<I',0xff000000|((i*7919)&0xffffff)) for i in range(width*height))
 assert len(pixels)==width*height*4
 length=width*height*4
 m.mem_write(source,pixels)
 crop=crop or(0,0,width,height)
 value=(f'{physical_address:x},{width:x},{height:x},{source_bpp:x},{width*4:x},'
        +','.join(f'{v:x}' for v in crop)).encode()+b'\0'
 if property_value is not None:value=property_value
 m.mem_write(text,value)
 w32=lambda p,v:m.mem_write(p,struct.pack('<I',v))
 w64=lambda p,v:m.mem_write(p,struct.pack('<Q',v))
 w64(fbi+0x28,info)
 w64(managers,manager);w64(manager+0xf8,callback)
 w64(memstart,0x40000000)
 for offset,v in [(0,0),(0xc,width),(0x10,height),(0x14,width),(0x18,height)]:w32(args+offset,v)
 properties,copies,layer_callbacks,flushes,frees,mappings=[],[],[],[],[],[]
 reserves=[]
 freed=[False]
 operations={}
 def cstr(p):return bytes(m.mem_read(p,256)).split(b'\0',1)[0]
 def register(pc,name,fn):
  assert pc not in operations or operations[pc][0]==name,(name,operations.get(pc))
  operations[pc]=(name,fn)
 def returns(fn):
  def callback(uc,pc,size,data):
   result=fn()
   if result is not None:uc.reg_write(a.UC_ARM64_REG_X0,result&MASK)
   uc.reg_write(a.UC_ARM64_REG_PC,uc.reg_read(a.UC_ARM64_REG_LR))
  return callback
 reg=lambda i:m.reg_read(a.UC_ARM64_REG_X0+i)
 def memcpy():
  dst,src,n=reg(0),reg(1),reg(2)
  assert n<0x400000
  assert not (freed[0] and source<=src<source+length),'source read after memblock_free'
  m.mem_write(dst,bytes(m.mem_read(src,n)))
  if source<=src<source+length:copies.append((dst,src,n))
  return dst
 def memset():
  dst,value,n=reg(0),reg(1),reg(2);assert n<0x800000
  m.mem_write(dst,bytes([value&255])*n);return dst
 def parse_string():
  name=cstr(reg(0));properties.append(name.decode());assert name==b'boot_fb0'
  return text if present else 0
 def number(bits):
  def fn():
   try:v=int(cstr(reg(0)),reg(1))
   except ValueError:return -22
   m.mem_write(reg(2),v.to_bytes(bits//8,'little'));return 0
  return fn
 def strchr():
  raw=cstr(reg(0));at=raw.find(bytes([reg(1)&255]));return 0 if at<0 else reg(0)+at
 def map_fb():
  assert reg(0)==info
  w64(info+0x2d0,destination);w64(info+0x110,0x4c000000)
  w64(fbi+0x1b0,ion);w64(ion+8,destination);w32(ion+0x10,length*2)
  m.mem_write(destination,bytes(length*2));return 0
 def cache_flush():flushes.append([reg(0),reg(1)]);return 0
 def layer():
  assert reg(0)==manager and reg(2)==1
  layer_callbacks.append(bytes(m.mem_read(reg(1),0xb8)).hex());return 0
 def map_source():
  pages=(length+(physical_address&4095)+4095)//4096
  assert reg(0)==array and reg(1)==pages and reg(2)==4,'unexpected vmap arguments'
  expected_page=0xffffffbf00000000+((physical_address-0x40000000)//4096)*64
  page_pointers=struct.unpack('<'+'Q'*pages,m.mem_read(array,pages*8))
  assert page_pointers==tuple(expected_page+i*64 for i in range(pages)),'vmap pages differ from owned source PA'
  mappings.append(dict(physical_address=physical_address,page_count=pages))
  return source-(physical_address&4095)
 def memblock_free():
  frees.append([reg(0),reg(1)]);freed[0]=True;return 0
 register(prop,'property',parse_string)
 for offset,name,fn in [(0xa8,'memset',memset),(0xb0,'strlen',lambda:len(cstr(reg(0)))),
   (0x110,'memcpy',memcpy),(0x12c,'strchr',strchr),(0x14c,'kstrtoull',number(64)),
   (0x174,'kstrtouint',number(32)),(0x3ac,'vmalloc',lambda:array),
   (0x41c,'vmap',map_source),(0x428,'vfree',lambda:0),(0x584,'vunmap',lambda:0)]:
  register(target(blob,cf+offset),name,fn)
 # Every printk call is a no-op; the rest of the copy routine executes unchanged.
 printk=target(blob,cf+0x6c)
 register(printk,'printk',lambda:0)
 for offset,name,fn in [(0x60,'screens',lambda:1),(0x68,'printlevel',lambda:0),
   (0x194,'mapfb',map_fb),(0x28c,'physicalwidth',lambda:100),(0x298,'physicalheight',lambda:70),
   (0x2ac,'memset',memset),(0x698,'var_to_disp_fb',lambda:0),(0x6ec,'flush',cache_flush)]:
  register(target(blob,rq+offset),name,fn)
 reserve=target(blob,cf+0x5dc)
 register(target(blob,reserve-BASE+0x3c),'memblock_free',memblock_free)
 def memblock_reserve():reserves.append([reg(0),reg(1)]);return 0
 register(target(blob,reserve-BASE+0x34),'memblock_reserve',memblock_reserve)
 register(callback,'layer',layer)
 display_sizes=[]
 if warm:
  def display_size():
   assert reg(0)==0
   display_sizes.append([reg(0),width,height])
   w32(reg(1),width);w32(reg(2),height);return 0
  register(target(blob,rq+0x638),'display_size',display_size)
  w32(drv+0x4a8,1);w32(drv+0x4ac,0);w32(drv+0x4b0,1);w32(drv+0x4b4,0)
 # Reject MMIO accesses structurally: only kernel and modeled heap are mapped.
 for pc,(name,fn) in operations.items():m.hook_add(u.UC_HOOK_CODE,returns(fn),begin=pc,end=pc)
 m.reg_write(a.UC_ARM64_REG_SP,HEAP+0x1f00000)
 if legacy_reserve:
  # Locate the real __setup entry, rather than importing a lab kernel's PCs.
  virtual_base=0xffffff8008080000
  string=blob.index(b'disp_reserve\0')
  pointer=struct.pack('<Q',virtual_base+string)
  setup=blob.index(pointer);assert blob.find(pointer,setup+1)<0
  # The unrelocated Image stores setup pointers in R_AARCH64_RELATIVE addends.
  setup_address,relocation=struct.unpack_from('<QQ',blob,setup-16)
  assert relocation==0x403
  function_relocation=blob.index(struct.pack('<Q',setup_address+8))
  kind,function=struct.unpack_from('<QQ',blob,function_relocation+8)
  assert kind==0x403
  early=function-virtual_base
  assert blob[early:early+4]==bytes.fromhex('fd7bbca9')
  assert pair_address(blob,early+0x40,early+0x44)==fbi
  assert target(blob,early+0x6c)==reserve
  # Execute early_disp_reserve and its real get_options numeric parser. Only
  # the external memblock operation and printk are modeled at the boundary.
  reserve_text=text+0x100
  m.mem_write(reserve_text,f'{length},0x{physical_address:x}'.encode()+b'\0')
  m.reg_write(a.UC_ARM64_REG_X0,reserve_text)
  m.reg_write(a.UC_ARM64_REG_LR,RETURN)
  m.emu_start(BASE+early,RETURN,count=100000)
  assert m.reg_read(a.UC_ARM64_REG_PC)==RETURN and m.reg_read(a.UC_ARM64_REG_X0)==0
  assert struct.unpack('<II',m.mem_read(fbi+0x270,8))==(length,physical_address)
  assert reserves==[[physical_address,length]],reserves
 fbinit_prefix=bytes.fromhex('fd7bb9a900400091fd030091f55b02a9')
 fi=blob.find(fbinit_prefix)
 assert fi>=0 and blob.find(fbinit_prefix,fi+1)<0
 format_to_var=target(blob,fi+0x240)
 m.reg_write(a.UC_ARM64_REG_X0,0);m.reg_write(a.UC_ARM64_REG_X1,info+0x60)
 m.reg_write(a.UC_ARM64_REG_LR,RETURN)
 m.emu_start(format_to_var,RETURN,count=10000)
 assert m.reg_read(a.UC_ARM64_REG_PC)==RETURN
 format_fields={name:struct.unpack('<I',m.mem_read(info+offset,4))[0] for name,offset in
                [('bpp',0x78),('red',0x80),('green',0x8c),('blue',0x98),('alpha',0xa4)]}
 assert format_fields==dict(bpp=32,red=16,green=8,blue=0,alpha=24),format_fields
 m.reg_write(a.UC_ARM64_REG_X0,0);m.reg_write(a.UC_ARM64_REG_X1,args)
 m.reg_write(a.UC_ARM64_REG_LR,RETURN)
 m.emu_start(BASE+rq,RETURN,count=200000)
 assert m.reg_read(a.UC_ARM64_REG_PC)==RETURN
 assert m.reg_read(a.UC_ARM64_REG_X0)==0,(manifest['target'],hex(m.reg_read(a.UC_ARM64_REG_X0)),properties)
 assert struct.unpack('<I',m.mem_read(drv+0x4a8,4))[0]==int(warm),'handoff sync state changed'
 assert display_sizes==([[0,width,height]] if warm else [])
 assert properties==['boot_fb0'] and len(layer_callbacks)==1 and len(flushes)==1
 successful=present and source_bpp==32
 actual=bytes(m.mem_read(destination,length))
 expected=bytearray(length)
 crop_width,crop_height=crop[2]-crop[0],crop[3]-crop[1]
 if successful:
  for row in range(crop_height):
   src=((row+crop[1])*width+crop[0])*4
   dst=((row+(height-crop_height)//2)*width+(width-crop_width)//2)*4
   expected[dst:dst+crop_width*4]=pixels[src:src+crop_width*4]
 assert actual==bytes(expected)
 assert len(copies)==(crop_height if successful else 0)
 assert len(mappings)==(1 if successful else 0)
 expected_frees=[[physical_address,length]] if successful and legacy_reserve else []
 assert frees==expected_frees,frees
 return dict(target=manifest['target'],kernel_sha256=sha(blob),copy_entry=hex(BASE+cf),
             copy_body_sha256=sha(blob[cf:rq]),request_entry=hex(BASE+rq),
             request_body_sha256=sha(blob[rq:rq+0x7bc]),width=width,height=height,
             boot_disp_sync=int(warm),fb_base=0,boot_fb0=value[:-1].decode() if present else None,
             source_bpp=source_bpp,crop=list(crop),framebuffer_format_zero_fields=format_fields,pixels_copied_exact=successful,copy_rows=len(copies),
             layer_submitted=True,cache_flush_requested=True,memblock_free_calls=frees,
             legacy_disp_reserve_populated=legacy_reserve,no_source_reads_after_free=True,mmio_accesses=0,
             early_disp_reserve_executed=legacy_reserve,memblock_reserve_calls=reserves,
             source_pixels_sha256=sha(pixels),destination_pixels_sha256=sha(actual),source_mappings=mappings,
             warm_display_size_calls=display_sizes)



def display_properties(blob,warm=False):
 assert fdt.total_size(blob)<=len(blob),'truncated FDT'
 props={(p,n):v for p,n,_,v in fdt.walk(blob)}
 displays=[p for p,n in props if n=='boot_disp' and (p,'fb0_format') in props]
 assert len(displays)==1,'expected one display node'
 display=displays[0]
 lcds=[p for p,n in props if n=='lcd_x']
 assert len(lcds)==1,'expected one panel0 geometry node'
 lcd=lcds[0]
 def cell(path,name):
  value=props[path,name];assert len(value)==4,(path,name,'not one cell')
  return struct.unpack('>I',value)[0]
 assert cell(display,'boot_disp')==(0x100 if warm else 0),'unexpected boot_disp ownership'
 assert cell(display,'fb_base')==0,'fb_base must remain zero'
 assert cell(display,'fb0_format')==0,'expected vendor ARGB32 format zero'
 width=cell(display,'fb0_width') or cell(lcd,'lcd_x')
 height=cell(display,'fb0_height') or cell(lcd,'lcd_y')
 assert 0<width<=4096 and 0<height<=4096 and width*height*4<=0x400000
 return props,display,width,height


def reservations(blob):
 offset=struct.unpack_from('>I',blob,16)[0]
 limit=fdt.total_size(blob)
 result=[]
 while True:
  assert offset+16<=limit,'unterminated FDT reservation map'
  address,length=struct.unpack_from('>QQ',blob,offset);offset+=16
  if not address and not length:return result
  assert length and address+length<1<<64,'invalid FDT reservation'
  result.append([address,length])


def fnv(data):
 value=2166136261
 for byte in data:value=(value^byte)*16777619&0xffffffff
 return value


def validate_warm_status(directory,props,display,pixels):
 manifest=json.loads((directory/'manifest.json').read_text())
 contract_path=Path(__file__).with_name('display-handoff.json')
 contract=json.loads(contract_path.read_text());assert contract['version']==1
 policy=contract['models'][manifest['target']]
 assert policy['package_sha256']==manifest['package_sha256']
 assert policy['vendor_uboot_sha256']==manifest['panel_contract']['vendor_uboot_sha256']
 for name in ('boot_disp','boot_disp1','boot_disp2'):
  assert struct.unpack('>I',props[display,name])[0]==policy[name],('warm metadata differs',name)
 raw=props['/chosen','baseos,boot-display']
 assert len(raw)==80,'warm display status must be exactly 80 bytes'
 magic,version,size,digest,panel,logo_bytes,logo_fnv,pixel_fnv=struct.unpack_from('<8I',raw)
 shown,reserved0,reserved1=struct.unpack_from('<Q2I',raw,64)
 assert (magic,version,size)==(0x31505344,1,80) and panel in (0,1)
 assert digest==fnv(raw[:12]+bytes(4)+raw[16:]),'warm status checksum differs'
 assert raw[32:64]==bytes.fromhex(manifest['generation']),'warm status generation differs'
 assert 54<=logo_bytes<=0x400000 and shown and not(reserved0 or reserved1)
 assert manifest['panel_rule']==2 or panel==manifest['panel_rule'],'warm panel violates model policy'
 assert pixel_fnv==fnv(pixels),'warm status pixel hash differs'
 fixture=directory/'early-logo-source.bmp'
 if not fixture.is_file():fixture=ROOT/'work'/manifest['target']/'bootlogo.bmp'
 source=fixture.read_bytes()
 assert len(source)==logo_bytes and fnv(source)==logo_fnv,'warm status source BMP differs'
 timing=props['/chosen','baseos,native-timing'];assert len(timing)==56
 stamps=struct.unpack('>7Q',timing)
 # v151: the logo is lit while the kernel decodes, so the window ends at FDT completion (stamps[5]).
 assert stamps[2]<=shown<=stamps[5],'warm shown tick lies outside the loader interval between image load and FDT completion'
 command=props['/chosen','bootargs'].rstrip(b'\0').decode('ascii').split()
 assert [token for token in command if token.startswith('lcd_type=')]==['lcd_type='+('boe' if panel else 'old')]
 return dict(display_record_sha256=sha(raw),shown_ticks=shown,panel=panel,
             fixture_sha256=sha(source),fixture_path=str(fixture.resolve()),handoff_policy_sha256=sha(contract_path.read_bytes()),
             exact_warm_metadata=True,native_display_interval=[stamps[2],stamps[5]],panel_cmdline_exact=True)


def generated_case(directory,dtb_path,pixel_path,warm=False):
 blob=dtb_path.read_bytes();pixels=pixel_path.read_bytes()
 props,display,width,height=display_properties(blob,warm)
 raw=props[display,'boot_fb0']
 assert raw.endswith(b'\0') and b'\0' not in raw[:-1],'boot_fb0 must be one terminated string'
 fields=raw[:-1].decode('ascii').split(',')
 assert len(fields)==9 and all(re.fullmatch(r'(?:0[xX])?[0-9a-fA-F]+',v) for v in fields),'invalid nine-field hex tuple'
 numbers=[int(v,16) for v in fields]
 manifest=json.loads((directory/'manifest.json').read_text())
 ram=struct.unpack('>QQ',props['/memory@40000000','reg'])[1]
 physical_address=0x40000000+ram-manifest['boot_logo']['framebuffer_from_ram_end']
 expected=[physical_address,width,height,32,width*4]
 assert numbers[:5]==expected,('boot_fb0 does not describe the stock ARGB32 source',numbers,expected)
 left,top,right,bottom=numbers[5:]
 assert 0<=left<right<=width and 0<=top<bottom<=height,'invalid stock crop'
 assert len(pixels)==width*height*4,'pixel byte count differs from geometry'
 for y in range(height):
  for x in range(width):
   pixel=pixels[(y*width+x)*4:(y*width+x+1)*4]
   if left<=x<right and top<=y<bottom:assert pixel[3]==255,'artwork alpha must be opaque'
   else:assert pixel==bytes(4),'unused stock canvas must be zero'
 reserved=reservations(blob)
 assert not any(base<numbers[0]+len(pixels) and numbers[0]<base+length for base,length in reserved),'source must not be permanently FDT-reserved'
 commandlines=[v.rstrip(b'\0').decode('ascii') for (path,name),v in props.items() if name=='bootargs']
 assert len(commandlines)==1 and [v for v in commandlines[0].split() if v.startswith('disp_reserve=')]==[f'disp_reserve={len(pixels)},0x{physical_address:x}'],'stock disp_reserve differs'
 manifest=json.loads((directory/'manifest.json').read_text())
 generation=bytes.fromhex(manifest['generation']);assert len(generation)==32
 markers=[v for (path,name),v in props.items() if name=='baseos,native-boot']
 assert markers==[generation],'FDT native generation differs from manifest'
 if warm:warm_status=validate_warm_status(directory,props,display,pixels)
 else:assert ('/chosen','baseos,boot-display') not in props,'cold FDT carries warm display status'
 result=run_case(directory,width,height,pixels=pixels,property_value=raw,warm=warm,legacy_reserve=True,physical_address=physical_address,crop=tuple(numbers[5:]))
 result.update(input_mode='generated-fdt-and-pixels',fdt_path=str(dtb_path.resolve()),
               fdt_sha256=sha(blob),pixels_path=str(pixel_path.resolve()),
               manifest_sha256=sha((directory/'manifest.json').read_bytes()),generation=manifest['generation'],
               source_reservations=reserved,source_held_reserved_for_boot=False,source_freed_after_copy=True)
 result['handoff_mode']='warm' if warm else 'cold'
 if warm:result['warm_status']=warm_status
 return result


def prepared_cases(fleet):
 rows=[]
 directories=sorted(p for p in fleet.iterdir() if (p/'manifest.json').is_file())
 expected={'rg28xx','rg34xx','rg34xxsp','rg35xxh','rg35xxplus','rg35xxpro',
           'rg35xxsp','rg40xxh','rg40xxv','rgcubexx','rgsp'}
 assert {p.name for p in directories}==expected,'all-prepared requires the exact eleven target directories'
 for directory in directories:
  dtb_path=directory/'verified-kernel.dtb';dtb=dtb_path.read_bytes()
  _,_,width,height=display_properties(dtb,True)
  result=run_case(directory,width,height,legacy_reserve=True,warm=True)
  result.update(input_mode='synthetic-source-prepared-geometry',prepared_fdt_sha256=sha(dtb),
                manifest_sha256=sha((directory/'manifest.json').read_bytes()))
  rows.append(result)
 for present,bpp,legacy in [(False,32,False),(True,24,False),(True,32,True)]:
  rows.append(run_case(fleet/'rg34xxsp',720,480,present,bpp,legacy))
 rows.append(run_case(fleet/'rg34xxsp',720,480,crop=(10,20,110,70)))
 return rows


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('native',type=Path,nargs='?',help='native output directory with exact manifest.json and kernel.bin')
 parser.add_argument('--dtb',type=Path,help='actual final native FDT containing boot_fb0 and the reservation')
 parser.add_argument('--pixels',type=Path,help='actual generated opaque ARGB32 source bytes')
 parser.add_argument('--all-prepared',type=Path,help='eleven native output directories; uses synthetic source pixels')
 parser.add_argument('--output',type=Path,required=True,help='fresh JSON report; existing files are never replaced')
 parser.add_argument('--warm',action='store_true',help='require authenticated early-display FDT metadata and replay warm sync')
 args=parser.parse_args()
 if args.all_prepared:
  if args.native or args.dtb or args.pixels or args.warm:parser.error('--all-prepared cannot be combined with generated inputs or warm mode')
 elif not(args.native and args.dtb and args.pixels):parser.error('NATIVE, --dtb and --pixels are required together')
 assert not args.output.exists(),'refusing to replace existing evidence'
 report=dict(passed=False,no_device_access=True,hardware_qualified=False,
   verifier_sha256=sha(Path(__file__).read_bytes()),cases=[],errors=[],
   modeled_boundaries=['kernel OF property accessor returns the exact supplied FDT string',
     'RAM/vmap and ION allocation, generic memcpy/memset/string/number library helpers',
     'screen-count/physical-size getters and layer-submit/cache-flush callbacks; no display hardware emulated'],
   actual_code=['vendor format-zero to fb_var_screeninfo conversion',
     'display_fb_request including the selected cold/warm branch and layer setup',
     'Fb_copy_boot_fb parser, geometry/bpp checks, row/crop/stride arithmetic, copy loop',
     'disp_reserve_mem empty-state guard and populated-state memblock_free dispatch'],
   limitations=['does not prove FAT reads, BMP conversion, RAM cache publication or physical display output',
     'all-prepared mode injects synthetic pixels/property and proves only kernel behavior',
     'FDT-reserved source remains reserved for this boot; no disp_reserve cleanup claim'])
 try:
  report['cases']=prepared_cases(args.all_prepared) if args.all_prepared else [generated_case(args.native,args.dtb,args.pixels,args.warm)]
  report['passed']=True
 except Exception as error:
  report['errors'].append(f'{type(error).__name__}: {error}')
 report['seal']=sha(json.dumps(report,sort_keys=True,separators=(',',':')).encode())
 args.output.parent.mkdir(parents=True,exist_ok=True)
 with args.output.open('x') as stream:stream.write(json.dumps(report,indent=2)+'\n')
 print(json.dumps(dict(passed=report['passed'],cases=len(report['cases']),seal=report['seal'],errors=report['errors'])))
 return 0 if report['passed'] else 1

if __name__=='__main__':sys.exit(main())
