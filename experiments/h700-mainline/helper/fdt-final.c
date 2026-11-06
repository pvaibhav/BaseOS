#include <libfdt.h>
#include <string.h>
#include "helper.h"
static int reserve(void *f, uint64_t a, uint64_t n)
{
 for (int i=0;i<fdt_num_mem_rsv(f);i++) {
  uint64_t b,s;
  if (fdt_get_mem_rsv(f,i,&b,&s)) return -1;
  if (b<=a && s>=n && a-b<=s-n) return 0;
 }
 return fdt_add_mem_rsv(f,a,n);
}
static int policy(const void *f)
{
 int n=-1,sec=0,psci=0;
 if (fdt_address_cells(f,0)!=2 || fdt_size_cells(f,0)!=2) return -1;
 while ((n=fdt_next_node(f,n,0))>=0) {
  int len; const char *c=fdt_getprop(f,n,"compatible",&len);
  if (c) {
   int at=0;
   while (at<len) {
    int l=strnlen(c+at,len-at);
    if (l==len-at || (l>=9 && !memcmp(c+at,"x-powers,",9))) return -1;
    at+=l+1;
   }
  }
  if (!fdt_node_check_compatible(f,n,"arm,psci-1.0") ||
      !fdt_node_check_compatible(f,n,"arm,psci-0.2")) {
   const char *method=fdt_getprop(f,n,"method",&len);
   if (!method || len!=4 || memcmp(method,"smc",4)) return -1;
   psci=1;
  }
  int parent=fdt_parent_offset(f,n);
  if (parent>=0 && parent==fdt_path_offset(f,"/reserved-memory")) {
   const fdt64_t *reg=fdt_getprop(f,n,"reg",&len);
   if (reg && len==16 && fdt_getprop(f,n,"no-map",0) &&
       fdt_address_cells(f,parent)==2 && fdt_size_cells(f,parent)==2) {
    uint64_t a=fdt64_ld(reg), s=fdt64_ld(reg+1);
    if (a<=0x40000000 && s>=0x40040000ull-a) sec=1;
   }
  }
 }
 return n!=-FDT_ERR_NOTFOUND || !sec || !psci ? -1:0;
}
int baseos_u2_fdt(void *dst,const void *src,uint32_t bytes,
 const struct baseos_native_record *r,uint32_t initrd,const struct baseos_u2_metadata *m)
{
 if (bytes>0x100000-4096 || fdt_check_full(src,bytes) || policy(src) ||
     fdt_open_into(src,dst,0x100000)) return -1;
 int ch=fdt_path_offset(dst,"/chosen"), mem=fdt_path_offset(dst,"/memory@40000000"),len;
 if (ch<0 || mem<0 || !fdt_getprop(dst,ch,"baseos,trial",0)) return -1;
 const char *args=fdt_getprop(dst,ch,"bootargs",&len);
 char cmd[2048];
 if (!args || len<1 || len>sizeof(cmd) || strnlen(args,len)!=(size_t)len-1) return -1;
 memcpy(cmd,args,len); unsigned used=len-1;
 static const char *reasons[]={"button","irq","usb","charger","battery","unknow"};
 const char *keys[]={" androidboot.serialno="," lcd_type="," bootreason="};
 const char *values[]={m->serial,m->panel?"boe":"old",reasons[r->boot_reason]};
 const char *props[]={"baseos,serial","baseos,panel","baseos,bootreason"};
 for (unsigned i=0;i<3;i++) {
  unsigned k=strlen(keys[i]),v=strlen(values[i]);
  if (k+v>=sizeof(cmd)-used || fdt_setprop_string(dst,ch,props[i],values[i])) return -1;
  memcpy(cmd+used,keys[i],k); used+=k; memcpy(cmd+used,values[i],v); used+=v;cmd[used]=0;
 }
 fdt64_t reg[]={cpu_to_fdt64(0x40000000),cpu_to_fdt64(r->ram_bytes)},stamps[7];
 fdt32_t words[32];
 for(unsigned i=0;i<7;i++) stamps[i]=cpu_to_fdt64(m->times[i]);
 for(unsigned i=0;i<32;i++) words[i]=cpu_to_fdt32(r->dram_words[i]);
 if(fdt_setprop_string(dst,ch,"bootargs",cmd) ||
 fdt_setprop_u64(dst,ch,"linux,initrd-start",0x42000000) ||
 fdt_setprop_u64(dst,ch,"linux,initrd-end",0x42000000+initrd) ||
 fdt_setprop(dst,ch,"baseos,native-boot",r->generation,32) ||
 fdt_setprop(dst,ch,"baseos,native-timing",stamps,sizeof(stamps)) ||
 fdt_setprop(dst,ch,"baseos,boot-display",m->display,m->display_bytes) ||
 fdt_setprop(dst,ch,"baseos,dram",words,sizeof(words))) return -1;
 mem=fdt_path_offset(dst,"/memory@40000000");
 if(fdt_setprop(dst,mem,"reg",reg,sizeof(reg)) || reserve(dst,0x42000000,initrd) ||
 reserve(dst,0x4a300000,4096) || reserve(dst,0x4a200000,0x100000) ||
 reserve(dst,m->logo_address,0x400000) || fdt_pack(dst) || fdt_totalsize(dst)>0x100000-4096) return -1;
 return 0;
}
