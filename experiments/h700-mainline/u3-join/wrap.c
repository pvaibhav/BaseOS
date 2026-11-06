#include <libfdt.h>
#include "simplefb.h"
#include "u3-layout.h"
extern int __real_baseos_u2_fdt(void *,const void *,uint32_t,
 const struct baseos_native_record *,uint32_t,const struct baseos_u2_metadata *);
int __wrap_baseos_u2_fdt(void *dst,const void *src,uint32_t bytes,
 const struct baseos_native_record *r,uint32_t initrd,const struct baseos_u2_metadata *m)
{
 int rc=__real_baseos_u2_fdt(dst,src,bytes,r,initrd,m);
 if(rc) return rc;
 rc=baseos_u3_simplefb(dst,0x100000,r,m,&u3_layout);
 if(rc) return rc;
 rc=fdt_pack(dst);
 if(rc) return rc;
 return fdt_check_full(dst,fdt_totalsize(dst)) ||
        fdt_totalsize(dst)>0x100000-4096 ? -FDT_ERR_BADVALUE:0;
}
