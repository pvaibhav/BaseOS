import sys, struct
sys.path.insert(0,'/Users/pvaibhav/Code/Me/BaseOS/work/uboot-trace/experiments/h700-boot-speed')
import fdt
B=open('/Users/pvaibhav/Code/Me/BaseOS/work/uboot-trace/work/rgsp/boot-gzip/boot-package.bin','rb').read()
# find all FDT blobs
i=0
offs=[]
while True:
    i=B.find(b'\xd0\x0d\xfe\xed',i)
    if i<0: break
    try:
        n=fdt.total_size(B[i:]); offs.append((i,n))
    except Exception: pass
    i+=4
print('FDT blobs:',[(hex(a),hex(n)) for a,n in offs])
names=('pmu_safe_vol','pmu_safe_ratio','ntc_status','ntc_cur','battery_exist','charge_mode','bat_bmp_type','safe_temp_H','safe_temp_L','pmu_bat_temp_para','power_delay')
for a,n in offs:
    blob=B[a:a+n]
    print('== blob',hex(a))
    try:
        for path,name,off,val in fdt.walk(blob):
            if any(name.startswith(x) for x in names) or path.endswith(('charger0','power_sply')) or 'fragment' in path and ('charger' in path or 'power' in path):
                v=struct.unpack('>%di'%(len(val)//4),val[:len(val)//4*4]) if len(val)%4==0 and len(val) else val
                print(f'  {path} {name} = {v}')
    except Exception as e: print('  walk error',e)
