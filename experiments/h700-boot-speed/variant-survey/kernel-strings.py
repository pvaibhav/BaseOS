import struct, zlib, re, subprocess, os
ROOT = "work"
TARGETS=["rg28xx","rg34xx","rg34xxsp","rg35xxh","rg35xxplus","rg35xxpro","rg35xxsp","rg40xxh","rg40xxv","rgcubexx","rgsp"]
os.makedirs("work/variant-survey/kernels", exist_ok=True)
for t in TARGETS:
    b=open(f"{ROOT}/{t}/boot-gzip/boot.img","rb").read()
    ksz,=struct.unpack_from("<I",b,8); page,=struct.unpack_from("<I",b,36)
    k=b[page:page+ksz]
    img=zlib.decompress(k, 31)
    open(f"work/variant-survey/kernels/{t}.img","wb").write(img)
    s=subprocess.run(["strings","-n","4"],input=img,capture_output=True).stdout.decode("latin1").splitlines()
    hits=sorted({x for x in s if re.search(r"lcd_type|lcd_id|panel_id|board_id|hw_ver|lcd_driver_name|allen", x, re.I)})
    # candidate panel driver names: strings next to lcd_type occurrences
    idx=[i for i,x in enumerate(s) if x=="lcd_type" or "lcd_type" in x]
    ctx=[]
    for i in idx[:6]:
        ctx.append(s[max(0,i-6):i+7])
    print(f"== {t}: kernel {len(img)//1024} KiB; hits={hits}")
    for c in ctx: print("   ctx:", c)
