#!/usr/bin/env python3
"""Execute the freestanding slot reader against sparse host-only card models."""
from pathlib import Path
import argparse
import ctypes as C
import hashlib
import json
import os
import shutil
import struct
import subprocess
import zlib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MODELS = ['rg28xx', 'rg34xx', 'rg34xxsp', 'rg35xxh', 'rg35xxplus', 'rg35xxpro',
          'rg35xxsp', 'rg40xxh', 'rg40xxv', 'rgcubexx', 'rgsp']
SLOT = 512 * 1024 * 1024
TAIL = 40 * 1024 * 1024
TAIL_OFFSET = SLOT - TAIL
ABI = hashlib.sha256(b'test installed native ABI').digest()
KERNEL = hashlib.sha256(b'test original kernel').digest()
IMAGE = bytes((i * 17 + 5) & 255 for i in range(4096))
sha = lambda data: hashlib.sha256(data).hexdigest()
u32 = lambda b, o: struct.unpack_from('<I', b, o)[0]
u64 = lambda b, o: struct.unpack_from('<Q', b, o)[0]


def fnv(data):
    h = 2166136261
    for b in data:
        h = ((h ^ b) * 16777619) & 0xffffffff
    return h


def header_checksum(d):
    d = bytearray(d)
    struct.pack_into('<I', d, 44, 0)
    struct.pack_into('<I', d, 44, fnv(d))
    return bytes(d)


def descriptor(model, root_id):
    d = bytearray(512)
    d[:8] = b'BOSSEQ1\0'
    struct.pack_into('<10I', d, 8, 1, 512, model, len(IMAGE), fnv(IMAGE),
                     1024, fnv(IMAGE[:1024]), 512, fnv(IMAGE[:512]), 0)
    struct.pack_into('<QQ', d, 48, SLOT, TAIL_OFFSET)
    d[64:96] = ABI
    d[96:128] = root_id
    d[128:160] = hashlib.sha256(IMAGE).digest()
    d[160:192] = KERNEL
    d[192:224] = hashlib.sha256(IMAGE[:1024]).digest()
    d[224:256] = hashlib.sha256(IMAGE[:512]).digest()
    return header_checksum(d)


def gpt_checksum(prefix):
    p = bytearray(prefix)
    h = bytearray(p[512:1024])
    table = u64(h, 72) * 512
    struct.pack_into('<I', h, 88, zlib.crc32(p[table:table + 1024]) & 0xffffffff)
    struct.pack_into('<I', h, 16, 0)
    struct.pack_into('<I', h, 16, zlib.crc32(h[:92]) & 0xffffffff)
    p[512:1024] = h
    return bytes(p)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    cc = shutil.which('clang') or shutil.which('cc')
    wrapper = out / 'reader-test.c'
    wrapper.write_text('''#include "slot-reader.h"
int reader_test(void *context, int (*read)(void *, uint32_t, uint32_t, void *),
    uint64_t sectors, uint32_t model, const unsigned char *abi,
    const unsigned char *kernel, const unsigned char *root, void *destination,
    uint32_t capacity, uint32_t *fields, unsigned char *header)
{
    struct baseos_seq_io io = {context, read, sectors};
    struct baseos_seq_policy policy = {0};
    struct baseos_seq_slot slot;
    policy.model_id = model;
    memcpy(policy.native_abi, abi, 32); memcpy(policy.kernel_sha256, kernel, 32);
    policy.expected_root_build_id = root;
    int result = baseos_seq_load(&io, &policy, destination, capacity, &slot);
    fields[0] = slot.root_lba; fields[1] = slot.root_sectors;
    fields[2] = slot.descriptor_lba; fields[3] = slot.image_lba;
    fields[4] = slot.image_bytes; fields[5] = slot.slot;
    memcpy(header, &slot.header, 512);
    return result;
}
''')
    library = out / 'reader-test.dylib'
    subprocess.run([cc, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-shared',
                    '-fPIC', '-I', str(HERE), str(wrapper), '-o', str(library)], check=True)
    gptslot = out / 'gptslot'
    subprocess.run([cc, '-std=c11', '-D_DARWIN_C_SOURCE', '-O2', '-Wall', '-Wextra',
                    str(ROOT / 'tools/gptslot.c'), '-o', str(gptslot)], check=True)
    lib = C.CDLL(str(library))
    callback_type = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint32, C.c_uint32, C.c_void_p)
    call = lib.reader_test
    call.argtypes = [C.c_void_p, callback_type, C.c_uint64, C.c_uint32, C.c_void_p,
                     C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint32, C.c_void_p, C.c_void_p]
    call.restype = C.c_int
    rows = []
    pins = {}

    def trial(name, prefix, *, model=3, desc=None, image=IMAGE, expect=True,
              abi=ABI, kernel=KERNEL, expected_root=None, capacity=4096,
              misaligned=False, fail_call=0, fail_value=0, disk_sectors=None):
        h = prefix[512:1024]
        sectors = u64(h, 32) + 1 if disk_sectors is None else disk_sectors
        table = u64(h, 72) * 512
        root = u64(prefix, table + 4 * 128 + 32) if table + 640 <= len(prefix) else 0
        root_id = hashlib.sha256(('root@' + str(root)).encode()).digest()
        desc = descriptor(model, root_id) if desc is None else desc
        lba = root + TAIL_OFFSET // 512
        segments = [(0, prefix), (lba * 512, desc), ((lba + 1) * 512, image)]
        calls, unmapped = [], []

        @callback_type
        def read(_context, sector, blocks, destination):
            calls.append([sector, blocks])
            assert sector + blocks <= sectors
            if len(calls) == fail_call:
                return fail_value
            start, size = sector * 512, blocks * 512
            for offset, data in segments:
                if start >= offset and start + size <= offset + len(data):
                    C.memmove(destination, data[start - offset:start - offset + size], size)
                    return 1
            unmapped.append([sector, blocks])
            return 0

        destination = C.create_string_buffer(8196)
        address = C.addressof(destination) + int(misaligned)
        fields = (C.c_uint32 * 6)(*[0xa5a5a5a5] * 6)
        copied_header = C.create_string_buffer(b'\xa5' * 512, 512)
        ok = call(None, read, sectors, model, abi, kernel, expected_root,
                  address, capacity, fields, copied_header)
        assert bool(ok) == expect, (name, ok, calls)
        assert not unmapped, (name, 'reader requested unmapped media', unmapped)
        assert len(calls) <= 4
        if expect:
            assert calls == [[1, 1], [u64(h, 72), 2], [lba, 1], [lba + 1, len(image) // 512]], (name, calls)
            assert bytes(destination)[:len(image)] == image
            assert list(fields)[:5] == [root, SLOT // 512, lba, lba + 1, len(image)]
            assert copied_header.raw == desc and copied_header.raw[96:128] == root_id
        else:
            assert not any(fields) and copied_header.raw == bytes(512), name
        rows.append(dict(name=name, passed=True, accepted=bool(ok), reads=calls))

    fixtures = {}
    for target in ('rg34xxsp', 'rg40xxv'):
        source = ROOT / f'work/h700-loader/{target}-inventory-v1/prefix.bin'
        original = source.read_bytes()
        pins[str(source.relative_to(ROOT))] = sha(original)
        prefix = original[:34 * 512]
        disk = out / (target + '-sparse-card.img')
        fd = os.open(disk, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        last = u64(prefix, 512 + 32)
        os.ftruncate(fd, (last + 1) * 512)
        os.pwrite(fd, prefix, 0)
        table_offset = u64(prefix, 512 + 72) * 512
        table = prefix[table_offset:table_offset + 1024]
        backup = bytearray(prefix[512:1024])
        struct.pack_into('<QQ', backup, 24, last, 1)
        struct.pack_into('<Q', backup, 72, last - 2)
        struct.pack_into('<I', backup, 16, 0)
        struct.pack_into('<I', backup, 16, zlib.crc32(backup[:92]) & 0xffffffff)
        os.pwrite(fd, table, (last - 2) * 512)
        os.pwrite(fd, backup, last * 512)
        os.close(fd)
        before = subprocess.check_output([str(gptslot), str(disk), 'geometry'], text=True)
        subprocess.run([str(gptslot), str(disk), 'flip'], check=True, capture_output=True)
        with disk.open('rb') as f:
            flipped = f.read(34 * 512)
        after = subprocess.check_output([str(gptslot), str(disk), 'geometry'], text=True)
        disk.unlink()
        assert 'SLOT_ACTIVE=B\n' in before and 'SLOT_ACTIVE=A\n' in after
        old_table = prefix[table_offset:table_offset + 1024]
        new_table = flipped[table_offset:table_offset + 1024]
        assert old_table[:4 * 128 + 32] == new_table[:4 * 128 + 32]
        assert old_table[4 * 128 + 48:] == new_table[4 * 128 + 48:]
        (out / (target + '-gpt-B.bin')).write_bytes(prefix)
        (out / (target + '-gpt-A.bin')).write_bytes(flipped)
        fixtures[target] = (prefix, flipped)
        for letter, p in [('B', prefix), ('A', flipped)]:
            trial(target + '-actual-gpt-' + letter, p, model=MODELS.index(target) + 1)

    prefix = fixtures['rg34xxsp'][0]
    root = u64(prefix, u64(prefix, 512 + 72) * 512 + 4 * 128 + 32)
    root_id = hashlib.sha256(('root@' + str(root)).encode()).digest()
    good = descriptor(3, root_id)
    trial('root-id-exact-optional-policy', prefix, expected_root=root_id)
    trial('root-id-mismatch', prefix, expected_root=bytes([9]) * 32, expect=False)
    for model in range(1, 12):
        trial('model-id-' + str(model), prefix, model=model)
    for target in MODELS:
        source = ROOT / f'work/{target}/boot-prefix.img'
        original = source.read_bytes()
        pins[str(source.relative_to(ROOT))] = sha(original)
        trial(target + '-prepared-vendor-layout-refused', original[:34 * 512], expect=False)
    for number in range(1, 5):
        trial('short-IO-' + str(number), prefix, fail_call=number, expect=False)
    trial('non-success-callback', prefix, fail_call=4, fail_value=2, expect=False)
    trial('capacity-short', prefix, capacity=2048, expect=False)
    trial('capacity-over-arena', prefix, capacity=0x03000001, expect=False)
    trial('destination-misaligned', prefix, misaligned=True, expect=False)
    trial('card-size-mismatch', prefix, disk_sectors=u64(prefix, 544) + 2, expect=False)
    trial('card-over-32bit', prefix, disk_sectors=0x100000001, expect=False)
    trial('unbound-ABI', prefix, abi=bytes(32), expect=False)
    trial('wrong-policy-ABI', prefix, abi=bytes([2]) * 32, expect=False)
    trial('wrong-policy-kernel', prefix, kernel=bytes([2]) * 32, expect=False)
    trial('payload-corrupt', prefix, image=bytes([IMAGE[0] ^ 1]) + IMAGE[1:], desc=good, expect=False)
    for name, offset, data in [
        ('magic', 0, b'!'), ('version', 8, struct.pack('<I', 2)),
        ('header-size', 12, struct.pack('<I', 511)), ('target', 16, struct.pack('<I', 9)),
        ('unaligned-image', 20, struct.pack('<I', 4095)), ('empty-image', 20, bytes(4)),
        ('outside-tail-image', 20, struct.pack('<I', TAIL)),
        ('empty-initrd', 28, bytes(4)), ('initrd-outside-image', 28, struct.pack('<I', 8192)),
        ('empty-library', 36, bytes(4)), ('library-outside-initrd', 36, struct.pack('<I', 2048)),
        ('slot-size', 48, struct.pack('<Q', SLOT - 512)),
        ('tail-offset', 56, struct.pack('<Q', TAIL_OFFSET + 512)),
        ('ABI', 64, bytes(32)), ('empty-root-id', 96, bytes(32)),
        ('kernel-hash', 160, bytes(32)), ('reserved', 511, b'!'),
    ]:
        d = bytearray(good)
        d[offset:offset + len(data)] = data
        trial('descriptor-' + name, prefix, desc=header_checksum(d), expect=False)
    d = bytearray(good);d[44] ^= 1
    trial('descriptor-FNV', prefix, desc=bytes(d), expect=False)
    for name, offset, data in [
        ('magic', 512, b'!'), ('header-size', 524, struct.pack('<I', 93)),
        ('entry-count', 592, struct.pack('<I', 128)),
        ('entry-size', 596, struct.pack('<I', 256)),
        ('table-outside-prefix', 584, struct.pack('<Q', 0xffffffffffffffff)),
        ('root-name', 1024 + 4 * 128 + 56, b'X'),
        ('data-name', 1024 + 5 * 128 + 56, b'X'),
        ('root-start', 1024 + 4 * 128 + 32, struct.pack('<Q', 1482753)),
        ('root-end', 1024 + 4 * 128 + 40, struct.pack('<Q', 2531326)),
        ('data-gap', 1024 + 5 * 128 + 32, struct.pack('<Q', 2531329)),
        ('root-wrap', 1024 + 4 * 128 + 32, struct.pack('<Q', 0xffffffffffffffff)),
        ('overlap', 1024 + 6 * 128 + 32, struct.pack('<Q', 2531328)),
    ]:
        p = bytearray(prefix);p[offset:offset + len(data)] = data
        # The impossible table-location case cannot be used to recalculate its table CRC.
        if name == 'table-outside-prefix':
            struct.pack_into('<I', p, 528, 0)
            struct.pack_into('<I', p, 528, zlib.crc32(p[512:604]) & 0xffffffff)
            p = bytes(p)
        else:
            p = gpt_checksum(p)
        trial('GPT-' + name, p, expect=False)
    p = bytearray(prefix);p[528] ^= 1
    trial('GPT-header-CRC', bytes(p), expect=False)
    p = bytearray(prefix);p[1100] ^= 1
    trial('GPT-table-CRC', bytes(p), expect=False)

    include = out / 'freestanding'
    include.mkdir()
    (include / 'string.h').write_text('#include <stddef.h>\nvoid *memcpy(void *, const void *, size_t);\nvoid *memset(void *, int, size_t);\nint memcmp(const void *, const void *, size_t);\n')
    obj = out / 'reader-a64.o'
    subprocess.run([cc, '--target=aarch64-none-elf', '-std=c11', '-Oz', '-ffreestanding',
                    '-fno-stack-protector', '-I', str(include), '-I', str(HERE),
                    '-c', str(wrapper), '-o', str(obj)], check=True)
    elf = obj.read_bytes()
    shoff = u64(elf, 40);shsize, shnum = struct.unpack_from('<HH', elf, 58)
    text_bytes = sum(u64(elf, shoff + i * shsize + 32) for i in range(shnum)
                     if u64(elf, shoff + i * shsize + 8) & 4)
    sources = [Path(__file__).resolve(), HERE / 'slot-reader.h', HERE / 'boot-blob.h',
               ROOT / 'tools/gptslot.c', ROOT / 'tools/gpt.h']
    report = dict(passed=True, case_count=len(rows), cases=rows,
                  sources={str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in sources},
                  prepared_input_pins=pins, a64_executable_text_bytes=text_bytes,
                  host_compiled_C_executed=True, actual_gptslot_flip_executed=True,
                  whole_payload_reads_per_success=1, maximum_callback_reads=4,
                  root_build_id_contract='nonzero mutable ID returned; /init must compare selected ext4 raw32 identity; optional expected ID exercised',
                  limits=['payloads are synthetic; actual kernel/cpio/DTB unpacking belongs to packer/loader proofs',
                          'primary GPT only; malformed/transitional selection refuses, no inactive retry',
                          'A64 object compiled, not hardware or privileged-instruction emulation'],
                  device_access=False, hardware_qualified=False, durable_install_eligible=False)
    (out / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(passed=True, cases=len(rows), a64_text_bytes=text_bytes,
                         output=str(out), device_access=False)))


if __name__ == '__main__':
    main()
