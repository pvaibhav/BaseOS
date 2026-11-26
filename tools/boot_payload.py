"""H700 sequential slot format: immutable firmware ABI, mutable root/payload ID.

The 512B descriptor and Android image occupy the final40MiB of a512MiB slot.
The ext4 filesystem must end at472MiB. This module performs no device access.
"""
from __future__ import annotations
import hashlib,json,re,shutil,stat,struct,subprocess
from pathlib import Path

HEADER_BYTES=512
SLOT_BYTES=512*1024*1024
TAIL_BYTES=40*1024*1024
TAIL_OFFSET=SLOT_BYTES-TAIL_BYTES
ROOTFS_BYTES=TAIL_OFFSET
INITRD_CAPACITY=32*1024*1024
IMAGE_CAPACITY=48*1024*1024
MAGIC=b'BOSSEQ1\0'
FORMAT='baseos-sequential-v1'
MODEL_IDS={name:i+1 for i,name in enumerate(('rg28xx','rg34xx','rg34xxsp','rg35xxh','rg35xxplus','rg35xxpro','rg35xxsp','rg40xxh','rg40xxv','rgcubexx','rgsp'))}
SIZES=(8,16,24,0x660,0x670)

def require(ok,message):
    if not ok:raise ValueError(message)

def sha256(blob):return hashlib.sha256(blob).digest()
def fnv32(blob):
    value=2166136261
    for byte in blob:value=((value^byte)*16777619)&0xffffffff
    return value

def identity(value,name):
    if isinstance(value,str):
        require(re.fullmatch('[0-9a-f]{64}',value) is not None,name+' must be64 lowercase hex digits')
        value=bytes.fromhex(value)
    require(isinstance(value,bytes) and len(value)==32 and any(value),name+' must be nonzero32 bytes')
    return value

def native_abi_id(manifest):
    """No boot0/calibration, serial, capsule attempt generation or rootfs input."""
    target=manifest['target'];require(MODEL_IDS.get(target)==manifest['model_id'],'model ID differs from native protocol')
    material=dict(protocol=FORMAT,firmware_abi=1,target=target,model_id=manifest['model_id'],
                  kernel_sha256=identity(manifest['kernel_sha256'],'kernel SHA').hex(),
                  kernel_dtb_sha256=identity(manifest['kernel_dtb_sha256'],'base DTB SHA').hex(),
                  slot_bytes=SLOT_BYTES,tail_offset=TAIL_OFFSET,external_initramfs='newc-ram-library-bind-v1')
    return sha256(b'BaseOS H700 native ABI\0'+json.dumps(material,sort_keys=True,separators=(',',':')).encode())

def android_parts(image):
    require(len(image)>=2048 and len(image)%2048==0 and image[:8]==b'ANDROID!','invalid Android image extent/magic')
    require(struct.unpack_from('<I',image,36)[0]==2048 and struct.unpack_from('<I',image,40)[0]==2 and struct.unpack_from('<I',image,1644)[0]==1660,'unsupported Android header')
    parts=[];position=2048
    for at in SIZES:
        size=struct.unpack_from('<I',image,at)[0];rounded=(size+2047)&~2047
        require(rounded<=len(image)-position,'Android component escapes image')
        parts.append(image[position:position+size]);position+=rounded
    require(position==len(image) and parts[0] and parts[4] and not parts[2] and not parts[3],'unsupported Android components')
    digest=hashlib.sha1()
    for part in parts:digest.update(part);digest.update(struct.pack('<I',len(part)))
    require(image[0x240:0x260]==digest.digest()+bytes(12),'Android component ID differs')
    return parts

def make_header(*,target,image,initrd,library,kernel_sha256,native_abi,root_build_id):
    require(target in MODEL_IDS,'unsupported model')
    raw=bytearray(512);raw[:8]=MAGIC
    struct.pack_into('<10I2Q',raw,8,1,512,MODEL_IDS[target],len(image),fnv32(image),len(initrd),fnv32(initrd),len(library),fnv32(library),0,SLOT_BYTES,TAIL_OFFSET)
    raw[64:96]=identity(native_abi,'native ABI');raw[96:128]=identity(root_build_id,'payload ID')
    raw[128:160]=sha256(image);raw[160:192]=identity(kernel_sha256,'kernel SHA');raw[192:224]=sha256(initrd);raw[224:256]=sha256(library)
    struct.pack_into('<I',raw,44,fnv32(raw));parse_header(bytes(raw));return bytes(raw)

def parse_header(raw,*,target=None,native_abi=None,kernel_sha256=None):
    require(len(raw)==512 and raw[:8]==MAGIC,'sequential descriptor magic/size differs')
    values=struct.unpack_from('<10I2Q',raw,8)
    version,size,model,image_bytes,image_fnv,initrd_bytes,initrd_fnv,library_bytes,library_fnv,checksum,slot_bytes,tail_offset=values
    check=bytearray(raw);check[44:48]=bytes(4)
    require(version==1 and size==512 and checksum==fnv32(check) and not any(raw[256:]),'damaged sequential descriptor')
    require(model in MODEL_IDS.values() and slot_bytes==SLOT_BYTES and tail_offset==TAIL_OFFSET,'unsupported slot/model geometry')
    require(0<image_bytes<=min(IMAGE_CAPACITY,TAIL_BYTES-512) and image_bytes%2048==0,'image escapes sequential tail')
    require(0<library_bytes<=initrd_bytes<=INITRD_CAPACITY and initrd_bytes<image_bytes,'invalid initrd/library extent')
    abi=identity(raw[64:96],'native ABI');payload=identity(raw[96:128],'payload ID')
    for offset in (128,160,192,224):identity(raw[offset:offset+32],'component SHA')
    name=next(name for name,value in MODEL_IDS.items() if value==model)
    require(target is None or target==name,'sequential payload targets another model')
    require(native_abi is None or identity(native_abi,'expected ABI')==abi,'sequential native ABI differs')
    require(kernel_sha256 is None or identity(kernel_sha256,'expected kernel')==raw[160:192],'sequential kernel ABI differs')
    return dict(format=FORMAT,target=name,model_id=model,image_bytes=image_bytes,image_fnv=image_fnv,initrd_bytes=initrd_bytes,initrd_fnv=initrd_fnv,library_bytes=library_bytes,library_fnv=library_fnv,native_abi=abi.hex(),payload_id=payload.hex(),image_sha256=raw[128:160].hex(),kernel_sha256=raw[160:192].hex(),initrd_sha256=raw[192:224].hex(),library_sha256=raw[224:256].hex(),slot_bytes=slot_bytes,tail_offset=tail_offset,blob_bytes=512+image_bytes)

def cpio_files(archive):
    files={};position=0
    while True:
        require(position+110<=len(archive) and archive[position:position+6]==b'070701','invalid newc header')
        try:fields=[int(archive[position+6+i*8:position+14+i*8],16) for i in range(13)]
        except ValueError:raise ValueError('invalid newc numeric field') from None
        size,namesize=fields[6],fields[11];position+=110
        require(0<namesize<=4096 and namesize<=len(archive)-position,'invalid newc name')
        name=archive[position:position+namesize];require(name[-1:]==b'\0' and b'\0' not in name[:-1],'invalid newc name terminator')
        name=name[:-1].decode('utf-8');position=(position+namesize+3)&~3
        require(size<=len(archive)-position,'newc file escapes archive')
        data=archive[position:position+size];position=(position+size+3)&~3
        if name=='TRAILER!!!':
            require(size==0 and not any(archive[position:]),'newc trailer/padding differs');return files
        require(name and not name.startswith('/') and '..' not in name.split('/') and name not in files,'unsafe/duplicate newc path')
        files[name]=dict(mode=fields[1],data=data)

def validate_image(header,image):
    require(len(image)==header['image_bytes'] and sha256(image).hex()==header['image_sha256'] and fnv32(image)==header['image_fnv'],'sequential Android image integrity differs')
    parts=android_parts(image);initrd=parts[1]
    require(len(initrd)==header['initrd_bytes'] and sha256(initrd).hex()==header['initrd_sha256'] and fnv32(initrd)==header['initrd_fnv'],'sequential initrd integrity differs')
    files=cpio_files(initrd)
    for name in ('init','bin/busybox','baseos/usr-lib.sqfs','baseos/root-build-id'):require(name in files and stat.S_ISREG(files[name]['mode']),'missing initramfs file: '+name)
    library=files['baseos/usr-lib.sqfs']['data']
    require(len(library)==header['library_bytes'] and sha256(library).hex()==header['library_sha256'] and fnv32(library)==header['library_fnv'],'initramfs library identity differs')
    require(files['baseos/root-build-id']['data'].hex()==header['payload_id'],'initramfs/root identity differs')
    return header

def ext4_bytes(superblock):
    require(len(superblock)==1024 and superblock[56:58]==b'\x53\xef','slot has no ext4 superblock')
    log=struct.unpack_from('<I',superblock,24)[0];incompat=struct.unpack_from('<I',superblock,96)[0]
    require(log<=2 and not incompat&0x80,'unsupported ext4 block geometry')
    size=struct.unpack_from('<I',superblock,4)[0]*(1024<<log)
    require(size==TAIL_OFFSET,'sequential ext4 must be exactly472MiB and leave its40MiB tail');return size

def debugfs_file(slot,path,expected_bytes=None):
    tool=shutil.which('debugfs');require(tool is not None,'debugfs required to inspect actual ext4 boot identity')
    result=subprocess.run([tool,'-R','stat '+path,str(slot)],capture_output=True,check=False)
    text=result.stdout.decode('utf-8','replace');match=re.search(r'^Inode:\s+\d+\s+Type:\s+(\S+)\s+Mode:\s+([0-7]+)',text,re.M);size=re.search(r'^User:.*?\bSize:\s+(\d+)',text,re.M)
    require(result.returncode==0 and match and size and match[1]=='regular','boot identity path must be regular: '+path)
    require(expected_bytes is None or int(size[1])==expected_bytes,'wrong ext4 identity length: '+path)
    result=subprocess.run([tool,'-R','cat '+path,str(slot)],capture_output=True,check=False)
    require(result.returncode==0 and len(result.stdout)==int(size[1]),'cannot read exact ext4 file: '+path);return result.stdout

def validate_slot(slot,target=None,native_abi=None,root_build_id=None,*,require_root_marker=True):
    slot=Path(slot);require(slot.is_file() and slot.stat().st_size==SLOT_BYTES,'sequential slot must be a regular512MiB file')
    with slot.open('rb') as stream:
        stream.seek(1024);filesystem_bytes=ext4_bytes(stream.read(1024))
        stream.seek(TAIL_OFFSET);header=parse_header(stream.read(512),target=target,native_abi=native_abi);image=stream.read(header['image_bytes'])
    validate_image(header,image)
    require(root_build_id is None or identity(root_build_id,'expected payload ID').hex()==header['payload_id'],'sequential payload ID differs')
    if require_root_marker:
        require(debugfs_file(slot,'/etc/baseos/boot-payload-id',32).hex()==header['payload_id'],'selected ext4 payload identity differs')
        require(debugfs_file(slot,'/etc/baseos/native-abi',32).hex()==header['native_abi'],'selected ext4 native ABI differs')
    return dict(header,root_build_id=header['payload_id'],ext4_bytes=filesystem_bytes)
