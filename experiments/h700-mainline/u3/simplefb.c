#include <libfdt.h>
#include <string.h>
#include "simplefb.h"
#include "native-display.h"

static int string_is(const void *f, int n, const char *key, const char *value)
{
 int len; const char *p=fdt_getprop(f,n,key,&len);
 return p && len==(int)strlen(value)+1 && !memcmp(p,value,len);
}
static int overlaps(uint64_t a,uint64_t end,uint64_t b,uint64_t size)
{
 return size && (size>UINT64_MAX-b || (a<b+size && b<end));
}
static int word_is(const void *f,int n,const char *key,uint32_t value)
{
 int len; const fdt32_t *p=fdt_getprop(f,n,key,&len);
 return p && len==4 && fdt32_ld(p)==value;
}
/* Validated 1/2GiB RAM puts both addresses below 4GiB. No printf runtime. */
static void framebuffer_name(char name[21],uint64_t address)
{
 memcpy(name,"framebuffer@",12);
 for(unsigned i=0;i<8;i++) name[12+i]="0123456789abcdef"[(address>>(28-i*4))&15];
 name[20]=0;
}
int baseos_u3_simplefb(void *f,size_t capacity,
 const struct baseos_native_record *r,const struct baseos_u2_metadata *m,
 const struct baseos_u3_fb_layout *l)
{
 if (!f || !r || !m || !l || capacity>0x100000 || capacity<4096 ||
     fdt_check_full(f,capacity) || fdt_totalsize(f)>capacity-4096 ||
     (r->ram_bytes!=0x40000000ull && r->ram_bytes!=0x80000000ull) ||
     !m->display || m->display_bytes!=sizeof(struct baseos_native_display) ||
     baseos_display_valid(m->display,r->generation)!=1 ||
     !l->width || !l->height || l->width>UINT32_MAX/4 ||
     l->stride!=l->width*4 || !l->from_ram_end ||
     (l->arena_count && !l->arenas)) return -FDT_ERR_BADVALUE;
 uint64_t span=(uint64_t)l->stride*l->height;
 uint64_t address=m->logo_address, end=address+span;
 uint64_t ram_end=0x40000000ull+r->ram_bytes;
 /* 64KiB covers all supported arm64 Linux page sizes, including unaligned pixels. */
 uint64_t low=address&~0xffffull, high=(end+0xffff)&~0xffffull;
 if (!span || span>0x400000 || l->from_ram_end>r->ram_bytes ||
     address!=baseos_logo_address(r->ram_bytes,l->from_ram_end) ||
     address<0x40040000 || end<address || high<end || high>ram_end ||
     address+0x400000>ram_end || low<0x40040000) return -FDT_ERR_BADVALUE;
 for (size_t i=0;i<l->arena_count;i++)
  if(l->arenas[i].end<=l->arenas[i].start ||
     overlaps(low,high,l->arenas[i].start,l->arenas[i].end-l->arenas[i].start))
   return -FDT_ERR_BADVALUE;
 int fb=fdt_path_offset(f,"/chosen/framebuffer"),ch=fdt_path_offset(f,"/chosen");
 int rm=fdt_path_offset(f,"/reserved-memory"),len;
 if(fb<0 || rm<0 || fdt_address_cells(f,0)!=2 || fdt_size_cells(f,0)!=2 ||
    fdt_address_cells(f,ch)!=2 || fdt_size_cells(f,ch)!=2 ||
    !fdt_getprop(f,ch,"ranges",&len) || len ||
    fdt_address_cells(f,rm)!=2 || fdt_size_cells(f,rm)!=2 ||
    !fdt_getprop(f,rm,"ranges",&len) || len ||
    !string_is(f,fb,"compatible","simple-framebuffer") ||
    !string_is(f,fb,"status","disabled") ||
    !string_is(f,fb,"format","x8r8g8b8") ||
    !word_is(f,fb,"width",l->width) || !word_is(f,fb,"height",l->height) ||
    !word_is(f,fb,"stride",l->stride)) return -FDT_ERR_BADVALUE;
 const fdt64_t *template_reg=fdt_getprop(f,fb,"reg",&len);
 if(template_reg && (len!=16 || fdt64_ld(template_reg) || fdt64_ld(template_reg+1)))
  return -FDT_ERR_BADVALUE;
 if(!template_reg && len!=-FDT_ERR_NOTFOUND) return len;
 /* The existing loader logo reservation is the sole permitted overlapping memreserve. */
 for(int i=0;i<fdt_num_mem_rsv(f);i++) {
  uint64_t a,s;
  if(fdt_get_mem_rsv(f,i,&a,&s) ||
     (overlaps(low,high,a,s) && !(a==address && s==0x400000)))
   return -FDT_ERR_BADVALUE;
 }
 int node;
 fdt_for_each_subnode(node,f,rm) {
  const fdt64_t *reg=fdt_getprop(f,node,"reg",&len);
  if(!reg || len<=0 || len%16) return -FDT_ERR_BADVALUE;
  for(int i=0;i<len/8;i+=2)
   if(overlaps(low,high,fdt64_ld(reg+i),fdt64_ld(reg+i+1))) return -FDT_ERR_BADVALUE;
 }
 if(node!=-FDT_ERR_NOTFOUND) return node;
 if(fdt_subnode_offset(f,rm,"framebuffer")!=-FDT_ERR_NOTFOUND)
  return -FDT_ERR_BADVALUE;
 const fdt64_t *memory=fdt_getprop(f,fdt_path_offset(f,"/memory@40000000"),"reg",&len);
 if(!memory || len!=16 || fdt64_ld(memory)!=0x40000000 ||
    fdt64_ld(memory+1)!=r->ram_bytes) return -FDT_ERR_BADVALUE;
 /* From here failures leave a partial tree; caller must discard it. Status is last. */
 int rc=fdt_open_into(f,f,(int)(capacity-4096));
 if(rc) return rc;
 char reserved_name[21],pixel_name[21];
 framebuffer_name(reserved_name,low);
 framebuffer_name(pixel_name,address);
 rm=fdt_path_offset(f,"/reserved-memory");
 node=fdt_add_subnode(f,rm,reserved_name);
 if(node<0) return node;
 fdt64_t reserved[]={cpu_to_fdt64(low),cpu_to_fdt64(high-low)};
 if((rc=fdt_setprop(f,node,"reg",reserved,sizeof(reserved))) ||
    (rc=fdt_setprop(f,node,"no-map",NULL,0))) return rc;
 fb=fdt_path_offset(f,"/chosen/framebuffer");
 fdt64_t pixels[]={cpu_to_fdt64(address),cpu_to_fdt64(span)};
 if((rc=fdt_setprop(f,fb,"reg",pixels,sizeof(pixels))) ||
    (rc=fdt_set_name(f,fb,pixel_name))) return rc;
 return fdt_setprop_string(f,fb,"status","okay");
}
