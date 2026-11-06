#include <string.h>
#include <libfdt.h>
#include "helper.h"
#include "native-generated.h"
#include "native-checks.h"
#include "native-logo.h"
#include "native-display.h"
#include "lz4-stream.h"
extern int baseos_early_display_start(const struct baseos_native_logo *);
extern int baseos_early_display_poll(void), baseos_early_display_finish(void);
extern uint64_t native_ticks(void);
int baseos_u2_record(const struct baseos_native_record *r)
{
 return r->magic==BASEOS_NATIVE_MAGIC && r->version==2 && r->bytes==sizeof(*r) &&
 r->model==BASEOS_MODEL_ID && r->record_fnv==record_hash(r) &&
 !memcmp(r->generation,baseos_generation,32) &&
 (r->ram_bytes==0x40000000ull || r->ram_bytes==0x80000000ull) &&
 r->ram_bytes==(uint64_t)(r->dram_words[7]>>16)<<20 && r->dram_type==r->dram_words[1] &&
 r->image_bytes>=1024 && r->image_bytes<=BASEOS_NATIVE_IMAGE_CAPACITY &&
 baseos_boot_reason_valid(r->boot_reason,r->boot_reason_source);
}
int baseos_u2_prepare(const struct baseos_u2_arenas *a,const struct baseos_native_record *r,
 struct baseos_u2_metadata *m)
{
 if(!baseos_u2_record(r)) return 1;
 const struct baseos_mainline_header *p=(const void *)a->image;
 if(!baseos_payload_header(p,baseos_generation) || p->total!=r->image_bytes) return 2;
 const unsigned char *im=a->image+p->android_offset;
 uint32_t size=p->android_bytes;
 if(memcmp(im,"ANDROID!",8) || little32(im+36)!=2048 || little32(im+40)!=2 ||
 little32(im+1644)!=1660 || little32(im+24) || little32(im+1632) || little32(im+1636) || little32(im+1640)) return 3;
 uint32_t kb=little32(im+8),ib=little32(im+16),db=little32(im+1648),off=2048,k,i,d;
 if(!kb || !ib || ib>0xe00000 || !db || db>0x100000 ||
 checked_extent(&off,kb,size,&k) || checked_extent(&off,ib,size,&i) ||
 checked_extent(&off,db,size,&d) || off!=size || fdt_check_full(im+d,db)) return 3;
 /* Empty Android DTB only: no properties, children or reservations. */
 if(fdt_totalsize(im+d)!=db || fdt_num_mem_rsv(im+d) ||
 fdt_first_property_offset(im+d,0)!=-FDT_ERR_NOTFOUND ||
 fdt_first_subnode(im+d,0)!=-FDT_ERR_NOTFOUND) return 3;
#ifndef BASEOS_U2_HOST
 if(baseos_early_display_start((void *)BASEOS_NATIVE_LOGO_RECORD_ADDRESS)<0) return 4;
#endif
 m->times[0]=r->started_ticks; m->times[1]=r->read_started_ticks;m->times[2]=r->loaded_ticks;
 m->times[3]=native_ticks();
 struct baseos_lz4 lz;
 if(baseos_lz4_begin(&lz,a->kernel,0x1e00000,im+k,kb)) return 5;
 int step;
 do { step=baseos_lz4_step(&lz);
#ifndef BASEOS_U2_HOST
 if(baseos_early_display_poll()<0) return 4;
#endif
 } while(step==1);
 if(step) return 5;
 uint32_t output=lz.limit-lz.dst;
 m->times[4]=native_ticks();
 const unsigned char *kh=a->kernel;
 if(output<64 || little32(kh+56)!=0x644d5241 || little32(kh+8) || little32(kh+12) ||
 !little32(kh+16) || little32(kh+20) || little32(kh+16)<output ||
 little32(kh+16)>0x1e00000 || (little32(kh+24)&~0xeu) ||
 !(little32(kh+24)&8) || little32(kh+28)) return 6;
 memcpy(a->initrd,im+i,ib);
#ifndef BASEOS_U2_HOST
 if(baseos_early_display_finish()<0) return 4;
 const struct baseos_native_display *display=(void *)BASEOS_NATIVE_DISPLAY_ADDRESS;
 if(baseos_display_valid(display,baseos_generation)!=1) return 4;
 m->panel=display->panel;m->display=display;m->display_bytes=sizeof(*display);
#endif
 m->times[5]=native_ticks();m->times[6]=native_ticks();
 if(baseos_u2_fdt(a->fdt,a->image+p->dtb_offset,p->dtb_bytes,r,ib,m)) return 7;
 struct baseos_bl31_handoff *h=a->handoff;
 memset(h,0,sizeof(*h)); memset(a->t4,0,512);
 h->magic=BASEOS_HANDOFF_MAGIC; h->version=1;h->bytes=sizeof(*h);
 h->fdt=BASEOS_HANDOFF_FDT;h->fdt_capacity=BASEOS_HANDOFF_FDT_CAPACITY;
 h->flags=1;h->ns_entry=BASEOS_HANDOFF_KERNEL;h->bl31_base=0x40000000;h->bl31_limit=0x40040000;
 h->ram_bytes=r->ram_bytes;h->dram_type=r->dram_type;h->dram_flags=1;
 memcpy(h->dram_words,r->dram_words,sizeof(h->dram_words));memcpy(h->generation,r->generation,32);
 h->fnv=baseos_handoff_fnv(h);
 return baseos_handoff_valid(h,0x40000000,0x40040000)?0:8;
}
#ifndef BASEOS_U2_HOST
extern void helper_mmu_on(void),helper_exit(void),helper_fault(void);
/* The display module uses the selected trained RAM size via native-logo.h. */
void helper_main(const struct baseos_native_record *incoming)
{
 struct baseos_native_record r;
 if((uintptr_t)incoming!=BASEOS_NATIVE_RECORD_ADDRESS) helper_fault();
 memcpy(&r,incoming,sizeof(r));
 if(!baseos_u2_record(&r)) helper_fault();
 volatile uint64_t *table=(void *)0x4a0f8000;
 for(unsigned j=0;j<512;j++) table[j]=0;
 table[0]=(1ull<<54)|0x401;table[1]=0x40000705;
 if(r.ram_bytes==0x80000000ull) table[2]=0x80000705;
 helper_mmu_on();
 struct baseos_u2_metadata m={0};
 volatile uint32_t *sid=(void *)0x03006000;
 uint32_t words[]={sid[0x204/4]>>20,sid[0x208/4],sid[0x20c/4]};
 unsigned pos=0;
 for(unsigned j=0;j<3;j++) for(unsigned d=(j?8:3);d;d--) m.serial[pos++]="0123456789abcdef"[(words[j]>>((d-1)*4))&15];
 m.logo_address=BASEOS_NATIVE_LOGO_PIXEL_ADDRESS;
 const struct baseos_u2_arenas a={(void *)0x45000000,(void *)0x40200000,(void *)0x42000000,
 (void *)0x4a200000,(void *)0x4a300400,(void *)0x4a300800};
 if(baseos_u2_prepare(&a,&r,&m)) helper_fault();
 helper_exit();
}
#endif
#ifndef BASEOS_U2_HOST
void release_display_sram(void)
{
 volatile uint32_t *system=(void *)0x03000000;
 system[0]&=1u<<31;system[1]&=~(1u<<24);
 asm volatile("dsb sy" ::: "memory");
}
#endif
