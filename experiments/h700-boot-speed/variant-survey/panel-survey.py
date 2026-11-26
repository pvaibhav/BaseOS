import sys, struct, re, subprocess
sys.path.insert(0, "experiments/h700-boot-speed"); import fdt
ROOT = "work"
TARGETS = ["rg28xx","rg34xx","rg34xxsp","rg35xxh","rg35xxplus","rg35xxpro","rg35xxsp","rg40xxh","rg40xxv","rgcubexx","rgsp"]

def dtb(p, off):
    tot = fdt.total_size(p[off:off+8]); return p[off:off+tot]

for t in TARGETS:
    p = open(f"{ROOT}/{t}/boot-gzip/boot-package.bin", "rb").read()
    d = dtb(p, 0x11b800); o = dtb(p, 0x11ac00)
    props = {}
    for path, name, off, val in fdt.walk(d):
        if path.endswith("lcd0@01c0c000") and name in ("lcd_driver_name", "lcd_x", "lcd_y", "lcd_if", "lcd_model_name"):
            props[name] = val.rstrip(b"\0")
    ov = [(path, name, val[:24]) for path, name, off, val in fdt.walk(o) if name not in ("phandle", "linux,phandle")]
    us = subprocess.run(["strings", "-n", "3"], input=p, capture_output=True).stdout.decode("latin1").splitlines()
    names = sorted({x for x in us if re.fullmatch(r"(boe|BOE|HSD|hsd|JDI|jdi|INX|inx|TM|tianma|sharp|ips|IPS|auo|AUO|v1|v2|lcd_type_[0-9a-z]+)", x)})
    ks = open(f"work/variant-survey/kernels/{t}.img", "rb").read()
    kdrv = sorted(set(m.decode() for m in re.findall(rb"rg[0-9a-z]+_v[0-9]+[a-z0-9_]*", ks)))
    x = struct.unpack(">I", props["lcd_x"])[0] if "lcd_x" in props else "?"
    y = struct.unpack(">I", props["lcd_y"])[0] if "lcd_y" in props else "?"
    print(f"{t}: dt lcd_driver_name={props.get('lcd_driver_name')} {x}x{y} uboot_names={names} kernel_panel_drivers={kdrv}")
    if t == "rgsp":
        print("   overlay (rgsp):", ov)
