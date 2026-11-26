#!/usr/bin/env python3
"""Disable only vendor U-Boot's normal boot0 updater in a pinned recovery TOC.

Host-only file transform. Does not change kernel, monitor, DTB, DTBO or DRAM
parameters. Requires Capstone for an instruction/unique-string callsite check.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import capstone

BASE=0x4a000000
STAMP=0x5f0a6c39
MESSAGE=b'begin to update boot0 atfer ota\n\0'
PATCH=bytes.fromhex('002000bf')  # movs r0,#0; nop
MD=capstone.Cs(capstone.CS_ARCH_ARM,capstone.CS_MODE_THUMB)
MD.detail=True
sha=lambda blob:hashlib.sha256(blob).hexdigest()


def checksum(blob,field):
    if len(blob)%4:raise ValueError('checksum input is not word-aligned')
    words=struct.unpack(f'<{len(blob)//4}I',blob)
    return (sum(words)-struct.unpack_from('<I',blob,field)[0]+STAMP)&0xffffffff


def locate(code):
    strings=[at for at in range(len(code)) if code.startswith(MESSAGE,at)]
    if len(strings)!=1:raise ValueError('normal updater message is missing or ambiguous')
    pointer=struct.pack('<I',BASE+strings[0])
    literals=[at for at in range(0,len(code)-3,4) if code[at:at+4]==pointer]
    hits=[]
    for literal in literals:
        for at in range(max(0,literal-0x400),literal,2):
            ins=next(MD.disasm(code[at:at+4],BASE+at),None)
            if not ins or ins.mnemonic!='ldr' or len(ins.operands)!=2:continue
            dst,mem=ins.operands
            if dst.type!=capstone.arm.ARM_OP_REG or dst.reg!=capstone.arm.ARM_REG_R1:continue
            if mem.type!=capstone.arm.ARM_OP_MEM or mem.mem.base!=capstone.arm.ARM_REG_PC:continue
            if ((at+4)&~3)+mem.mem.disp!=literal:continue
            seq=list(MD.disasm(code[at:at+40],BASE+at))
            if len(seq)<8 or not (seq[1].mnemonic=='movs' and seq[1].op_str=='r0, #3' and
                seq[2].mnemonic=='bl' and seq[3].mnemonic=='bl' and seq[3].size==4 and
                seq[4].mnemonic=='ldr' and seq[4].op_str.startswith('r2, ') and
                seq[5].mnemonic=='mov' and seq[5].op_str=='r4, r0'):
                raise ValueError('normal updater caller instruction contract changed')
            starts=[]
            for p in range(max(0,at-0x80),at,2):
                inst=next(MD.disasm(code[p:p+4],BASE+p),None)
                if inst and inst.mnemonic=='push' and inst.op_str=='{r4, lr}':starts.append(p)
            if not starts:raise ValueError('normal updater function entry not found')
            call=seq[3];target=call.operands[0].imm-BASE
            if not 0<=target<len(code)-4:raise ValueError('updater BL target outside U-Boot')
            callee=next(MD.disasm(code[target:target+4],BASE+target),None)
            if not callee or callee.mnemonic!='push':raise ValueError('updater target prologue changed')
            hits.append(dict(entry=max(starts),call=call.address-BASE,target=target,
                string=strings[0],literal=literal,old=call.bytes.hex()))
    if len(hits)!=1:raise ValueError('normal updater callsite is missing or ambiguous')
    return hits[0]


def parse(package):
    if len(package)<64 or struct.unpack_from('<I',package,16)[0]!=0x89119800:
        raise ValueError('not a vendor TOC1 package')
    count,length=struct.unpack_from('<II',package,32)
    if not 0<count<=16 or not 0<length<=len(package) or length%512:
        raise ValueError('invalid TOC1 count/length')
    if checksum(package[:length],20)!=struct.unpack_from('<I',package,20)[0]:
        raise ValueError('input TOC1 checksum mismatch')
    if 64+count*368>length:raise ValueError('TOC1 item table exceeds package')
    items={}
    for index in range(count):
        at=64+index*368
        name=package[at:at+64].split(b'\0',1)[0].decode('ascii')
        offset,size=struct.unpack_from('<II',package,at+64)
        if name in items or not size or offset<64+count*368 or offset+size>length:
            raise ValueError('invalid TOC1 item extent')
        items[name]=(offset,size)
    if set(items)!={'u-boot','monitor','dtb','dtbo'}:
        raise ValueError('unexpected recovery TOC1 item set')
    ordered=sorted((offset,offset+size) for offset,size in items.values())
    if any(a[1]>b[0] for a,b in zip(ordered,ordered[1:])):
        raise ValueError('TOC1 item extents overlap')
    offset,size=items['u-boot']
    item=package[offset:offset+size]
    if size<64 or item[4:12]!=b'uboot\0\0\0':
        raise ValueError('invalid U-Boot image header')
    alignment,image_length,uboot_length=struct.unpack_from('<III',item,16)
    if not alignment or alignment&(alignment-1) or size%alignment or image_length!=size or uboot_length!=size:
        raise ValueError('U-Boot declared lengths/alignment disagree with its TOC item')
    if checksum(item,12)!=struct.unpack_from('<I',item,12)[0]:
        raise ValueError('input U-Boot image checksum mismatch')
    return length,items


def prepare(package,expected):
    if len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('expected SHA256 must be64 lowercase hex digits')
    if sha(package)!=expected:raise ValueError('input package SHA256 differs from pin')
    length,items=parse(package)
    offset,size=items['u-boot'];item=package[offset:offset+size];site=locate(item)
    result=bytearray(package);at=offset+site['call'];result[at:at+4]=PATCH
    struct.pack_into('<I',result,offset+12,checksum(result[offset:offset+size],12))
    struct.pack_into('<I',result,20,checksum(result[:length],20))
    result=bytes(result);parse(result)
    changed=[i for i,(old,new) in enumerate(zip(package,result)) if old!=new]
    if not changed or not all(20<=i<24 or offset+12<=i<offset+16 or at<=i<at+4 for i in changed):
        raise ValueError('change escaped the updater call/two checksum extents')
    records={name:dict(offset=start,bytes=n,
        original_sha256=sha(package[start:start+n]),sha256=sha(result[start:start+n]),
        unchanged=package[start:start+n]==result[start:start+n]) for name,(start,n) in items.items()}
    if not all(records[name]['unchanged'] for name in ('monitor','dtb','dtbo')):
        raise ValueError('non-U-Boot item changed')
    report=dict(schema=2,purpose='recovery-only normal boot0 updater suppression',
        original_package_sha256=sha(package),package_sha256=sha(result),package_bytes=len(result),
        declared_bytes=length,checksum_valid=True,uboot_checksum_valid=True,
        uboot_header=dict(alignment=struct.unpack_from('<I',item,16)[0],
            image_length=struct.unpack_from('<I',item,20)[0],uboot_length=struct.unpack_from('<I',item,24)[0],
            checksum_package_offset=offset+12,original_checksum=struct.unpack_from('<I',item,12)[0],
            checksum=struct.unpack_from('<I',result,offset+12)[0]),
        patch={**site,'package_offset':at,'new':PATCH.hex()},
        items=records,changed_byte_offsets=changed,
        hardware_qualified=False,durable_install_eligible=False,
        semantics='normal updater BL replaced with success return0; selected DRAM parameters untouched',
        scope='normal startup updater only; explicit interactive firmware update commands unchanged')
    return result,report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--expect-sha256',required=True)
    parser.add_argument('--report',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists() or args.report.exists():parser.error('use fresh output/report paths')
    result,report=prepare(args.input.read_bytes(),args.expect_sha256)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes(result)
    args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('package_sha256','checksum_valid','hardware_qualified')}))


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,UnicodeError) as error:raise SystemExit(f'recovery-toc: {error}')
