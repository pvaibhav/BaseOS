#!/usr/bin/env python3
"""Pack a model's unchanged kernel/DTB plus library initramfs into one boot tail."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,stat,struct,sys,tarfile
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT/'tools'))
import boot_payload as bp

def require(ok,message):
    if not ok:raise ValueError(message)
def sha(blob):return hashlib.sha256(blob).hexdigest()
def tar_file(tar,name):
    members=[m for m in tar.getmembers() if m.name.removeprefix('./')==name]
    require(len(members)==1 and members[0].isfile(),'expected one regular rootfs file: '+name)
    return tar.extractfile(members[0]).read()
def static_busybox(blob):
    require(blob[:6]==b'\x7fELF\x02\x01' and len(blob)>=64 and struct.unpack_from('<H',blob,18)[0]==183,'BusyBox must be little-endian ELF64 AArch64')
    offset=struct.unpack_from('<Q',blob,32)[0];size,count=struct.unpack_from('<HH',blob,54)
    require(size>=56 and offset+size*count<=len(blob),'truncated BusyBox program headers')
    require(all(struct.unpack_from('<I',blob,offset+i*size)[0]!=3 for i in range(count)),'initramfs BusyBox must be static')

def newc(entries):
    out=bytearray()
    for inode,(name,mode,data,rdev) in enumerate(entries+[('TRAILER!!!',0,b'',(0,0))],1):
        encoded=name.encode()+b'\0'
        fields=(inode,mode,0,0,2 if stat.S_ISDIR(mode) else 1,0,len(data),0,0,*rdev,len(encoded),0)
        out+=b'070701'+b''.join(f'{value:08x}'.encode() for value in fields)+encoded
        out+=bytes((-len(out))%4);out+=data;out+=bytes((-len(out))%4)
    out+=bytes((-len(out))%512)
    return bytes(out)

def build(native_dir,rootfs_tar,library_path,output):
    require(not output.exists(),'fresh output directory required')
    manifest=json.loads((native_dir/'manifest.json').read_text());source=(native_dir/'image.bin').read_bytes();kernel=(native_dir/'kernel.bin').read_bytes()
    require(len(source)==manifest['image_bytes'] and sha(source)==manifest['image_sha256'],'source Android image differs')
    require(sha(kernel)==manifest['kernel_sha256'],'source kernel differs')
    require(not manifest.get('native_diagnostic') and not manifest.get('kernel_initcall_probe') and not manifest.get('kernel_exception_witness'),'production unchanged-kernel input required')
    parts=bp.android_parts(source);library=library_path.read_bytes()
    require(len(library)>=96 and library[:4]==b'hsqs','library must be little-endian squashfs')
    require(struct.unpack_from('<H',library,20)[0]==5 and struct.unpack_from('<H',library,28)[0]==4,'library must be squashfs4/LZ4')
    require(0<struct.unpack_from('<Q',library,40)[0]<=len(library),'truncated squashfs')
    with tarfile.open(rootfs_tar) as tar:
        busybox=tar_file(tar,'usr/bin/busybox');release=tar_file(tar,'etc/baseos-release').decode()
        require(('BASEOS_TARGET='+manifest['target']) in release.splitlines(),'rootfs target differs')
        # Format2 carries an ABI ID, not per-card capsule attempt identity.
        require(not any(m.name.removeprefix('./')=='etc/baseos/native-generation' for m in tar.getmembers()),'format2 rootfs must not contain per-card native-generation')
        require(not any(m.name.removeprefix('./')=='etc/baseos/boot-payload-id' for m in tar.getmembers()),'root build ID must be inserted after packing, not hashed into itself')
        staged_abi=tar_file(tar,'etc/baseos/native-abi')
    static_busybox(busybox);abi=bp.native_abi_id(manifest)
    require(staged_abi==abi,'staged rootfs native ABI differs from model/kernel contract')
    init=(HERE/'init').read_bytes();header_source=(HERE/'boot-blob.h').read_bytes();module_source=Path(bp.__file__).read_bytes()
    material=dict(format=bp.FORMAT,target=manifest['target'],native_abi=abi.hex(),rootfs_tar_sha256=sha(rootfs_tar.read_bytes()),library_sha256=sha(library),busybox_sha256=sha(busybox),source_image_sha256=sha(source),init_sha256=sha(init),header_source_sha256=sha(header_source),host_protocol_sha256=sha(module_source))
    payload_id=hashlib.sha256(b'BaseOS sequential root build\0'+json.dumps(material,sort_keys=True,separators=(',',':')).encode()).digest()
    entries=[(name,stat.S_IFDIR|0o755,b'',(0,0)) for name in ('bin','baseos','dev','proc','sys','newroot')]
    entries += [('dev/console',stat.S_IFCHR|0o600,b'',(5,1)),('init',stat.S_IFREG|0o755,init,(0,0)),('bin/busybox',stat.S_IFREG|0o755,busybox,(0,0)),('baseos/root-build-id',stat.S_IFREG|0o444,payload_id,(0,0)),('baseos/usr-lib.sqfs',stat.S_IFREG|0o444,library,(0,0))]
    archive=newc(entries);require(len(archive)<=bp.INITRD_CAPACITY,'library initramfs exceeds32MiB reservation')
    parts[1]=archive;head=bytearray(source[:2048]);identifier=hashlib.sha1()
    for at,part in zip(bp.SIZES,parts):struct.pack_into('<I',head,at,len(part));identifier.update(part);identifier.update(struct.pack('<I',len(part)))
    head[0x240:0x260]=identifier.digest()+bytes(12)
    image=bytes(head)+b''.join(p+bytes((-len(p))%2048) for p in parts)
    require(len(image)+512<=bp.TAIL_BYTES,'payload exceeds40MiB slot tail')
    require(bp.android_parts(image)[0]==bp.android_parts(source)[0] and bp.android_parts(image)[4]==bp.android_parts(source)[4],'kernel/DTB changed')
    descriptor=bp.make_header(target=manifest['target'],image=image,initrd=archive,library=library,kernel_sha256=manifest['kernel_sha256'],native_abi=abi,root_build_id=payload_id)
    decoded=bp.parse_header(descriptor,target=manifest['target'],native_abi=abi,kernel_sha256=manifest['kernel_sha256']);bp.validate_image(decoded,image)
    output.mkdir(parents=True)
    for name,data in [('descriptor.bin',descriptor),('initramfs.cpio',archive),('image.bin',image),('boot-blob.bin',descriptor+image),('root-build-id',payload_id),('native-abi',abi)]:
        with (output/name).open('xb') as stream:stream.write(data)
    report=dict(passed=True,**decoded,inputs=material,source_native_manifest_sha256=sha((native_dir/'manifest.json').read_bytes()),source_capsule_generation=manifest['generation'],capsule_generation_is_not_abi=True,initramfs_entries=len(entries),busybox_bytes=len(busybox),tail_free_bytes=bp.TAIL_BYTES-512-len(image),unchanged_kernel_and_dtb=True,rootfs_marker='/etc/baseos/boot-payload-id',ram_library_path='/usr/share/baseos/boot-ram/usr-lib.sqfs',existing_rcS_unchanged=True,device_access=False,hardware_qualified=False,durable_install_eligible=False,required_firmware_changes=['GPT selected-root tail reader','dynamic Android size/FNV bound to descriptor','32MiB initrd and48MiB staging bounds with cache/lifetime proof','native-abi FDT/health/update flow separate from RTC capsule generation'])
    (output/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('native_dir',type=Path);p.add_argument('rootfs_tar',type=Path);p.add_argument('library_sqfs',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    r=build(a.native_dir,a.rootfs_tar,a.library_sqfs,a.output);print(json.dumps({k:r[k] for k in ('passed','target','native_abi','payload_id','image_bytes','initrd_bytes','library_bytes','tail_free_bytes','hardware_qualified')},indent=2))
if __name__=='__main__':main()
