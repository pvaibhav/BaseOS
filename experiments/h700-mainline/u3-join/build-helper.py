"""Reuse the immutable U2 builder, then relink its objects with one FDT wrapper."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct
import importlib.util
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
BASE=load('u3_base_helper',HERE.parent/'helper/build.py')
OUTPUT_PREFIX='u3-'
LINK=['--wrap=baseos_u2_fdt']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def describe_inputs(target,inventory,prepared):
 inputs=BASE.describe_inputs(target,inventory,prepared)
 paths=sorted(HERE.glob('*.py'))+sorted(HERE.glob('*.c'))+[HERE.parent/'u3/simplefb.c',HERE.parent/'u3/simplefb.h',HERE.parent/'build-u2.py',ROOT/'tools/mainline_payload.py',ROOT/'tests/test-u3-helper.py',HERE.parent/'verify-u2-helper.py']
 inputs['u3_join']={'sources':{str(p.relative_to(ROOT)):sha(p) for p in sorted(paths) if p.is_file()},'link_flags':LINK,'layout':'trusted model boot_logo policy; 64KiB no-map; final pack; abort on any error'}
 return inputs

def layout_header(inputs):
 p=inputs['model_policy']['boot_logo']
 assert p['framebuffer_from_ram_end']==68012032
 ranges=p['nonoverlap_arenas']
 return '#include "simplefb.h"\nstatic const struct baseos_u3_range u3_arenas[]={\n'+''.join('{%duLL,%duLL},\n'%tuple(v) for v in ranges.values())+'};\nstatic const struct baseos_u3_fb_layout u3_layout={%du,%du,%du,%du,u3_arenas,sizeof(u3_arenas)/sizeof(u3_arenas[0])};\n'%(p['width'],p['height'],p['width']*4,p['framebuffer_from_ram_end'])
def build(target,inventory,prepared,generation,output):
 inputs=describe_inputs(target,inventory,prepared)
 report=BASE.build(target,inventory,prepared,generation,output)
 (output/'u3-layout.h').write_text(layout_header(inputs))
 clang,lld=BASE.toolchain();env=dict(os.environ)
 for parent in lld.parents:
  if (parent/'lib/libLLVM.dylib').is_file():env['DYLD_LIBRARY_PATH']=str(parent/'lib');break
 includes=[output,output/'include',output/'libfdt',HERE.parent/'helper',HERE.parent/'u3',BASE.NATIVE,BASE.SPL,HERE.parent/'include']
 objects=sorted(output.glob('[0-9]*.o'),key=lambda p:int(p.stem))
 for name,src in [('u3-wrap',HERE/'wrap.c'),('u3-simplefb',HERE.parent/'u3/simplefb.c')]:
  obj=output/(name+'.o');subprocess.run([str(clang),*BASE.FLAGS,*[f'-I{p}' for p in includes],'-c',str(src),'-o',str(obj)],check=True);objects.append(obj)
 for name,extra in [('helper.elf',[]),('helper.bin',['--oformat=binary'])]:
  subprocess.run([str(lld),'--gc-sections','-T',str(BASE.HERE/'link.ld'),*LINK,*extra,*map(str,objects),'-o',str(output/name)],check=True,env=env)
 elf=(output/'helper.elf').read_bytes()
 phoff=struct.unpack_from('<Q',elf,32)[0];stride,count=struct.unpack_from('<HH',elf,54)
 segments=[]
 for at in range(phoff,phoff+stride*count,stride):
  kind,flags,offset,va,pa,filesz,memsz,align=struct.unpack_from('<II6Q',elf,at)
  if kind==1:
   assert 0x4a000000<=pa<=pa+memsz<=0x4a0e0000
   segments.append(dict(address=pa,file_bytes=filesz,memory_bytes=memsz,flags=flags))
 assert segments
 report.update(elf_segments=segments,elf_memory_end=max(s['address']+s['memory_bytes'] for s in segments))
 report.update(inputs=inputs,size=(output/'helper.bin').stat().st_size,hashes={n:sha(output/n) for n in ('helper.bin','helper.elf')})
 (output/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
 return report
