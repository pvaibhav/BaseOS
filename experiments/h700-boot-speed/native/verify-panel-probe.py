#!/usr/bin/env python3
"""Compile the panel probe and replay its GPIO protocol against original U-Boot."""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path

import capstone as c
import unicorn as u
from unicorn import arm_const as a

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE, RETURN, STACK = 0x4a000000, 0x7000f000, 0x4a0ff000
ICFG, IDATA, ECFG, EPULL, EDATA = 0x0300b124, 0x0300b130, 0x0300b090, 0x0300b0ac, 0x0300b0a0
MASKS = {ICFG: 0xff000000, IDATA: 0xc000, ECFG: 0x0f000000, EPULL: 0x3000}
FREQUENCY = 24000000


def sha(data):
    return hashlib.sha256(data).hexdigest()


def items(package):
    result = {}
    for i in range(struct.unpack_from('<I', package, 32)[0]):
        at = 64 + i * 368
        name = package[at:at + 64].split(b'\0', 1)[0].decode()
        off, size = struct.unpack_from('<II', package, at + 64)
        assert off + size <= len(package) and name not in result
        result[name] = package[off:off + size]
    return result


def instructions(blob, start, end):
    return list(c.Cs(c.CS_ARCH_ARM, c.CS_MODE_THUMB).disasm(blob[start:end], BASE + start))


def locate_vendor(blob):
    pattern = bytes.fromhex('23f0706343f0c063bff35f8f1360')
    cfg = blob.find(pattern)
    assert cfg >= 0 and blob.find(pattern, cfg + 1) < 0, 'PE6 vendor configuration must be unique'
    starts = [i for i in range(cfg - 0x180, cfg, 2) if blob[i:i + 2] == bytes.fromhex('38b5')]
    assert len(starts) == 1
    entry = starts[0]
    ptr = struct.pack('<I', BASE + entry + 1)
    refs = [i for i in range(0, len(blob) - 4, 4) if blob[i:i + 4] == ptr]
    assert len(refs) == 1
    flow = refs[0] - 0x34
    assert blob[flow:flow + 2] == bytes.fromhex('10b5')
    tail = instructions(blob, cfg + len(pattern), refs[0] if refs[0] < cfg + 0x100 else cfg + 0x100)
    eor = next(i for i, ins in enumerate(tail) if ins.mnemonic == 'eor' and '#0x40' in ins.op_str)
    store = next(ins for ins in tail[eor:] if ins.mnemonic == 'str')
    delay = next(ins for ins in instructions(blob, entry, cfg) if ins.mnemonic == 'bl')
    printf_calls = {int(ins.op_str[1:], 16) for ins in tail[:eor] if ins.mnemonic == 'bl'}
    return {'panel_init': entry, 'open_flow': flow, 'strap_cfg': cfg,
            'stop': store.address + store.size - BASE,
            'delay': int(delay.op_str[1:], 16) - BASE,
            'printf': [x - BASE for x in sorted(printf_calls)]}


class GPIO:
    def __init__(self, level, pull_up, seed=0, ticks=0):
        self.level, self.pull_up, self.now = level, pull_up, ticks
        self.regs = {address: seed for address in MASKS}
        self.events, self.samples = [], []

    def ready(self):
        data = [e for e in self.events if e['address'] == IDATA]
        if len(data) != 4:
            return False
        if [bool(e['value'] & 0x4000) for e in data[1:]] != [True, False, True]:
            return False
        elapsed = lambda end, start: (end - start) & 0xffffffffffffffff
        return (bool(data[0]['value'] & 0x8000) and
                all(e['value'] & 0x8000 for e in data) and
                elapsed(data[1]['ticks'], data[0]['ticks']) >= 30 * 24000 and
                elapsed(data[2]['ticks'], data[1]['ticks']) >= 15 * 24000 and
                elapsed(data[3]['ticks'], data[2]['ticks']) >= 50 * 24000 and
                elapsed(self.now, data[3]['ticks']) >= 100 * 24000 and
                self.regs[ICFG] & 0xff000000 == 0x11000000 and
                self.regs[ECFG] & 0x0f000000 == 0x06000000 and
                (not self.pull_up or self.regs[EPULL] & 0x3000 == 0x1000))

    def read(self, address):
        if address == EDATA:
            ready = self.ready()
            self.samples.append({'ticks': self.now, 'prerequisites_met': ready})
            return 0x40 if self.level and ready else 0
        return self.regs.get(address, 0)

    def write(self, address, value):
        assert address in MASKS, f'unexpected GPIO write {address:#x}'
        self.regs[address] = value
        self.events.append({'address': address, 'value': value, 'ticks': self.now})


def machine(blob, thumb=False):
    m = u.Uc(u.UC_ARCH_ARM, u.UC_MODE_THUMB if thumb else u.UC_MODE_ARM)
    m.mem_map(BASE, 0x100000)
    m.mem_write(BASE, blob)
    m.mem_map(0x0300b000, 0x1000)
    m.mem_map(0x70000000, 0x10000)
    m.reg_write(a.UC_ARM_REG_SP, STACK)
    return m


def gpio_hooks(m, gpio):
    def read(uc, access, address, size, value, data):
        assert size == 4
        uc.mem_write(address, struct.pack('<I', gpio.read(address)))
    def write(uc, access, address, size, value, data):
        assert size == 4
        gpio.write(address, value)
    m.hook_add(u.UC_HOOK_MEM_READ, read, begin=0x0300b000, end=0x0300bfff)
    m.hook_add(u.UC_HOOK_MEM_WRITE, write, begin=0x0300b000, end=0x0300bfff)


def call(m, entry, thumb=False, end=RETURN, count=100000):
    m.reg_write(a.UC_ARM_REG_LR, RETURN | int(thumb))
    m.emu_start(entry | int(thumb), end, count=count)
    assert m.reg_read(a.UC_ARM_REG_PC) == end, f'entry {entry:#x} did not return/stop'


def vendor_case(blob, located, level, pull_up):
    m = machine(blob, True)
    m.mem_write(BASE + 0xe0000, bytes(0x20000))  # relocated .bss and driver callback pointers
    gpio = GPIO(level, pull_up)
    gpio_hooks(m, gpio)
    callbacks, delays, result = [], [], []
    flow_ins = instructions(blob, located['open_flow'], located['open_flow'] + 0x30)
    flow_call = next(ins for ins in flow_ins if ins.mnemonic == 'bl')
    register_flow = int(flow_call.op_str[1:], 16)
    def replace(uc, pc, size, data):
        if pc == register_flow:
            callbacks.append((uc.reg_read(a.UC_ARM_REG_R1), uc.reg_read(a.UC_ARM_REG_R2)))
        elif pc == BASE + located['delay']:
            ms = uc.reg_read(a.UC_ARM_REG_R0)
            delays.append(ms)
            gpio.now += ms * 24000
        uc.reg_write(a.UC_ARM_REG_R0, 0)
        uc.reg_write(a.UC_ARM_REG_PC, uc.reg_read(a.UC_ARM_REG_LR))
    for pc in [register_flow, BASE + located['delay'], *[BASE + x for x in located['printf']]]:
        m.hook_add(u.UC_HOOK_CODE, replace, begin=pc, end=pc)
    call(m, BASE + located['open_flow'], True)
    assert len(callbacks) == 4 and callbacks[1][0] == BASE + located['panel_init'] + 1
    assert [ms for _, ms in callbacks] == [30, 50, 100, 0]
    call(m, callbacks[0][0] & ~1, True)
    gpio.now += callbacks[0][1] * 24000
    # The final store after PE6 EOR/UBFX is the actual vendor panel decision.
    def decision(uc, access, address, size, value, data):
        if gpio.samples:
            result.append(value)
    m.hook_add(u.UC_HOOK_MEM_WRITE, decision, begin=BASE, end=BASE + 0xfffff)
    call(m, BASE + located['panel_init'], True, BASE + located['stop'])
    assert delays == [15, 50, 100] and result == [int(not level)]
    assert gpio.samples == [{'ticks': 195 * 24000, 'prerequisites_met': True}]
    assert (EPULL in [e['address'] for e in gpio.events]) == bool(pull_up)
    return gpio, callbacks


def compile_probe(output, header=None, stalled=True):
    (output / 'panel-probe.h').write_bytes(header if header is not None else (HERE / 'panel-probe.h').read_bytes())
    source = output / 'probe.c'
    source.write_text('''#include "panel-probe.h"
extern uint32_t probe_read(uint32_t);
extern void probe_write(uint32_t,uint32_t);
extern uint64_t probe_ticks(void);
int probe_entry(unsigned rule,unsigned pull) {
 const struct baseos_panel_io io={probe_read,probe_write,probe_ticks};
 return baseos_panel_probe(rule,pull,&io);
}
int legacy_probe(void) {
 probe_write(0x0300b090,probe_read(0x0300b090)&~0x0f000000u);
 probe_write(0x0300b0ac,(probe_read(0x0300b0ac)&~0x3000u)|0x1000u);
 return !(probe_read(0x0300b0a0)&0x40);
}
''')
    asm = output / 'callbacks.S'
    asm.write_text('''.syntax unified
.arm
.global probe_read,probe_write,probe_ticks
probe_read: ldr r0,[r0]
 bx lr
probe_write: str r1,[r0]
 bx lr
probe_ticks: mov r0,#0
 mov r1,#0
 bx lr
''')
    clang = shutil.which('clang')
    lld = sorted((Path.home() / '.rustup/toolchains').glob('*/lib/rustlib/*/bin/gcc-ld/ld.lld'))[-1]
    env = dict(os.environ)
    for parent in lld.parents:
        if (parent / 'lib/libLLVM.dylib').is_file():
            env['DYLD_LIBRARY_PATH'] = str(parent / 'lib')
            break
    flags = ['--target=armv7a-none-eabi','-march=armv7-a','-marm','-mfloat-abi=soft',
             '-Os','-ffreestanding','-fno-builtin','-fno-stack-protector','-fno-unwind-tables',
             '-fno-asynchronous-unwind-tables','-Wall','-Wextra','-Werror','-I',str(output)]
    for path in (source, asm):
        subprocess.run([clang,*flags,'-c',str(path),'-o',str(path.with_suffix('.o'))],check=True)
    script = output / 'probe.ld'
    script.write_text('SECTIONS { .text 0x4a000000 : { *(.text*) *(.rodata*) *(.data*) } /DISCARD/ : { *(.ARM.exidx*) *(.comment) *(.ARM.attributes) } }')
    subprocess.run([str(lld),'-T',str(script),'-Map='+str(output/'probe.map'),str(source.with_suffix('.o')),
                    str(asm.with_suffix('.o')),'--oformat=binary','-o',str(output/'probe.bin')],check=True,env=env)
    symbols={name:int(address,16) for address,name in re.findall(
        r'^\s*([0-9a-f]+)\s+[0-9a-f]+\s+[0-9a-f]+\s+\d+\s+(\w+)\s*$',(output/'probe.map').read_text(),re.M)}
    if not stalled:
        return (output/'probe.bin').read_bytes(),symbols,None
    # A real host build executes the unchanged finite poll loop with a stalled counter.
    host = output / 'stalled.c'
    host.write_text('''#include "panel-probe.h"
static unsigned calls,writes,samples;
static uint32_t rd(uint32_t p){if(p==0x0300b0a0)samples++;return 0;}
static void wr(uint32_t p,uint32_t v){(void)p;(void)v;writes++;}
__attribute__((noinline)) static uint64_t tick(void){calls++;return 0;}
int main(void){const struct baseos_panel_io io={rd,wr,tick};
 return !(baseos_panel_probe(2,1,&io)==-1 && calls==100000001u && writes==2 && samples==0);}
''')
    subprocess.run([clang,'-Os','-Wall','-Wextra','-Werror','-I',str(output),str(host),'-o',str(output/'stalled')],check=True)
    subprocess.run([str(output/'stalled')],check=True,timeout=10)
    return (output/'probe.bin').read_bytes(),symbols,subprocess.check_output([clang,'--version'],text=True).splitlines()[0]


def native_case(blob,symbols,rule,pull_up,level,seed=0,start=0,legacy=False,expect_not_ready=False):
    m=machine(blob)
    gpio=GPIO(level,pull_up,seed,start)
    gpio_hooks(m,gpio)
    def ticks(uc,pc,size,data):
        gpio.now=(gpio.now+2400)&0xffffffffffffffff
        uc.reg_write(a.UC_ARM_REG_R0,gpio.now&0xffffffff)
        uc.reg_write(a.UC_ARM_REG_R1,gpio.now>>32)
        uc.reg_write(a.UC_ARM_REG_PC,uc.reg_read(a.UC_ARM_REG_LR))
    m.hook_add(u.UC_HOOK_CODE,ticks,begin=symbols['probe_ticks'],end=symbols['probe_ticks'])
    m.reg_write(a.UC_ARM_REG_R0,rule);m.reg_write(a.UC_ARM_REG_R1,pull_up)
    call(m,symbols['legacy_probe' if legacy else 'probe_entry'],count=200000)
    result=m.reg_read(a.UC_ARM_REG_R0)
    if legacy or expect_not_ready:
        assert result==1 and gpio.samples and not gpio.samples[0]['prerequisites_met']
    elif rule<2:
        assert result==rule and not gpio.events and not gpio.samples and gpio.now==start
    else:
        assert result==int(not level) and len(gpio.samples)==1 and gpio.samples[0]['prerequisites_met']
        assert [e['address'] for e in gpio.events]==[ICFG,IDATA,IDATA,IDATA,IDATA,ECFG]+([EPULL] if pull_up else [])
        for address,mask in MASKS.items():
            assert gpio.regs[address]&~mask == seed&~mask,'unrelated GPIO bits changed'
    return {'rule':rule,'pull_up':bool(pull_up),'level':level,'seed':hex(seed),'initial_ticks':hex(start),
            'result':result,'gpio_writes':gpio.events,'strap_samples':gpio.samples}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--contract',type=Path,default=HERE/'panel-contract.json')
    parser.add_argument('--write-contract',action='store_true',help='regenerate the owned vendor contract after actual instruction replay')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    blob,symbols,compiler=compile_probe(args.output)
    compatibility=json.loads((args.root/'experiments/h700-boot-speed/evidence/compatibility.json').read_text())['targets']
    contract={'version':1,'counter_hz':FREQUENCY,'prerequisites':{'pi_cfg1':hex(ICFG),'pi_data':hex(IDATA),
              'power_pin':15,'reset_pin':14,'power_wait_ms':30,'reset_levels':[1,0,1],'reset_waits_ms':[15,50,100],
              'pe_cfg0':hex(ECFG),'pe_pull0':hex(EPULL),'pe_data':hex(EDATA),'detect_pin':6,'detect_function':6},'models':{}}
    recorded={}
    trace_pins={}
    for level,name in (('high','uboot-trace-all.json'),('low','uboot-trace-pe6-low.json')):
        path=args.root/'experiments/h700-boot-speed/evidence'/name
        trace_pins[name]=sha(path.read_bytes())
        for run in json.loads(path.read_text()):
            assert run['jump']['entry']==0x40080000
            token=re.search(r'(?:^| )lcd_type=([^ ]+)',run['bootargs']).group(1)
            recorded.setdefault(run['target'],{})[level]=token
    rows=[]
    for target,previous in compatibility.items():
        package=(args.root/'work'/target/'boot-gzip/boot-package.bin').read_bytes()
        assert sha(package)==previous['package_sha256'],target
        vendor=items(package)['u-boot']
        assert previous['vendor_pe6_lcd_type']==recorded[target]
        high,low=[recorded[target][x] for x in ('high','low')]
        rule={('old','old'):0,('boe','boe'):1,('old','boe'):2}[high,low]
        entry={'rule':rule,'high':high,'low':low,'package_sha256':sha(package),'vendor_uboot_sha256':sha(vendor),
               'pull_up':False,'vendor_entries':None}
        tests=[]
        if rule==2:
            located=locate_vendor(vendor)
            # Whether the original code touches PE_PULL0 is decided by its actual MMIO writes.
            pull_up=struct.pack('<I',EPULL) in vendor[located['strap_cfg']:located['stop']+0x80]
            entry['pull_up']=pull_up
            for level in (0,1):
                vg,callbacks=vendor_case(vendor,located,level,pull_up)
                case=native_case(blob,symbols,rule,pull_up,level)
                assert [(e['address'],e['value']&MASKS[e['address']]) for e in vg.events] == \
                       [(e['address'],e['value']&MASKS[e['address']]) for e in case['gpio_writes']]
                tests.append(case)
            entry['vendor_entries']={name:hex(located[name]) for name in ('panel_init','open_flow','strap_cfg','stop','delay')}
            entry['vendor_entries']['power_on']=hex(callbacks[0][0]-BASE-1)
        else:
            tests=[native_case(blob,symbols,rule,False,level) for level in (0,1)]
        contract['models'][target]=entry
        rows.append({'target':target,**entry,'cases':tests})
    if args.write_contract:
        args.contract.write_text(json.dumps(contract,indent=2)+'\n')
    assert json.loads(args.contract.read_text())==contract,'vendor panel contract differs from actual inputs/replay'
    extra=[native_case(blob,symbols,2,pull,level,seed,start)
           for pull in (False,True) for level in (0,1)
           for seed in (0xffffffff,0xa5a5a5a5,0x5a5a5a5a)
           for start in (0,0xffffffffffc00000)]
    legacy=native_case(blob,symbols,2,True,1,legacy=True)
    negatives=[]
    original=(HERE/'panel-probe.h').read_text()
    mutations=[('panel_power_off','0x8000u, 0x8000u','0x8000u, 0'),
               ('reset_never_low','0x4000u, 0);','0x4000u, 0x4000u);'),
               *[(f'missing_{ms}ms_wait',f'baseos_panel_wait(io, {ms})',f'baseos_panel_wait(io, 0)') for ms in (30,15,50,100)],
               ('wrong_detect_mux','0x0f000000u, 0x06000000u','0x0f000000u, 0'),
               ('missing_pull_up','if (pull_up) baseos_panel_update','if (0) baseos_panel_update')]
    for name,before,after in mutations:
        assert original.count(before)==1,name
        mutant=args.output/name
        mutant.mkdir()
        changed=original.replace(before,after).encode()
        bad,bad_symbols,_=compile_probe(mutant,changed,False)
        case=native_case(bad,bad_symbols,2,True,1,expect_not_ready=True)
        negatives.append({'mutation':name,'compiled_sha256':sha(bad),'result':case['result'],
                          'prerequisites_met':case['strap_samples'][0]['prerequisites_met']})
    (args.output/'panel-contract.json').write_bytes(args.contract.read_bytes())
    report={'passed':True,'no_device_access':True,'hardware_qualified':False,'compiler':compiler,
            'header_sha256':sha((HERE/'panel-probe.h').read_bytes()),'verifier_sha256':sha(Path(__file__).read_bytes()),
            'contract_sha256':sha(args.contract.read_bytes()),'compiled_probe_sha256':sha(blob),'models':rows,'prior_vendor_trace_pins':trace_pins,
            'negative_compiled_prerequisite_mutations':negatives,
            'additional_preservation_and_counter_wrap_cases':extra,'legacy_old_panel_false_boe':legacy,
            'stalled_counter':{'actual_host_compiled_loop':True,'ticks_calls':100000001,'result':-1,
                               'strap_reads':0,'gpio_writes':2,'rtc_wdt_writes':0},
            'limitations':['Electrical strap readiness is a modeled boundary; physical proof remains required.',
                           'Dynamic oracle executes original U-Boot power/reset/strap instructions; unrelated power callbacks and printf are modeled.',
                           'Fixed rule0/1 decisions use the previously recorded all11 original vendor traces; no GPIO acquisition is added.',
                           'Comparison projects vendor writes onto PI14/PI15 and PE6; native deliberately preserves unrelated SPI GPIO bits.']}
    report['seal']=sha(json.dumps(report,sort_keys=True,separators=(',',':')).encode())
    (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':True,'models':len(rows),'additional_cases':len(extra),'seal':report['seal'],
                      'report':str(args.output/'verification.json')}))


if __name__=='__main__':
    main()
