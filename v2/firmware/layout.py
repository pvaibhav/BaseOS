"""Framebuffer layout from the checked stock allocation policy."""
def layout_header(inputs):
 p=inputs['model_policy']['boot_logo']
 assert p['width'] > 0 and p['height'] > 0
 span=p['width']*p['height']*4
 assert span <= 0x400000
 for ram in p['proven_ram_bytes']:
  address=0x40000000+ram-p['framebuffer_from_ram_end']
  low=address & ~0xffff; high=(address+span+0xffff) & ~0xffff
  assert 0x40040000 <= low < high <= 0x40000000+ram
  assert address+0x400000 <= 0x40000000+ram
  for start,end in p['nonoverlap_arenas'].values():
   assert start < end and (high <= start or low >= end)
 ranges=p['nonoverlap_arenas']
 return '#include "simplefb.h"\nstatic const struct baseos_u3_range u3_arenas[]={\n'+''.join('{%duLL,%duLL},\n'%tuple(v) for v in ranges.values())+'};\nstatic const struct baseos_u3_fb_layout u3_layout={%du,%du,%du,%du,u3_arenas,sizeof(u3_arenas)/sizeof(u3_arenas[0])};\n'%(p['width'],p['height'],p['width']*4,p['framebuffer_from_ram_end'])
