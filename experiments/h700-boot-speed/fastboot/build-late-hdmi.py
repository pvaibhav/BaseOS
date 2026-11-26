#!/usr/bin/env python3
"""Build the lab HDMI deferral module against the pinned vendor ABI."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import audio_module as audio

output = ROOT / "work/h700-loader/late-hdmi"
module = output / "module"
module.mkdir(parents=True, exist_ok=True)
kernel, modules, config, _ = audio.inputs("rg34xxsp")
_, _, exports = audio.vendor_contract(kernel, modules)
(output / "kernel.config").write_bytes(config)
(output / "Module.symvers").write_text("".join(
    f"0x{crc:08x}\t{name}\tvmlinux\tEXPORT_SYMBOL\n" for name, crc in sorted(exports.items())))
shutil.copyfile(HERE / "late-hdmi.c", module / "h700_late_hdmi.c")
(module / "Makefile").write_text("obj-m += h700_late_hdmi.o\n")
command = r'''
if ! command -v bc >/dev/null; then apt-get update -qq; apt-get install -y -qq bc; fi
echo '33887b40fc8e0b71f423bb7afe112a4ae190378145f4a3f3892c563b7e43131d  /cache/linux-4.9.170.tar.xz' | sha256sum -c -
tar -xJf /cache/linux-4.9.170.tar.xz -C /tmp
cd /tmp/linux-4.9.170
cp /out/kernel.config .config
export ARCH=arm64 CROSS_COMPILE=aarch64-nextui-linux-gnu-
make HOSTCFLAGS='-O2 -fcommon' olddefconfig modules_prepare >/out/prepare.log 2>&1
cp /out/Module.symvers Module.symvers
make HOSTCFLAGS='-O2 -fcommon' M=/out/module modules >/out/build.log 2>&1
aarch64-nextui-linux-gnu-strip --strip-debug /out/module/h700_late_hdmi.ko
'''
subprocess.run(["docker", "run", "--rm", "--platform", "linux/arm64", "--entrypoint", "/bin/sh",
                "-v", f"{output}:/out", "-v", f"{ROOT / 'work/audio-build'}:/cache:ro",
                "ghcr.io/loveretro/h700-toolchain@sha256:bf5d8f9f5442cad789aa77176bc059ec823caefa3258bf30780eee681c777119",
                "-euc", command], check=True)
blob = (module / "h700_late_hdmi.ko").read_bytes()
imports = audio.validate_module(blob, kernel, modules)
(output / "h700_late_hdmi.ko").write_bytes(blob)
report = {"target": "rg34xxsp", "kernel_sha256": audio.digest(kernel), "module_sha256": audio.digest(blob),
          "source_sha256": audio.digest((HERE / "late-hdmi.c").read_bytes()), "validated_imports": imports}
(output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
