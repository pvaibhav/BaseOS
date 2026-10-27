#!/usr/bin/env python3
"""Execute the legacy early-display A64 prototype with explicit peripheral models.

Use NATIVE_DIR --output FRESH_DIR. This is host-only instruction replay, not a
panel or timing qualification. Clang is not needed; Capstone/Unicorn are needed.
Current releases draw in A32 and use verify-loader-display.py. This verifier
requires the separate A64 helper artifact and cannot qualify a release build.
"""
import argparse
import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path

import capstone as c
import unicorn as u
from unicorn import arm64_const as a

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.dont_write_bytecode = True
HELPER, HELPER_CAP = 0x4a500000, 0x40000
RECORD, DESC, BMP, CAP = 0x4a300300, 0x4a300200, 0x4b400000, 0x400000
STACK, STOP = 0x4e00ff00, 0x4f000000
PHASES = ('prepare', 'publish', 'enable', 'backlight')
KINDS = {'update': 0, 'write': 1, 'framebuffer': 2, 'delay_us': 3, 'poll': 4, 'barrier': 5}
FIELDS = ('address', 'clear_mask', 'set_bits', 'timeout_us')
CFG, DAT, STRAP, SCANLINE = 0x0300b124, 0x0300b130, 0x0300b0a0, 0x065110fc
sha = lambda data: hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def xreg(index):
    return a.UC_ARM64_REG_X0 + index if index < 29 else (a.UC_ARM64_REG_X29, a.UC_ARM64_REG_X30)[index - 29]


def fnv(data, skip_checksum=False):
    result = 2166136261
    for index, byte in enumerate(data):
        result = ((result ^ (0 if skip_checksum and 12 <= index < 16 else byte)) * 16777619) & 0xffffffff
    return result


def elf(blob):
    require(blob[:6] == b'\x7fELF\x02\x01' and struct.unpack_from('<H', blob, 18)[0] == 183, 'not little-endian A64 ELF')
    entry, offset = struct.unpack_from('<Q', blob, 24)[0], struct.unpack_from('<Q', blob, 40)[0]
    size, count, names_index = struct.unpack_from('<3H', blob, 58)
    headers = [struct.unpack_from('<IIQQQQIIQQ', blob, offset + i * size) for i in range(count)]
    names_header = headers[names_index]
    names = blob[names_header[4]:names_header[4] + names_header[5]]
    sections = {}
    for header in headers:
        name = names[header[0]:].split(b'\0', 1)[0].decode()
        sections[name] = (header, blob[header[4]:header[4] + header[5]])
    symbols = {}
    for header, data in sections.values():
        if header[1] != 2:
            continue
        strings_header = headers[header[6]]
        strings = blob[strings_header[4]:strings_header[4] + strings_header[5]]
        for at in range(0, len(data), 24):
            name, info, other, index, value, symbol_size = struct.unpack_from('<IBBHQQ', data, at)
            name = strings[name:].split(b'\0', 1)[0].decode()
            if name and index:
                symbols[name] = (value, symbol_size)
    return entry, sections, symbols


def prepared_bmp(target, width, height):
    """Independent, bounded GPT/FAT16 extraction of the pinned prepared artwork."""
    path = ROOT / 'work' / target / 'boot-prefix.img'
    image = path.read_bytes()
    require(image[512:520] == b'EFI PART', 'prepared image has no GPT')
    table_lba, count, size = struct.unpack_from('<QII', image, 584)
    require(count >= 2 and size >= 128, 'prepared GPT cannot describe p2')
    entry = image[table_lba * 512 + size:table_lba * 512 + size * 2]
    first, last = struct.unpack_from('<QQ', entry, 32)
    require(0 < first <= last and (last + 1) * 512 <= len(image), 'p2 extent outside prepared image')
    volume = image[first * 512:(last + 1) * 512]
    get16 = lambda offset: struct.unpack_from('<H', volume, offset)[0]
    spc, reserved, fats, roots, fat_sectors = volume[13], get16(14), volume[16], get16(17), get16(22)
    require(fat_sectors and roots and spc and get16(11) == 512, 'prepared oracle requires FAT16/512')
    root = reserved + fats * fat_sectors
    data = root + (roots * 32 + 511) // 512
    found = None
    for at in range(root * 512, root * 512 + roots * 32, 32):
        item = volume[at:at + 32]
        if not item[0]:
            break
        if item[:11] == b'BOOTLOGOBMP' and not item[11] & 0x18:
            found = item
            break
    require(found is not None, 'prepared p2 has no BOOTLOGO.BMP')
    length = struct.unpack_from('<I', found, 28)[0]
    cluster = struct.unpack_from('<H', found, 26)[0]
    result, seen = bytearray(), set()
    require(54 <= length <= CAP, 'prepared BMP length outside helper capacity')
    while len(result) < length:
        require(2 <= cluster < 0xfff0 and cluster not in seen, 'prepared FAT chain malformed')
        seen.add(cluster)
        sector = data + (cluster - 2) * spc
        require(sector + spc <= last - first + 1, 'prepared FAT chain escapes physical p2')
        result += volume[sector * 512:(sector + spc) * 512]
        cluster = get16(reserved * 512 + cluster * 2)
    require(cluster >= 0xfff8, 'prepared FAT chain not terminal')
    bmp = bytes(result[:length])
    require(bmp[:2] == b'BM' and struct.unpack_from('<I', bmp, 2)[0] == length, 'prepared BMP file size differs')
    return path, bmp, reference_pixels(bmp, width, height)


def reference_pixels(bmp, width, height):
    """Independent physical canvas oracle, including stock's zero padding."""
    stored_width, stored_height = struct.unpack_from('<ii', bmp, 18)
    rows = abs(stored_height)
    require(0 < stored_width <= width and 0 < rows <= height, 'BMP raster exceeds physical canvas')
    require(struct.unpack_from('<HHI', bmp, 26) == (1, 24, 0), 'independent oracle requires uncompressed24bit BMP')
    offset = struct.unpack_from('<I', bmp, 10)[0]
    stride = (stored_width * 3 + 3) & ~3
    require(offset + rows * stride <= len(bmp), 'BMP raster truncated')
    pixels = bytearray(width * height * 4)
    left, top = (width - stored_width) // 2, (height - rows) // 2
    for y in range(rows):
        source_row = y if stored_height < 0 else rows - 1 - y
        for x in range(stored_width):
            source = offset + source_row * stride + x * 3
            destination = ((top + y) * width + left + x) * 4
            pixels[destination:destination + 4] = bmp[source:source + 3] + b'\xff'
    return bytes(pixels)


def smaller_fixture(bmp):
    """Odd-sized real-art crop: exercises centering/padding without a new asset."""
    width, signed_height = struct.unpack_from('<ii', bmp, 18)
    height = abs(signed_height)
    columns, rows = max(1, width - 3), max(1, height - 5)
    source_offset = struct.unpack_from('<I', bmp, 10)[0]
    source_stride, stride = (width * 3 + 3) & ~3, (columns * 3 + 3) & ~3
    result = bytearray(54 + rows * stride)
    result[:2] = b'BM'
    struct.pack_into('<I', result, 2, len(result))
    struct.pack_into('<IIiiHH', result, 10, 54, 40, columns, rows, 1, 24)
    for row in range(rows):
        source_row = row if signed_height > 0 else height - 1 - row
        source = source_offset + source_row * source_stride
        result[54 + row * stride:54 + row * stride + columns * 3] = bmp[source:source + columns * 3]
    return bytes(result)


def vendor_streams():
    """Re-execute all22 vendor panel bodies; byte leaves retain their tested model."""
    path = HERE / 'verify-panel-init.py'
    verifier = module('early_helper_vendor_panels', path)
    contract = json.loads((HERE / 'panel-init.json').read_text())
    policy = json.loads((HERE / 'panel-contract.json').read_text())['models']
    derived = {'version': 1, 'encoding': contract['encoding'], 'initial_reset_delays_ms': [15, 50, 100], 'models': {}, 'streams': {}}
    rows, sources = [], [path, HERE.parent / 'fdt.py']
    for target in policy:
        sources += [ROOT / 'work' / target / 'boot-gzip/boot-package.bin', ROOT / 'experiments/h700-boot-speed/uboot-trace-notes/delays' / f'inventory-{target}.json']
        for high in (True, False):
            row = verifier.run(target, high)
            ops = [value | {'command': 0, 'data': 0x100, 'delay_ms': 0x8000}[kind] for kind, value in row['stream'][3:]]
            digest = sha(b''.join(value.to_bytes(2, 'little') for value in ops))
            derived['streams'][digest] = ops
            model = derived['models'].setdefault(target, {'package_sha256': policy[target]['package_sha256'], 'vendor_uboot_sha256': row['uboot_sha256'], 'old': None, 'boe': None})
            require(model[row['selected_panel']] in (None, digest), 'ambiguous vendor panel stream')
            model[row['selected_panel']] = digest
            rows.append({key: row[key] for key in ('target', 'pe6', 'selected_panel', 'uboot_sha256', 'stream_sha256')})
    require(len(rows) == 22 and derived == contract, 'all22 actual vendor streams differ from panel-init.json')
    return rows, sources


class Replay:
    def __init__(self, blob, symbols, model, policy, generation, bmp, pixels):
        self.blob, self.symbols, self.model, self.policy = blob, symbols, model, policy
        self.generation, self.bmp, self.pixels = generation, bmp, pixels
        self.disassembler = c.Cs(c.CS_ARCH_ARM64, c.CS_MODE_ARM)
        self.disassembler.detail = True
        self.instructions = {ins.address: ins for ins in self.disassembler.disasm(blob, HELPER)}
        self.apply = symbols['apply'][0]
        self.wait = symbols['wait_us'][0]
        self.returns = {}
        for ins in self.instructions.values():
            if ins.mnemonic == 'bl' and ins.operands[0].imm in (self.apply, self.wait):
                self.returns[ins.address + 4] = 'apply' if ins.operands[0].imm == self.apply else 'wait'
        for phase in PHASES:
            address, size = symbols['baseos_display_' + phase]
            expected = b''.join(struct.pack('<5I', KINDS[op['kind']], *(op[key] for key in FIELDS)) for op in model[phase])
            require(size == len(expected) and blob[address - HELPER:address - HELPER + size] == expected, f'compiled {phase} operations differ from semantic oracle')

    def run(self, name, high=True, line=64, fault=None):
        # Approved host architectural model: a frozen counter read lowers to
        # MOVZ24000, avoiding100million Python callbacks. Every loop/control
        # instruction stays byte-identical; the actual finite guard executes.
        emulated = bytearray(self.blob)
        lowerings = []
        if fault == 'counter-stuck':
            for address, ins in self.instructions.items():
                if ins.mnemonic != 'mrs' or not ins.op_str.endswith(', cntpct_el0'):
                    continue
                register = int(ins.op_str.split(', ')[0][1:])
                replacement = struct.pack('<I', 0xd2800000 | (24000 << 5) | register)
                decoded = next(self.disassembler.disasm(replacement, address))
                require(decoded.mnemonic in ('mov', 'movz') and decoded.operands[1].imm == 24000, 'constant counter lowering differs')
                offset = address - HELPER
                before = bytes(emulated[offset:offset + 4])
                emulated[offset:offset + 4] = replacement
                lowerings.append(dict(address=hex(address), before=before.hex(), after=replacement.hex(), architectural_value=24000))
            allowed = {int(row['address'], 16) - HELPER + offset for row in lowerings for offset in range(4)}
            require(lowerings and all(index in allowed for index, (old, new) in enumerate(zip(self.blob, emulated)) if old != new), 'counter model escaped MRS instructions')
        m = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
        m.ctl_set_cpu_model(a.UC_CPU_ARM64_A53)
        m.mem_map(HELPER, HELPER_CAP)
        m.mem_write(HELPER, bytes(emulated))
        m.mem_protect(HELPER, HELPER_CAP, u.UC_PROT_READ | u.UC_PROT_EXEC)
        m.mem_map(RECORD & ~0xfff, 0x1000)
        m.mem_write(RECORD & ~0xfff, b'\xa5' * 0x1000)
        PIXELS = 0x80000000 - self.policy['boot_logo']['framebuffer_from_ram_end']
        page = PIXELS & ~4095
        m.mem_map(page, (PIXELS + CAP - page + 4095) & ~4095)
        m.mem_write(PIXELS, b'\xa5' * CAP)
        m.mem_map(BMP, CAP)
        m.mem_write(0x4a300010, struct.pack('<Q', 0x40000000))
        m.mem_map(0x4e000000, 0x10000)
        m.mem_map(STOP, 0x1000)
        source = bytearray(self.bmp)
        magic, reserved, logo_bytes = 0x31474f4c, 0, len(source)
        if fault == 'bad-bmp': source[0] ^= 1
        if fault == 'wrong-dimensions': struct.pack_into('<I', source, 18, self.policy['boot_logo']['width'] + 1)
        if fault == 'bad-magic': magic = 0
        if fault == 'bad-reserved': reserved = 1
        if fault == 'zero-length': logo_bytes = 0
        if fault == 'oversized': logo_bytes = CAP + 1
        if fault == 'short-bmp': logo_bytes = 53
        logo_fnv = fnv(source[:logo_bytes])
        if fault == 'bad-fnv': logo_fnv ^= 1
        descriptor = struct.pack('<4I', magic, logo_bytes, logo_fnv, reserved)
        m.mem_write(BMP, bytes(source))
        m.mem_write(DESC, descriptor)
        m.mem_protect(BMP, CAP, u.UC_PROT_READ)
        pages = {0x03000000, 0x0300b000, 0x03009000, 0x07000000, 0x04020000, SCANLINE & ~0xfff}
        for phase in PHASES:
            pages.update(op['address'] & ~0xfff for op in self.model[phase] if op['kind'] not in ('delay_us', 'barrier'))
        for page in pages: m.mem_map(page, 0x1000)
        preserved = {index: 0x1973000000000000 + index * 0x102030405 for index in range(18, 30)}
        for index, value in preserved.items(): m.reg_write(xreg(index), value)
        m.reg_write(a.UC_ARM64_REG_SP, STACK)
        m.reg_write(a.UC_ARM64_REG_X0, DESC)
        m.reg_write(a.UC_ARM64_REG_X30, STOP)
        registers = {CFG: 0x22345678, DAT: 0xa5a5e7a5, 0x03000000: 0xf1234567, 0x03000004: 0x89abcdef, 0x030017bc: 0xa5a55a5a, 0x030f0030: 0xc369a59a}
        seq, clock, last_dsb, last_counter = 0, 24000, 0, 0
        clean, pixel_writes, last_pixel, last_clean = [], 0, 0, 0
        first_mmio, magic_publication, frame = None, None, None
        iommu_gate, iommu_bypass, iommu_ack = 0, 0, 0
        timeline, frames, phases, delays, scanlines = [], [], [], [], []
        panel_gpio, strap_observations = [], []
        mmio_reads, mmio_writes, phase_writes, barrier_counts = [], [], [], {'dmb': 0, 'dsb': 0, 'isb': 0}
        active, write_queue, pending_wait = None, [], None
        poll_ops = [op for phase in PHASES for op in self.model[phase] if op['kind'] == 'poll']
        permitted_reads = {0x03000000, 0x03000004, CFG, DAT, STRAP, 0x0300b090, 0x0300b0ac, SCANLINE}
        permitted_reads.update(op['address'] for phase in PHASES for op in self.model[phase] if op['kind'] not in ('delay_us', 'barrier'))
        failed_poll = poll_ops[0] if fault == 'poll-timeout' and poll_ops else None
        expected_panel = self.policy['panel_contract']['high' if high else 'low']
        panel = int(expected_panel == 'boe')
        pixel_end = PIXELS + len(self.pixels)

        def next_event():
            nonlocal seq
            seq += 1
            return seq

        def clean_complete():
            require(clean == list(range(PIXELS, pixel_end, line)), 'cache CVAC coverage missing/duplicated/outside pixels')
            require(last_pixel < clean_start[0] <= last_clean < last_dsb, 'pixel stores/CVAC/DSB order differs')

        def panel_ready():
            if len(panel_gpio) < 4:
                return False
            for index, (mask, bits) in enumerate(((0x8000, 0x8000), (0x4000, 0x4000), (0x4000, 0), (0x4000, 0x4000))):
                event = panel_gpio[index]
                if event['value'] != (event['old'] & ~mask) | bits:
                    return False
            moments = [event['ticks'] for event in panel_gpio[:4]]
            if any(moments[index + 1] - moments[index] < delay * 24 for index, delay in enumerate((30000, 15000, 50000))):
                return False
            if clock - moments[-1] < 100000 * 24:
                return False
            cfg, dat = registers.get(CFG, 0), registers.get(DAT, 0)
            return (cfg >> 24 & 0xff) == 0x11 and dat & 0xc000 == 0xc000

        clean_start = []
        def mmio_read(uc, access, address, size, value, context):
            nonlocal clock, iommu_ack
            next_event()
            require(size == 4, 'MMIO read is not32bit')
            mmio_reads.append([seq, hex(address)])
            require(address in permitted_reads, f'unowned MMIO read {address:#x}')
            old = registers.get(address, 0x5a5a5a5a)
            if 0x030f0000 <= address < 0x030f1000:
                require(registers[0x030017bc] & 1 and iommu_gate and last_dsb > iommu_gate, 'IOMMU read before CCU gate/DSB')
            if address == STRAP:
                ready = panel_ready() and (registers.get(0x0300b090, 0) >> 24 & 15) == 6
                if self.policy['panel_pull_up']:
                    ready = ready and registers.get(0x0300b0ac, 0) & 0x3000 == 0x1000
                strap_observations.append(dict(sequence=seq, ticks=clock, powered_reset_mux_pull_ready=ready))
                old = old & ~0x40 | (0x40 if high and ready else 0)
            elif address == SCANLINE:
                value = 31 if fault == 'scanline-stuck' else 31 + len(scanlines)
                old = old & ~0x0fff0000 | (value & 0xfff) << 16
                scanlines.append((seq, value & 0xfff))
            elif 0x03001000 <= address < 0x03001100:
                # Native SPL already supplies vendor PLLpower31. LOCK28 is a
                # readiness model, not a measured hardware lock interval.
                old |= 0x90000000
            for op in poll_ops:
                if address == op['address']:
                    if address not in (0x030017bc, 0x030f0030):
                        old = old & ~op['clear_mask'] | op['set_bits']
                    if op is failed_poll:
                        bit = op['clear_mask'] & -op['clear_mask']
                        old = old & ~op['clear_mask'] | (op['set_bits'] ^ bit)
            if address == 0x030f0030 and iommu_bypass and old & 1:
                require(last_dsb > iommu_bypass, 'IOMMU acknowledgment before bypass DSB')
                iommu_ack = seq
            registers[address] = old
            uc.mem_write(address, struct.pack('<I', old))

        def mmio_write(uc, access, address, size, value, context):
            nonlocal first_mmio, frame, iommu_gate, iommu_bypass
            next_event()
            require(size == 4, 'MMIO write is not32bit')
            require(not (0x07000000 <= address < 0x07001000 or 0x04020000 <= address < 0x04021000 or 0x03009000 <= address < 0x0300a000), 'SD/RTC/watchdog write')
            if first_mmio is None:
                clean_complete()
                first_mmio = seq
            old = registers.get(address, 0x5a5a5a5a)
            if 0x030f0000 <= address < 0x030f1000:
                require(registers[0x030017bc] & 1 and iommu_gate and last_dsb > iommu_gate, 'IOMMU write before CCU gate/DSB')
            if 0x01000000 <= address < 0x02400000:
                require(registers[0x030f0030] & 1 and iommu_ack > iommu_bypass > iommu_gate, 'DE write before ordered IOMMU bypass/readback')
            if address == 0x030017bc and value & 1:
                require((old ^ value) & ~1 == 0, 'IOMMU gate changed unrelated bits')
                iommu_gate = seq
            if address == 0x030f0030 and value & 1:
                require((old ^ value) & ~1 == 0, 'IOMMU bypass changed unrelated bits')
                iommu_bypass = seq
            if active:
                require(write_queue, f'unexpected {active} MMIO write')
                op = write_queue.pop(0)
                expected = (old & ~op['clear_mask']) | op['set_bits'] if op['kind'] == 'update' else op['set_bits'] + (PIXELS if op['kind'] == 'framebuffer' else 0)
                require(address == op['address'] and value == expected, f'{active} semantic MMIO mismatch at {address:#x}')
                phase_writes.append([active, hex(address), hex(value)])
            elif address in (0x03000000, 0x03000004):
                mask = 0x7fffffff if address == 0x03000000 else 0x01000000
                require(value == old & ~mask, 'SRAM bridge release mismatch')
            else:
                require(address in (CFG, DAT, 0x0300b090, 0x0300b0ac), f'unowned panel MMIO write {address:#x}')
            if address == DAT and not active:
                panel_gpio.append(dict(sequence=seq, ticks=clock, old=old, value=value))
                if len(panel_gpio) == 5:
                    require(panel_ready(), 'SPI started before195ms powered panel reset')
                require((old ^ value) & ~0xc700 == 0, 'panel DAT write changed unrelated bits')
                if frame is None and not value & 0x100: frame = []
                if frame is not None and not old & 0x200 and value & 0x200 and not value & 0x100: frame.append(int(bool(value & 0x400)))
                if frame is not None and value & 0x100:
                    if frame:
                        require(len(frame) == 9, 'partial or overlong SPI9 frame')
                        byte = sum(bit << (7 - index) for index, bit in enumerate(frame[1:]))
                        event = ['data' if frame[0] else 'command', byte]
                        frames.append(event); timeline.append(event)
                    frame = None
            registers[address] = value
            mmio_writes.append([seq, hex(address), hex(value)])

        def ram_write(uc, access, address, size, value, context):
            nonlocal pixel_writes, last_pixel, magic_publication
            next_event()
            if PIXELS <= address < PIXELS + CAP:
                require(address + size <= pixel_end, 'pixel write outside physical raster')
                require(not clean, 'pixel write after cache cleaning started')
                pixel_writes += 1; last_pixel = seq
            else:
                require(RECORD <= address and address + size <= RECORD + 80, 'main/logo/adjacent record modified')
                if address == RECORD and size == 4 and value == 0x31505344:
                    require(len(phases) == 4 and phases[-1]['returned'] == 0 and phases[-1]['phase'] == 'backlight', 'ready published before backlight completes')
                    require(len({line for _, line in scanlines}) >= 2, 'ready published without advancing TCON')
                    clean_complete(); magic_publication = seq

        def code(uc, address, size, context):
            nonlocal active, write_queue, pending_wait, clock, last_dsb, last_clean, last_counter
            next_event()
            if address == self.apply:
                require(active is None, 'nested apply')
                index = len(phases); require(index < 4, 'extra apply phase')
                active = PHASES[index]
                pointer, count = uc.reg_read(a.UC_ARM64_REG_X0), uc.reg_read(a.UC_ARM64_REG_X1)
                require((pointer, count) == (self.symbols['baseos_display_' + active][0], len(self.model[active])), 'apply table ABI differs')
                if active == 'backlight': require(len({line for _, line in scanlines}) >= 2, 'backlight before scanline readiness')
                write_queue = [op for op in self.model[active] if op['kind'] in ('update', 'write', 'framebuffer')]
                phases.append({'phase': active, 'sequence': seq, 'returned': None})
            elif address == self.wait:
                require(pending_wait is None, 'nested delay')
                microseconds = uc.reg_read(a.UC_ARM64_REG_X0)
                pending_wait = {'microseconds': microseconds, 'start': clock, 'sequence': seq}
                timeline.append(['wait_us', microseconds])
            elif address in self.returns:
                result = struct.unpack('<i', struct.pack('<I', uc.reg_read(a.UC_ARM64_REG_X0) & 0xffffffff))[0]
                if self.returns[address] == 'apply':
                    require(active is not None, 'apply return without phase')
                    if result == 0: require(not write_queue, 'apply omitted semantic writes')
                    phases[-1]['returned'] = result; active = None
                else:
                    require(pending_wait is not None, 'delay return without entry')
                    pending_wait.update(returned=result, end=clock)
                    if result == 0: require(clock - pending_wait['start'] >= pending_wait['microseconds'] * 24, 'delay returned too early')
                    delays.append(pending_wait); pending_wait = None
            ins = self.instructions.get(address)
            if not ins: return
            if ins.mnemonic == 'mrs':
                target, source = ins.op_str.split(', ')
                require(source in ('cntpct_el0', 'ctr_el0'), 'unmodeled system register ' + source)
                if source == 'ctr_el0': value = ((line.bit_length() - 3) << 16)
                else:
                    value = clock
                    last_counter = value
                    if fault != 'counter-stuck': clock += 24000
                uc.reg_write(xreg(int(target[1:])), value); uc.reg_write(a.UC_ARM64_REG_PC, address + 4)
            elif ins.mnemonic == 'dc':
                operation, target = ins.op_str.split(', ')
                require(operation == 'cvac', 'unmodeled cache operation')
                if not clean_start: clean_start.append(seq)
                last_clean = seq
                clean.append(uc.reg_read(xreg(int(target[1:])))); uc.reg_write(a.UC_ARM64_REG_PC, address + 4)
            elif ins.mnemonic in barrier_counts:
                barrier_counts[ins.mnemonic] += 1
                if ins.mnemonic == 'dsb': last_dsb = seq
            elif ins.mnemonic in ('msr', 'ic', 'tlbi', 'smc', 'hvc', 'wfi'):
                raise RuntimeError('unmodeled privileged instruction ' + ins.mnemonic)

        m.hook_add(u.UC_HOOK_MEM_READ, mmio_read, begin=0x01000000, end=0x0fffffff)
        m.hook_add(u.UC_HOOK_MEM_WRITE, mmio_write, begin=0x01000000, end=0x0fffffff)
        m.hook_add(u.UC_HOOK_MEM_WRITE, ram_write, begin=PIXELS, end=PIXELS + CAP - 1)
        m.hook_add(u.UC_HOOK_MEM_WRITE, ram_write, begin=RECORD & ~0xfff, end=(RECORD & ~0xfff) + 0xfff)
        special = {address for address, ins in self.instructions.items() if ins.mnemonic in ('mrs', 'msr', 'dc', 'ic', 'tlbi', 'dmb', 'dsb', 'isb', 'smc', 'hvc', 'wfi')}
        if fault == 'counter-stuck':
            special -= {int(row['address'], 16) for row in lowerings}
        for address in special | {self.apply, self.wait} | set(self.returns):
            m.hook_add(u.UC_HOOK_CODE, code, begin=address, end=address)
        m.emu_start(HELPER, STOP, count=1200000000 if fault == 'counter-stuck' else 100000000)
        require(m.reg_read(a.UC_ARM64_REG_PC) == STOP, 'helper exceeded instruction budget')
        result = struct.unpack('<i', struct.pack('<I', m.reg_read(a.UC_ARM64_REG_X0) & 0xffffffff))[0]
        require(m.reg_read(a.UC_ARM64_REG_SP) == STACK and all(m.reg_read(xreg(index)) == value for index, value in preserved.items()), 'A64 ABI x18/x19..29/SP damaged')
        require(bytes(m.mem_read(DESC, 16)) == descriptor, 'raw logo descriptor modified')
        require(bytes(m.mem_read(BMP, len(source))) == bytes(source), 'source BMP modified')
        record = bytes(m.mem_read(RECORD, 80))
        bad = fault in ('bad-bmp', 'wrong-dimensions', 'bad-magic', 'bad-reserved', 'zero-length', 'oversized', 'short-bmp', 'bad-fnv')
        if bad:
            require(result == -2 and not mmio_reads and not mmio_writes and record == bytes(80) and not clean and not pixel_writes, 'optional malformed logo changed hardware/pixels or published status')
        elif fault:
            require(result == -1 and record == bytes(80) and magic_publication is None, 'bounded readiness/delay failure published ready')
            if fault == 'counter-stuck': require(delays and delays[-1]['returned'] == -1, 'counter stall did not hit finite delay bound')
        else:
            require(result == panel and magic_publication is not None, 'helper did not publish selected panel')
            require(iommu_ack > iommu_bypass > iommu_gate > 0, 'ready record without IOMMU gate/bypass/readback')
            require(panel_ready() and all(item['powered_reset_mux_pull_ready'] for item in strap_observations), 'PE6 sampled before powered/reset/mux/pull prerequisites')
            require(bool(strap_observations) == (self.policy['panel_rule'] == 2), 'strap access differs from static/dynamic policy')
            require(bytes(m.mem_read(PIXELS, len(self.pixels))) == self.pixels, 'ARGB raster differs from independent BMP conversion')
            require(bytes(m.mem_read(pixel_end, CAP - len(self.pixels))) == b'\xa5' * (CAP - len(self.pixels)), 'pixel tail guard modified')
            clean_complete()
            magic, version, length, checksum, chosen, length_bmp, hash_bmp, hash_pixels = struct.unpack_from('<8I', record)
            require((magic, version, length, chosen, length_bmp, hash_bmp, hash_pixels) == (0x31505344, 1, 80, panel, len(source), fnv(source), fnv(self.pixels)), 'ready record fields differ')
            require(checksum == fnv(record, True) and record[32:64] == self.generation and struct.unpack_from('<Q', record, 64)[0] == last_counter != 0 and record[72:] == bytes(8), 'ready checksum/generation/timing/reserved differs')
            require([phase['phase'] for phase in phases] == list(PHASES) and all(phase['returned'] == 0 for phase in phases), 'display phase ordering differs')
            artwork = json.loads((HERE / 'panel-init.json').read_text())
            digest = artwork['models'][self.policy['target']][expected_panel]
            stream = artwork['streams'][digest]
            expected_timeline = [['wait_us', op['set_bits']] for op in self.model['prepare'] if op['kind'] == 'delay_us']
            expected_timeline += [['wait_us', value] for value in (30000, 15000, 50000, 100000)]
            expected_timeline += [['wait_us', (op & 0x7fff) * 1000] if op & 0x8000 else ['data' if op & 0x100 else 'command', op & 255] for op in stream]
            expected_timeline += [['wait_us', 50000]]
            for phase in ('publish', 'enable'):
                expected_timeline += [['wait_us', op['set_bits']] for op in self.model[phase] if op['kind'] == 'delay_us']
            expected_timeline += [['wait_us', 100000]]
            expected_timeline += [['wait_us', op['set_bits']] for op in self.model['backlight'] if op['kind'] == 'delay_us']
            require(timeline == expected_timeline, 'actual SPI9 edges/delays differ from vendor stream and phase contract')
        row = dict(name=name, passed=True, unmodified_helper_sha256=sha(self.blob), host_emulated_code_sha256=sha(bytes(emulated)), architectural_counter_lowerings=lowerings, frozen_counter_guard_iterations=100000000 if fault == 'counter-stuck' else None, result=result, pe6='high' if high else 'low', panel=expected_panel, ctr_line_bytes=line, fault=fault, x18_x19_to29_sp_preserved=True, record_hex=record.hex(), record_sha256=sha(record), pixel_writes=pixel_writes, cvac=dict(count=len(clean), first=hex(clean[0]) if clean else None, last=hex(clean[-1]) if clean else None, addresses_sha256=sha(b''.join(struct.pack('<Q', value) for value in clean))), phase_calls=phases, semantic_writes=phase_writes, iommu_order=dict(gate_sequence=iommu_gate, bypass_sequence=iommu_bypass, readback_sequence=iommu_ack, initial_master0='0xc369a59a', final_master0=hex(registers[0x030f0030])), panel_power_reset_events=panel_gpio[:4], strap_observations=strap_observations, gpio_frames=len(frames), frame_sha256=sha(json.dumps(frames, separators=(',', ':')).encode()), mmio_read_count=len(mmio_reads), mmio_write_count=len(mmio_writes), barriers=barrier_counts, scanline_reads=len(scanlines), distinct_scanlines=len({line for _, line in scanlines}), counter_model='CNTPCT advances24000ticks/read (1ms at24MHz); singlefaultcase lowers counter MRS toMOVZ24000, all compiled loop/control instructions unchanged', first_mmio_sequence=first_mmio, magic_publication_sequence=magic_publication, SD_RTC_WDT_writes=0)
        print(json.dumps({key: row[key] for key in ('name', 'passed', 'result', 'mmio_write_count', 'gpio_frames')}), flush=True)
        return row, record


def verify(native_dir, output, include_counter_fault=True):
    require(__debug__, 'run without Python -O: vendor proof assertions must remain enabled')
    require(not output.exists() and not output.is_symlink(), 'use a fresh output directory')
    directory = native_dir / 'early-display'
    manifest = json.loads((directory / 'manifest.json').read_text())
    native = json.loads((native_dir / 'manifest.json').read_text())
    blob, elf_blob = (directory / 'helper.bin').read_bytes(), (directory / 'helper.elf').read_bytes()
    require(manifest['target'] == native['target'] and manifest['generation'] == native['generation'], 'helper/native identity differs')
    require(manifest['address'] == HELPER and manifest['bytes'] == len(blob) and 0 < len(blob) <= HELPER_CAP and len(blob) % 512 == 0 and sha(blob) == manifest['sha256'], 'helper extent/SHA differs')
    entry, sections, symbols = elf(elf_blob)
    require(entry == HELPER and symbols['baseos_early_display'][0] == HELPER, 'helper ABI entry differs')
    text_header, text = sections['.text']
    require(text_header[3] == HELPER and blob[:len(text)] == text and not any(blob[len(text):]), 'ELF text/bin differs')
    require(not sections.get('.bss', ([0] * 10, b''))[0][5], 'helper unexpectedly requires BSS')
    engine_path, panel_path = HERE / 'display-engine.json', HERE / 'panel-init.json'
    engine, panel = json.loads(engine_path.read_text()), json.loads(panel_path.read_text())
    require(sha(engine_path.read_bytes()) == manifest['engine_contract_sha256'] and sha(panel_path.read_bytes()) == manifest['panel_contract_sha256'], 'helper contract pins differ')
    model = engine['models'][native['target']]
    require(model['vendor_dtb_sha256'] == manifest['vendor_dtb_sha256'], 'semantic source DTB pin differs')
    for name in ('native-logo.h', 'native-display.h'):
        require((directory / name).read_bytes() == (HERE.parent / 'spl' / name).read_bytes(), 'helper header differs from canonical ' + name)
    width, height = native['boot_logo']['width'], native['boot_logo']['height']
    source, bmp, pixels = prepared_bmp(native['target'], width, height)
    require(len(pixels) <= CAP, 'physical raster exceeds owned arena')
    vendor_rows, vendor_sources = vendor_streams()
    sources = [Path(__file__).resolve(), HERE / 'early-display.c', HERE / 'build-display.py', HERE / 'runtime.c', HERE / 'boot-logo-pixels.h', HERE / 'panel-spi9.h', engine_path, panel_path, HERE / 'panel-contract.json', HERE.parent / 'spl/native-logo.h', HERE.parent / 'spl/native-display.h', source] + vendor_sources
    source_hashes = {str(path.relative_to(ROOT)): sha(path.read_bytes()) for path in sources}
    inputs = {str(path.relative_to(native_dir)): sha(path.read_bytes()) for path in directory.rglob('*') if path.is_file()}
    inputs['manifest.json'] = sha((native_dir / 'manifest.json').read_bytes())
    output.mkdir(parents=True)
    generation = bytes.fromhex(native['generation'])
    require(len(generation) == 32, 'native generation must contain32bytes')
    replay = Replay(blob, symbols, model, native, generation, bmp, pixels)
    cases, records = [], {}
    crop = smaller_fixture(bmp)
    crop_pixels = reference_pixels(crop, width, height)
    center_inputs = [('centered-odd-real-art-crop', crop, crop_pixels)]
    if width >= 640 and height >= 480 and (width > 640 or height > 480):
        center_source, center_bmp, center_pixels = prepared_bmp('rgsp', width, height)
        source_hashes[str(center_source.relative_to(ROOT))] = sha(center_source.read_bytes())
        center_inputs.append(('centered-actual-rgsp-p2', center_bmp, center_pixels))
    for line in (16, 64, 256):
        for high in (True, False):
            name = f'valid-{line}-' + ('high' if high else 'low')
            row, record = replay.run(name, high=high, line=line)
            cases.append(row); records[name] = record
    for name, artwork, canvas in center_inputs:
        centered = Replay(blob, symbols, model, native, generation, artwork, canvas)
        row, record = centered.run(name, high=False)
        row.update(fixture_sha256=sha(artwork), fixture_bytes=len(artwork), expected_pixels_sha256=sha(canvas))
        cases.append(row); records[name] = record
    faults = ['bad-bmp', 'wrong-dimensions', 'bad-magic', 'bad-reserved', 'zero-length', 'oversized', 'short-bmp', 'bad-fnv', 'scanline-stuck']
    if any(op['kind'] == 'poll' for phase in PHASES for op in model[phase]): faults += ['poll-timeout']
    if include_counter_fault: faults += ['counter-stuck']
    for fault in faults:
        row, record = replay.run(fault, fault=fault)
        cases.append(row); records[fault] = record
    require(all(sha((ROOT / path).read_bytes()) == digest for path, digest in source_hashes.items()), 'source changed during replay')
    require(all(sha((native_dir / path).read_bytes()) == digest for path, digest in inputs.items()), 'helper input changed during replay')
    for name, record in records.items(): (output / (name + '-record.bin')).write_bytes(record)
    (output / 'display-pixels.bin').write_bytes(pixels)
    display_record = records['valid-64-low']
    (output / 'display-record.bin').write_bytes(display_record)
    report = dict(passed=include_counter_fault, required_cases_complete=include_counter_fault, verifier_sha256=sha(Path(__file__).read_bytes()), fixture_sha256=sha(bmp), display_record_file='display-record.bin', display_record_sha256=sha(display_record), display_pixels_file='display-pixels.bin', display_pixels_sha256=sha(pixels), shown_ticks=struct.unpack_from('<Q', display_record, 64)[0], target=native['target'], generation=native['generation'], helper_sha256=sha(blob), helper_bytes=len(blob), source_hashes=source_hashes, input_hashes=inputs, prepared_bmp_sha256=sha(bmp), prepared_bmp_bytes=len(bmp), expected_pixels_sha256=sha(pixels), expected_pixel_bytes=len(pixels), vendor_contract_cases=vendor_rows, case_count=len(cases), cases=cases, counter_stall_full_bound_executed=include_counter_fault, scope='actual linked A64 helper; explicit CNTPCT/CTR/CVAC/PLL/poll/TCON register models; actual SPI9 edges and all22 vendor panel-body contract replays', limitations=['No panel/electrical timing, PLL lock-time, bus bandwidth or visual hardware qualification.', 'DC CVAC is modeled as a recorded clean-to-PoC event; cache-line coverage/order and DSB are verified, not physical coherency.', 'Status cache publication to later CPUs relies on the SPL subsequent full cache clean; this helper proof does not execute that caller.', 'Semantic DE tables are checked against the separately derived source oracle; display hardware side effects are not inferred from zero-filled registers.'], device_access=False, hardware_qualified=False)
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    (output / 'verify-early-display.py').write_bytes(Path(__file__).read_bytes())
    pins = {str(path.relative_to(output)): sha(path.read_bytes()) for path in output.rglob('*') if path.is_file() and path.name != 'seal.json'}
    (output / 'seal.json').write_text(json.dumps(dict(passed=True, artifacts=pins, source_hashes=source_hashes, inputs=inputs), indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('native_dir', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--skip-counter-stall', action='store_true', help='debug only: omit frozen-counter bound; report passed=false/incomplete')
    args = parser.parse_args()
    report = verify(args.native_dir.resolve(), args.output.absolute(), not args.skip_counter_stall)
    print(json.dumps(dict(passed=report['passed'], target=report['target'], cases=report['case_count'], output=str(args.output))))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, u.UcError) as error:
        raise SystemExit(f'early-display proof refused: {error}')
