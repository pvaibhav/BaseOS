#!/usr/bin/env python3
"""Emulate selection of each bank by index, including banks sharing a DRAM type."""
import importlib.util
import json
from pathlib import Path
import re
import struct
import sys
import unicorn as u
from unicorn import arm64_const as a
R=Path('/repo')
spec=importlib.util.spec_from_file_location('selector',R/'experiments/h700-boot-speed/spl/verify-dram-select.py')
selector=importlib.util.module_from_spec(spec);spec.loader.exec_module(selector)
d=Path(sys.argv[1]);blob=(d/'spl.bin').read_bytes()
symbols={n:int(v,16) for v,n in re.findall(r'^([0-9a-f]+) [a-zA-Z] (\w+)$',(d/'symbols.txt').read_text(),re.M)}
text=(d/'u-boot-2026.10/arch/arm/mach-sunxi/dram-candidates.h').read_text().split('dram_vendor_candidates[][32] = {')[1]
banks=[[int(x,16) for x in re.findall(r'0x([0-9a-f]+)u',row)] for row in re.findall(r'\{([^}]+)\}',text)]
reports=[]
for selected,bank in enumerate(banks):
 for timeout in (False,True):
    m=u.Uc(u.UC_ARCH_ARM64,u.UC_MODE_ARM)
    for address,size in ((0x20000,0x38000),(0x03000000,0x1000),(0x07010000,0x1000),(0x047fb000,0x2000)):m.mem_map(address,size)
    m.mem_write(0x20000,blob);m.reg_write(a.UC_ARM64_REG_SP,0x52a00);m.reg_write(a.UC_ARM64_REG_X30,0x55ff0)
    m.mem_write(symbols['baseos_dram_power'],struct.pack('<I',1))
    dram=selector.Dram(m)
    for size in (512<<20,1<<30,2<<30):dram.page(0x40000000);dram.page(0x40000000+size//2)
    attempts=[];volts=[];outcome=[]
    def ret(value):m.reg_write(a.UC_ARM64_REG_X0,value);m.reg_write(a.UC_ARM64_REG_PC,m.reg_read(a.UC_ARM64_REG_X30))
    def hook(mc,pc,size,ctx):
        if pc==symbols['mctl_core_init']:
            i=(mc.reg_read(a.UC_ARM64_REG_X0)-symbols['dram_candidates'])//48
            attempts.append(i)
            if timeout and i!=selected:mc.reg_write(a.UC_ARM64_REG_PC,symbols['dram_panic'])
            else:ret(int(i==selected))
        elif pc==symbols['mctl_auto_detect_dram_size']:mc.mem_write(mc.reg_read(a.UC_ARM64_REG_X1),bytes((10,15,1,1)));ret(0)
        elif pc==symbols['mctl_calc_size']:ret(0x40000000)
        elif pc==symbols['axp_set_dcdc3']:volts.append(mc.reg_read(a.UC_ARM64_REG_X0));ret(0)
        elif pc in (symbols['baseos_set_master_priority'],symbols['udelay']):ret(0)
        elif pc in (symbols['panic'],0x55ff0):outcome.append(pc);mc.emu_stop()
    m.hook_add(u.UC_HOOK_CODE,hook);m.emu_start(symbols['sunxi_dram_init'],0x58000,count=200000)
    assert outcome==[0x55ff0] and m.reg_read(a.UC_ARM64_REG_X0)==0x40000000
    assert set(attempts)==set(range(selected+1))
    words=struct.unpack('<32I',m.mem_read(symbols['trained_vendor_params'],128))
    assert words[:6]==tuple(bank[:6]) and words[8:]==tuple(bank[8:])
    assert volts==[{7:1200,8:1100}[b[1]] for b in banks[:selected+1]]
    reports.append(dict(bank=selected,type=bank[1],timeout_exit=timeout,voltage_requests_mv=volts))
print(json.dumps(dict(emulated=True,physical_training=False,banks=reports),indent=2))
