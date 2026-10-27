#!/usr/bin/env python3
"""Execute the linked A32 loader, decoder, libfdt and final kernel-call setup."""
import argparse
import hashlib
import importlib.util
import json
import re
import struct
import sys
from pathlib import Path

import capstone as c
import unicorn as u
from unicorn import arm_const as a

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fdt


def fnv(data):
    value = 2166136261
    for byte in data: value = (value ^ byte) * 16777619 & 0xffffffff
    return value


def symbols(directory):
    return {name: int(address, 16) for address, name in re.findall(
        r"^\s*([0-9a-f]+)\s+[0-9a-f]+\s+[0-9a-f]+\s+\d+\s+(\w+)(?:\s*=.*)?\s*$",
        (directory / "loader.map").read_text(), re.M)}


def fdt_reservations(blob):
    cursor = struct.unpack_from(">I", blob, 16)[0]
    result = []
    while True:
        assert cursor + 16 <= len(blob), "unterminated FDT reservation map"
        entry = struct.unpack_from(">2Q", blob, cursor)
        cursor += 16
        if entry == (0, 0): return result
        result.append(entry)


def logo_pixels(bmp, width, height):
    """Independent BMP reference with stock's zero-filled canvas padding."""
    offset = struct.unpack_from("<I", bmp, 10)[0]
    columns = struct.unpack_from("<i", bmp, 18)[0]
    stored_height = struct.unpack_from("<i", bmp, 22)[0]
    rows = abs(stored_height)
    assert 0 < columns <= width and 0 < rows <= height
    stride = (columns * 3 + 3) & ~3
    assert offset + stride * rows <= len(bmp)
    left, top = (width - columns) // 2, (height - rows) // 2
    pixels = bytearray(width * height * 4)
    for y in range(rows):
        row_index = y if stored_height < 0 else rows - 1 - y
        row = bmp[offset + row_index * stride:offset + row_index * stride + columns * 3]
        start = ((top + y) * width + left) * 4
        end = start + columns * 4
        for channel in range(3): pixels[start + channel:end:4] = row[channel::3]
        pixels[start + 3:end:4] = bytes([255]) * columns
    return bytes(pixels)


def verify_logo(machine, sym, manifest, tree, directory):
    assert "baseos_prepare_logo" in sym, "missing noinline linked logo producer"
    root = Path(__file__).resolve().parents[3]
    fixture_path = root / "work" / manifest["target"] / "bootlogo.bmp"
    bmp = fixture_path.read_bytes()
    width, height = manifest["boot_logo"]["width"], manifest["boot_logo"]["height"]
    assert bmp[:2] == b"BM" and struct.unpack_from("<I", bmp, 2)[0] == len(bmp)
    source_width, source_height, planes, bpp, compression = struct.unpack_from("<iiHHI", bmp, 18)
    assert 0 < source_width <= width and 0 < abs(source_height) <= height
    assert (planes, bpp, compression) == (1, 24, 0)
    capacity, descriptor_address, bmp_address, pixel_address = 0x400000, 0x4a300200, 0x4b400000, 0x40000000 + struct.unpack("<Q", machine.mem_read(0x4a300010, 8))[0] - manifest["boot_logo"]["framebuffer_from_ram_end"]
    assert 0 < len(bmp) <= capacity and 0 < width * height * 4 <= capacity
    tree_address, stop = 0x4a400000, 0x4a0d0000
    assert len(tree) == 0x100000
    original_properties = {(p, n): v for p, n, _, v in fdt.walk(tree)}
    display = "/soc@03000000/disp@01000000"
    previous_fb = original_properties.get((display, "boot_fb0"), b"")
    assert previous_fb == bytes(len(previous_fb))
    original_reservations = fdt_reservations(tree)
    owned_reservation = (pixel_address, width * height * 4)
    assert owned_reservation not in original_reservations
    description = ",".join(f"{v:x}" for v in (pixel_address, width, height, 32, width * 4, 0, 0, width, height))
    expected_pixels = logo_pixels(bmp, width, height)
    sentinel = bytes([0xa5]) * capacity
    cases = []
    stop_hook = machine.hook_add(u.UC_HOOK_CODE, lambda m, pc, size, data: m.emu_stop(), begin=stop, end=stop)

    def execute(name, payload=bmp, descriptor=None, expected=-1, input_tree=tree):
        desc = descriptor if descriptor is not None else struct.pack("<4I", 0x31474f4c, len(payload), fnv(payload), 0)
        assert len(desc) == 16 and len(payload) <= capacity
        machine.mem_write(tree_address, input_tree)
        machine.mem_write(descriptor_address, desc)
        machine.mem_write(bmp_address, payload)
        machine.mem_write(pixel_address, sentinel)
        def call(symbol, argument):
            machine.reg_write(a.UC_ARM_REG_CPSR, 0x1d3)
            machine.reg_write(a.UC_ARM_REG_R0, argument)
            machine.reg_write(a.UC_ARM_REG_SP, 0x4a0f0000)
            machine.reg_write(a.UC_ARM_REG_LR, stop)
            machine.emu_start(sym[symbol], 0x50000000, count=100000000)
            assert machine.reg_read(a.UC_ARM_REG_PC) == stop, (name, symbol, "did not return")
            result = machine.reg_read(a.UC_ARM_REG_R0)
            return result - (1 << 32) if result & 0x80000000 else result
        result = call("baseos_prepare_pixels", descriptor_address)
        if result == 1:
            # This helper test models readiness. The complete loader verifier executes
            # the real panel/DE/cache sequence and proves this record's publication.
            native = bytes(machine.mem_read(0x4a300000, 240))
            shown = struct.unpack_from("<Q", native, 40)[0]
            status = bytearray(struct.pack("<8I32sQ2I", 0x31505344, 1, 80, 0,
                              0 if manifest["panel_rule"] == 0 else 1, len(payload), fnv(payload),
                              fnv(logo_pixels(payload, width, height)), bytes.fromhex(manifest["generation"]), shown, 0, 0))
            struct.pack_into("<I", status, 12, fnv(status))
            machine.mem_write(0x4a300300, bytes(status))
            result = call("baseos_prepare_logo", tree_address)
        assert result == expected, (name, result, expected)
        actual_tree = bytes(machine.mem_read(tree_address, len(tree)))
        actual_pixels = bytes(machine.mem_read(pixel_address, capacity))
        assert bytes(machine.mem_read(bmp_address, len(payload))) == payload, (name, "source BMP changed")
        assert bytes(machine.mem_read(descriptor_address, 16)) == desc, (name, "descriptor changed")
        row = dict(name=name, return_value=result, descriptor_sha256=hashlib.sha256(desc).hexdigest(),
                   bmp_sha256=hashlib.sha256(payload).hexdigest(), bmp_bytes=len(payload))
        if expected == -1 and not name.startswith("fdt-"):
            assert actual_tree == input_tree and actual_pixels == sentinel, (name, "refusal changed tree or pixels")
            row.update(tree_unchanged=True, pixels_unchanged=True)
        elif expected == 1:
            reference = logo_pixels(payload, width, height)
            assert actual_pixels[:len(reference)] == reference and actual_pixels[len(reference):] == sentinel[len(reference):], (name, "pixel conversion/capacity differs")
            props = {(p, n): v for p, n, _, v in fdt.walk(actual_tree)}
            changed = {key for key in original_properties.keys() | props.keys() if original_properties.get(key) != props.get(key)}
            assert changed == {(display, "boot_fb0")}, (name, changed)
            columns, stored_rows = struct.unpack_from("<ii", payload, 18)
            rows = abs(stored_rows)
            left, top = (width - columns) // 2, (height - rows) // 2
            fields = (pixel_address, width, height, 32, width * 4, left, top, left + columns, top + rows)
            assert props[display, "boot_fb0"] == ",".join(f"{v:x}" for v in fields).encode() + b"\0"
            assert all(props[display, n] == struct.pack(">I", v) for n, v in manifest["early_display"]["handoff"].items())
            assert props["/chosen", "bootargs"] == original_properties["/chosen", "bootargs"]
            assert f"disp_reserve={width * height * 4},0x{pixel_address:x}".encode() in props["/chosen", "bootargs"].split()
            assert fdt_reservations(actual_tree) == original_reservations
            row.update(pixels_exact=True, pixel_tail_unchanged=True, only_boot_fb0_changed=True,
                       reservations_unchanged=True, boot_disp_unchanged=True, bootargs_unchanged=True)
        elif expected == 0:
            assert actual_tree == input_tree
            assert actual_pixels == bytes(width * height * 4) + sentinel[width * height * 4:]
            row.update(blank_pixels_exact=True)
        else:
            assert name.startswith("fdt-") and actual_tree == input_tree
            assert actual_pixels[:width * height * 4] == expected_pixels
        cases.append(row)
        return actual_tree, actual_pixels[:width * height * 4], desc

    try:
        descriptor = (0x31474f4c, len(bmp), fnv(bmp), 0)
        execute("missing-descriptor", descriptor=bytes(16), expected=0)
        for name, index, value in (("descriptor-magic", 0, 0x31474f4d), ("descriptor-reserved", 3, 1),
                                   ("descriptor-empty", 1, 0), ("descriptor-over-capacity", 1, capacity + 1),
                                   ("descriptor-hash", 2, descriptor[2] ^ 1)):
            fields = list(descriptor); fields[index] = value
            execute(name, descriptor=struct.pack("<4I", *fields))
        for name, offset, fmt, value in (("bmp-signature", 0, "<H", 0), ("bmp-file-size", 2, "<I", len(bmp) + 1),
                ("bmp-dib-short", 14, "<I", 39), ("bmp-dib-outside-file", 14, "<I", len(bmp)),
                ("bmp-offset-before-dib", 10, "<I", 53), ("bmp-offset-outside-file", 10, "<I", len(bmp) + 1),
                ("bmp-width", 18, "<I", width + 1), ("bmp-height", 22, "<i", height + 1),
                ("bmp-height-minimum-int", 22, "<I", 0x80000000), ("bmp-planes", 26, "<H", 2),
                ("bmp-bpp", 28, "<H", 32), ("bmp-compression", 30, "<I", 1)):
            damaged = bytearray(bmp); struct.pack_into(fmt, damaged, offset, value)
            execute(name, bytes(damaged))
        execute("bmp-short-header", payload=bmp[:53])
        truncated = bytearray(bmp[:-1]); struct.pack_into("<I", truncated, 2, len(truncated))
        execute("bmp-truncated-pixels", bytes(truncated))
        actual_tree, actual_pixels, actual_descriptor = execute("physical-bottom-up", expected=1)
        (directory / "verified-logo-kernel.dtb").write_bytes(actual_tree)
        (directory / "logo-pixels.bin").write_bytes(actual_pixels)
        (directory / "logo-descriptor.bin").write_bytes(actual_descriptor)
        offset = struct.unpack_from("<I", bmp, 10)[0]
        stride = (source_width * 3 + 3) & ~3
        rows = abs(source_height)
        top_down = bytearray(bmp)
        struct.pack_into("<i", top_down, 22, -rows)
        if source_height > 0:
            top_down[offset:offset + stride * rows] = b"".join(bmp[offset + y * stride:offset + (y + 1) * stride] for y in reversed(range(rows)))
        assert logo_pixels(top_down, width, height) == expected_pixels
        execute("top-down", bytes(top_down), expected=1)
        padded = bytearray(bmp[:offset] + bytes([0x9a]) * 17 + bmp[offset:])
        struct.pack_into("<I", padded, 2, len(padded)); struct.pack_into("<I", padded, 10, offset + 17)
        execute("pixel-offset-padding", bytes(padded), expected=1)
        invalid_tree = bytearray(tree); invalid_tree[:4] = bytes(4)
        execute("fdt-bad-magic", expected=-1, input_tree=bytes(invalid_tree))
        no_space = bytearray(tree)
        used = struct.unpack_from(">I", tree, 12)[0] + struct.unpack_from(">I", tree, 32)[0]
        struct.pack_into(">I", no_space, 4, used)
        execute("fdt-no-space", expected=-1, input_tree=bytes(no_space))
        assert len(cases) == 25
        for name, columns, rows in (("centered-smaller-odd-stride", width - 1, height - 1),
                                    ("centered-one-pixel", 1, 1)):
            row_stride = (columns * 3 + 3) & ~3
            smaller = bytearray(bmp[:54] + bytes(row_stride * rows))
            struct.pack_into("<I", smaller, 2, len(smaller)); struct.pack_into("<I", smaller, 10, 54)
            struct.pack_into("<ii", smaller, 18, columns, rows)
            struct.pack_into("<I", smaller, 34, row_stride * rows)
            for y in range(rows):
                for x in range(columns):
                    at = 54 + y * row_stride + x * 3
                    smaller[at:at + 3] = bytes(((17 + x + y) & 255, (34 + x * 3) & 255, (51 + y * 5) & 255))
            execute(name, bytes(smaller), expected=1)
        for name, value in (("bmp-zero-width", 0), ("bmp-negative-width", 0xffffffff)):
            damaged = bytearray(bmp); struct.pack_into("<I", damaged, 18, value)
            execute(name, bytes(damaged))
    finally:
        machine.hook_del(stop_hook)
        machine.mem_write(descriptor_address, bytes(16))
        machine.mem_write(0x4a300300, bytes(80))
    return dict(passed=True, linked_symbol="baseos_prepare_logo", linked_symbol_address=hex(sym["baseos_prepare_logo"]),
                fixture_path=str(fixture_path.relative_to(root)), fixture_sha256=hashlib.sha256(bmp).hexdigest(), fixture_bytes=len(bmp),
                width=width, height=height, pixel_address=hex(pixel_address), pixel_bytes=len(expected_pixels),
                descriptor_address=hex(descriptor_address), bmp_address=hex(bmp_address), capacity_bytes=capacity,
                descriptor_sha256=hashlib.sha256(actual_descriptor).hexdigest(),
                prepared_fdt_sha256=hashlib.sha256(actual_tree).hexdigest(), pixels_sha256=hashlib.sha256(actual_pixels).hexdigest(),
                missing_logo_fdt_sha256=hashlib.sha256(tree).hexdigest(), boot_fb0=description, cases=cases[:25], geometry_cases=cases[25:],
                source_width=source_width, source_height=source_height,
                row_padding_bytes=stride - source_width * 3, pixel_offset_padding_bytes=17,
                hardware_boot=False, kernel_decode_repeated=False)


def verify_display(machine, sym, manifest, tree, directory, record, ticks):
    """Exercise the linked ready-state boundary, cached panel, and warm FDT producer."""
    assert "baseos_native_display_state" in sym and "make_command_line" in sym
    root = Path(__file__).resolve().parents[3]
    width, height = manifest["boot_logo"]["width"], manifest["boot_logo"]["height"]
    fixture_path = root / "work" / manifest["target"] / "bootlogo.bmp"
    bmp = fixture_path.read_bytes()
    pixels = logo_pixels(bmp, width, height)
    display_address, descriptor_address = 0x4a300300, 0x4a300200
    bmp_address, pixel_address, tree_address, stop = 0x4b400000, 0x40000000 + struct.unpack("<Q", machine.mem_read(0x4a300010, 8))[0] - manifest["boot_logo"]["framebuffer_from_ram_end"], 0x4a400000, 0x4a0d0000
    descriptor = struct.pack("<4I", 0x31474f4c, len(bmp), fnv(bmp), 0)
    generation = bytes.fromhex(manifest["generation"])
    rule = manifest["panel_rule"]
    read_started, loaded = struct.unpack_from("<2Q", record, 32)
    assert 0 < read_started <= loaded < ticks[0]
    # The A32 loader draws after the SPL load and before its own readiness check.
    shown = loaded + 1
    policy = manifest["early_display"]["handoff"]
    assert set(policy) == {"boot_disp", "boot_disp1", "boot_disp2"}
    assert policy == {"boot_disp": 0x100, "boot_disp1": 0x01040000, "boot_disp2": 4}

    def checksum(raw):
        raw = bytearray(raw)
        struct.pack_into("<I", raw, 12, 0)
        struct.pack_into("<I", raw, 12, fnv(raw))
        return bytes(raw)

    def status(panel, tick=shown):
        return checksum(struct.pack("<8I32sQ2I", 0x31505344, 1, 80, 0, panel, len(bmp), fnv(bmp),
                                    fnv(pixels), generation, tick, 0, 0))

    base_panel = 1 if rule == 2 else rule
    base = status(base_panel)
    cases = []
    accesses = []
    sid_reads = []

    def mmio_guard(m, access, address, size, value, data):
        if access == u.UC_MEM_READ and address in (0x03006204, 0x03006208, 0x0300620c) and size == 4:
            sid_reads.append(address)
            return
        accesses.append([access, hex(address), size])
        raise AssertionError(("ready path touched panel/display MMIO", hex(address), access))

    hooks = [machine.hook_add(u.UC_HOOK_CODE, lambda m, pc, size, data: m.emu_stop(), begin=stop, end=stop)]
    hooks += [machine.hook_add(u.UC_HOOK_MEM_READ | u.UC_HOOK_MEM_WRITE, mmio_guard, begin=start, end=end)
              for start, end in ((0x03000000, 0x0300ffff), (0x01000000, 0x023fffff), (0x06510000, 0x06511fff))]

    def call(name, argument=0):
        machine.reg_write(a.UC_ARM_REG_CPSR, 0x1d3)
        machine.reg_write(a.UC_ARM_REG_R0, argument)
        machine.reg_write(a.UC_ARM_REG_SP, 0x4a0f0000)
        machine.reg_write(a.UC_ARM_REG_LR, stop)
        machine.emu_start(sym[name], 0x50000000, count=100000000)
        assert machine.reg_read(a.UC_ARM_REG_PC) == stop, (name, "did not return")
        value = machine.reg_read(a.UC_ARM_REG_R0)
        return value - (1 << 32) if value & 0x80000000 else value

    def execute(name, raw=base, payload=bmp, source_pixels=pixels, desc=descriptor, expected=-1,
                native_record=record, input_tree=tree):
        assert len(raw) == 80
        machine.mem_write(0x4a300000, bytes(native_record))
        machine.mem_write(display_address, raw)
        machine.mem_write(descriptor_address, desc)
        machine.mem_write(bmp_address, payload)
        machine.mem_write(pixel_address, source_pixels)
        machine.mem_write(tree_address, input_tree)
        assert call("baseos_native_display_state") == expected, name
        if expected == 0:
            assert call("baseos_prepare_logo", tree_address) == -1, "cold display advertised warm ownership"
            assert bytes(machine.mem_read(tree_address, len(input_tree))) == input_tree
            cases.append(dict(name=name, return_value=0, all_zero_is_cold=True))
            return None
        assert call("make_command_line", 0x45000000) == (0 if expected == 1 else -1), name
        produced = call("baseos_prepare_logo", tree_address)
        if produced != expected:
            after = {(p, n): v for p, n, _, v in fdt.walk(bytes(machine.mem_read(tree_address, len(input_tree))))}
            before = {(p, n): v for p, n, _, v in fdt.walk(input_tree)}
            changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
            raise AssertionError((name, "logo return", produced, expected, sorted(changed)))
        actual_tree = bytes(machine.mem_read(tree_address, len(input_tree)))
        actual_pixels = bytes(machine.mem_read(pixel_address, len(source_pixels)))
        assert bytes(machine.mem_read(display_address, 80)) == raw
        assert bytes(machine.mem_read(descriptor_address, 16)) == desc
        assert bytes(machine.mem_read(bmp_address, len(payload))) == payload
        row = dict(name=name, return_value=expected, record_sha256=hashlib.sha256(raw).hexdigest(),
                   no_panel_probe_or_reset=True, panel_display_mmio_accesses=0)
        if expected < 0:
            assert actual_tree == input_tree and actual_pixels == source_pixels, (name, "refusal changed tree/pixels")
            row.update(tree_unchanged=True, pixels_unchanged=True)
        else:
            panel = struct.unpack_from("<I", raw, 16)[0]
            command = bytes(machine.mem_read(sym["command_line"], 2048)).split(b"\0", 1)[0]
            assert [v for v in command.split() if v.startswith(b"lcd_type=")] == [b"lcd_type=" + (b"boe" if panel else b"old")]
            before = {(p, n): v for p, n, _, v in fdt.walk(input_tree)}
            props = {(p, n): v for p, n, _, v in fdt.walk(actual_tree)}
            display = "/soc@03000000/disp@01000000"
            changed = {key for key in before.keys() | props.keys() if before.get(key) != props.get(key)}
            assert changed == {(display, "boot_fb0")}, (name, changed)
            for prop, value in policy.items(): assert props[display, prop] == struct.pack(">I", value)
            assert props["/chosen", "baseos,boot-display"] == before["/chosen", "baseos,boot-display"]
            assert actual_pixels == pixels
            assert fdt_reservations(actual_tree) == fdt_reservations(input_tree)
            # Exercise the actual renderer above, then bind its output for the kernel consumer.
            output = bytearray(actual_tree)
            old_command = props["/chosen", "bootargs"]
            expected_command = old_command.replace(b"lcd_type=old", b"lcd_type=boe") if panel else old_command.replace(b"lcd_type=boe", b"lcd_type=old")
            assert len(expected_command) == len(old_command)
            if command + b"\0" != expected_command:
                raise AssertionError((name, "cached renderer changed unrelated bootargs"))
            fdt.set_same_size(output, "/chosen", "bootargs", expected_command)
            fdt.set_same_size(output, "/chosen", "baseos,boot-display", raw)
            actual_tree = bytes(output)
            row.update(panel="boe" if panel else "old", warm_metadata_exact=True, status_copied_exact=True,
                       pixels_exact=True, cached_panel_rendered=True, only_warm_properties_changed=True)
        cases.append(row)
        assert not accesses
        return actual_tree

    try:
        execute("all-zero-cold", raw=bytes(80), expected=0)
        invalid = bytearray(80); invalid[-1] = 1
        execute("nonzero-unknown", raw=bytes(invalid))
        for name, offset, fmt, value in (("magic", 0, "<I", 0), ("version", 4, "<I", 2),
                ("bytes", 8, "<I", 79), ("panel", 16, "<I", 2), ("logo-short", 20, "<I", 53),
                ("logo-large", 20, "<I", 0x400001), ("reserved0", 72, "<I", 1),
                ("reserved1", 76, "<I", 1), ("shown-zero", 64, "<Q", 0),
                ("shown-before-load", 64, "<Q", loaded - 1), ("shown-after-now", 64, "<Q", 1 << 62)):
            damaged = bytearray(base); struct.pack_into(fmt, damaged, offset, value)
            execute("status-" + name, raw=checksum(damaged))
        damaged = bytearray(base); damaged[12] ^= 1
        execute("status-checksum", raw=bytes(damaged))
        damaged = bytearray(base); damaged[32] ^= 1
        execute("status-generation", raw=checksum(damaged))
        for name, offset in (("magic", 0), ("bytes", 4), ("hash", 8), ("reserved", 12)):
            damaged = bytearray(descriptor); damaged[offset] ^= 1
            execute("descriptor-" + name, desc=bytes(damaged))
        # v151: the ready record already carries the hashes taken by the display program in
        # this same loader run; the BMP and pixels are not hashed a second time.
        damaged = bytearray(bmp); damaged[-1] ^= 1
        execute("source-not-rehashed", payload=bytes(damaged), expected=1)
        for panel in (0, 1):
            execute("ready-" + ("boe" if panel else "old"), raw=status(panel), expected=1 if rule == 2 or panel == rule else -1)
        for tick, name in ((loaded, "loaded-end"), (ticks[0], "current-counter")):
            execute("ready-stamp-" + name, raw=status(base_panel, tick), expected=1)
        export = execute("ready-export", expected=1)
        (directory / "verified-logo-kernel.dtb").write_bytes(export)
        (directory / "logo-display-record.bin").write_bytes(base)
    finally:
        for hook in hooks: machine.hook_del(hook)
        machine.mem_write(display_address, bytes(80))
        machine.mem_write(descriptor_address, bytes(16))
        machine.mem_write(0x4a300000, bytes(record))
    return dict(passed=True, linked_symbol="baseos_native_display_state", linked_symbol_address=hex(sym["baseos_native_display_state"]),
                cases=cases, kernel_decode_repeated=False, no_panel_probe_or_reset=True, panel_display_mmio_accesses=0,
                sid_read_count=len(sid_reads), fixture_sha256=hashlib.sha256(bmp).hexdigest(),
                fixture_path=str(fixture_path.resolve()), pixels_sha256=hashlib.sha256(pixels).hexdigest(),
                hardware_boot=False)


def verify(directory, inventory, spl=None, prepared_source=False):
    manifest = json.loads((directory / "manifest.json").read_text())
    inventory_manifest = json.loads((inventory / "inventory.json").read_text())
    serial = "1c00103dc2840671ed3" if prepared_source else inventory_manifest["serial"]
    assert re.fullmatch(r"[0-9a-f]{19}", serial)
    code, image = (directory / "loader.bin").read_bytes(), (directory / "image.bin").read_bytes()
    assert hashlib.sha256(code).hexdigest() == manifest["loader_sha256"]
    assert hashlib.sha256(image).hexdigest() == manifest["image_sha256"]
    expected_kernel = (directory / "kernel.bin").read_bytes()
    assert hashlib.sha256(expected_kernel).hexdigest() == manifest["kernel_sha256"]
    if not manifest.get("kernel_exception_witness"):
        assert manifest["kernel_sha256"] == manifest["source_kernel_sha256"], "clean kernel differs from source"
    if manifest.get("kernel_exception_witness"):
        offset = 0x4098d640 - 0x40080000
        fault_code = (directory / "kernel-fault.bin").read_bytes()
        expected_fault = bytearray(fault_code)
        if manifest.get("kernel_initcall_probe"):
            initcall = (directory / "initcall.bin").read_bytes()
            expected_fault[0x40:0x40 + len(initcall)] = initcall
        assert len(fault_code) == 0x9c0 and expected_kernel[offset:offset + len(fault_code)] == expected_fault
        source = bytearray(expected_kernel[:offset] + bytes(len(fault_code)) + expected_kernel[offset + len(fault_code):])
        if manifest.get("kernel_initcall_probe"):
            struct.pack_into("<I", source, 0x3954, 0xa9b77bfd)
        assert hashlib.sha256(source).hexdigest() == manifest["source_kernel_sha256"]
    sym = symbols(directory)
    words = list(struct.unpack_from("<32I", (inventory / "boot0.bin").read_bytes(), 0x38))
    ram_bytes = (words[7] >> 16) << 20
    assert ram_bytes in (0x40000000, 0x80000000)
    record_version = manifest.get("native_record_version", 1)
    assert record_version in (1, 2)
    record_bytes = 240 if record_version == 2 else 232
    record = bytearray(struct.pack("<4I4QI32I8s3I32s", 0x314e5342, record_version, record_bytes, manifest["model_id"],
                      ram_bytes, 1000, 2000, 3000, words[1], *words, bytes(8), len(image),
                      manifest["image_fnv"], 0, bytes.fromhex(manifest["generation"])))
    if record_version == 2:
        record += struct.pack("<2I", 5, 0)
    struct.pack_into("<I", record, 196, fnv(record))
    spl_panel, spl_pio = None, {}
    if spl:
        spl_manifest = json.loads((spl / "manifest.json").read_text())
        assert spl_manifest["native_loader"] == manifest
        record = bytearray((spl / "native-record.bin").read_bytes())
        assert len(record) == record_bytes
        words = list(struct.unpack_from("<32I", record, 52))
        ram_bytes = struct.unpack_from("<Q", record, 16)[0]
        # v151: the SPL ran the panel steps; replay its record and the pins it left.
        if (spl / "native-panel.bin").exists():
            spl_panel = (spl / "native-panel.bin").read_bytes()
            spl_pio = {int(k, 16): v for k, v in json.loads((spl / "pio-state.json").read_text()).items()}
    decoder = c.Cs(c.CS_ARCH_ARM, c.CS_MODE_ARM)
    cp_instructions = {}
    for instruction in decoder.disasm(code[0x1000:sym["baseos_prepare_kernel"] - 0x4a000000], 0x4a001000):
        if instruction.mnemonic in ("mrc", "mcr", "mrrc", "smc"):
            cp_instructions[instruction.address] = instruction

    m = u.Uc(u.UC_ARCH_ARM, u.UC_MODE_ARM)
    m.mem_map(0x40000000, 0x10000000)
    pixel_address = 0x40000000 + ram_bytes - manifest["boot_logo"]["framebuffer_from_ram_end"]
    pixel_page = pixel_address & ~4095
    m.mem_map(pixel_page, (pixel_address + 0x400000 - pixel_page + 4095) & ~4095)
    m.mem_write(0x44000000, b"\xa5" * 0x100000)
    m.mem_map(0x03000000, 0x10000)
    m.mem_map(0x01008000, 0x1000)
    m.mem_map(0x07000000, 0x1000)
    m.mem_write(0x4a000000, code)
    m.mem_write(0x45000000, image)
    m.mem_write(0x4a300000, bytes(record))
    if spl_panel:
        m.mem_write(0x4a300280, spl_panel)
        for address, value in spl_pio.items():
            m.mem_write(address, struct.pack("<I", value))
    m.mem_write(sym["__bss_start"], b"\xa5" * (sym["__bss_end"] - sym["__bss_start"]))
    sid = (int(serial[:3], 16) << 20, int(serial[3:11], 16), int(serial[11:], 16))
    for offset, value in zip((0x204, 0x208, 0x20c), sid):
        m.mem_write(0x03006000 + offset, struct.pack("<I", value))
    m.reg_write(a.UC_ARM_REG_CPSR, 0x1d3)
    for i in range(13): m.reg_write(a.UC_ARM_REG_R0 + i, 0xdead0000 + i)
    cp = {(0, 1, 0, 0): 0x30c00820, (1, 0, 0, 1): 0x02000023,
          (1, 0, 0, 0): 2 | (3 << 3) | (7 << 13)}
    events, calls, ticks, translations = [], [], [4000], []
    if spl_panel:
        ticks[0] = max(4000, struct.unpack_from("<Q", spl_panel, 56)[0], struct.unpack_from("<Q", record, 40)[0]) + 1000
    dirty_guard = [None]
    sram_initial = {0x03000000: 0xffffffff, 0x03000004: 0xa5ffffff}
    for address, value in sram_initial.items():
        m.mem_write(address, struct.pack("<I", value))
    sram_writes = []
    panel_writes = []
    panel_samples = []
    panel_sequence = [(0x0300b124, 0xff000000, 0x11000000),
                      (0x0300b130, 0x8000, 0x8000),
                      (0x0300b130, 0x4000, 0x4000),
                      (0x0300b130, 0x4000, 0),
                      (0x0300b130, 0x4000, 0x4000),
                      (0x0300b090, 0x0f000000, 0x06000000)]
    if manifest["panel_pull_up"]:
        panel_sequence.append((0x0300b0ac, 0x3000, 0x1000))
    prepare_completed = [False]
    prepare_hooks = []
    logo_calls = []
    assert "baseos_prepare_logo" in sym, "missing noinline linked logo producer"
    prepare_hooks.append(m.hook_add(u.UC_HOOK_CODE,
                         lambda machine, pc, size, data: logo_calls.append(machine.reg_read(a.UC_ARM_REG_R0)),
                         begin=sym["baseos_prepare_logo"], end=sym["baseos_prepare_logo"]))

    def prepare_return(machine, pc, size, data):
        assert machine.reg_read(a.UC_ARM_REG_R0) == 0
        prepare_completed[0] = True

    def prepare_entry(machine, pc, size, data):
        address = machine.reg_read(a.UC_ARM_REG_LR)
        prepare_hooks.append(machine.hook_add(u.UC_HOOK_CODE, prepare_return, begin=address, end=address))

    prepare_hooks.append(m.hook_add(u.UC_HOOK_CODE, prepare_entry,
                         begin=sym["baseos_prepare_kernel"], end=sym["baseos_prepare_kernel"]))

    def loader_mmio_write(machine, access, address, size, value, data):
        assert size == 4, (hex(address), size)
        if address in sram_initial:
            assert prepare_completed[0], "SRAM release preceded successful kernel preparation"
            sram_writes.append((address, value))
        else:
            assert manifest["panel_rule"] == 2 and len(panel_writes) < len(panel_sequence) and not spl_panel, \
                ("unexpected loader MMIO write", hex(address), hex(value))
            expected_address, mask, owned = panel_sequence[len(panel_writes)]
            before = struct.unpack("<I", machine.mem_read(address, 4))[0]
            assert address == expected_address and value == (before & ~mask) | owned, \
                ("panel GPIO sequence or unrelated bits changed", hex(address), hex(value))
            panel_writes.append((address, value, ticks[0]))

    def panel_sample(machine, access, address, size, value, data):
        assert manifest["panel_rule"] == 2 and size == 4
        if spl_panel:
            # Cross-check read of the strap the SPL already configured and sampled.
            panel_samples.append(ticks[0])
            return
        assert len(panel_writes) == len(panel_sequence), "panel sampled before prerequisite GPIO writes"
        for previous, following, minimum in ((1, 2, 720000), (2, 3, 360000),
                                              (3, 4, 1200000)):
            assert panel_writes[following][2] - panel_writes[previous][2] >= minimum
        assert ticks[0] - panel_writes[4][2] >= 2400000
        panel_samples.append(ticks[0])

    def forbidden_display_access(machine, access, address, size, value, data):
        raise AssertionError(("loader touched DE/CCU instead of leaving kernel initialization intact",hex(address),access))

    display_hooks = [m.hook_add(u.UC_HOOK_MEM_WRITE, loader_mmio_write, begin=0x03000000, end=0x0300ffff)]
    display_hooks.append(m.hook_add(u.UC_HOOK_MEM_READ, panel_sample, begin=0x0300b0a0, end=0x0300b0a3))
    display_hooks += [m.hook_add(u.UC_HOOK_MEM_READ | u.UC_HOOK_MEM_WRITE, forbidden_display_access,
                                begin=begin,end=end)
                      for begin,end in ((0x03001000, 0x03001fff), (0x01000000, 0x023fffff))]

    def reg(name):
        return {"sb": a.UC_ARM_REG_R9, "sl": a.UC_ARM_REG_R10,
                "fp": a.UC_ARM_REG_R11, "ip": a.UC_ARM_REG_R12}.get(name,
                getattr(a, "UC_ARM_REG_" + name.upper()))

    def cp_step(machine, address, size, data):
        ins = cp_instructions[address]
        if ins.mnemonic == "smc":
            calls.append([machine.reg_read(a.UC_ARM_REG_R0 + i) for i in range(4)])
            machine.emu_stop()
            return
        if ins.mnemonic == "mrrc":
            machine.reg_write(a.UC_ARM_REG_R0, ticks[0])
            machine.reg_write(a.UC_ARM_REG_R1, 0)
            ticks[0] += 1000
        else:
            match = re.fullmatch(r"p15, #(\d+), (\w+), c(\d+), c(\d+), #(\d+)", ins.op_str)
            assert match, ins.op_str
            op1, register, crn, crm, op2 = match.groups()
            key = tuple(map(int, (op1, crn, crm, op2)))
            if ins.mnemonic == "mrc":
                assert key in cp, (address, key)
                machine.reg_write(reg(register), cp[key])
            else:
                value = machine.reg_read(reg(register))
                if key == (0, 1, 0, 0) and value & 1:
                    table = struct.unpack("<4096I", machine.mem_read(0x4a0f8000, 16384))
                    for index, descriptor in enumerate(table):
                        expected = (index << 20) | (0x1c0e if index == 0 or 0x400 <= index < (0x400 + (ram_bytes >> 20)) else 0x10c16)
                        assert descriptor == expected
                    assert cp[0, 2, 0, 0] == 0x4a0f8000 and cp[0, 2, 0, 2] == 0 and cp[0, 3, 0, 0] == 1
                    translations.append({"entries": len(table), "normal_dram": True, "device_xn_mmio": True})
                    if dirty_guard[0] is not None:
                        machine.hook_del(dirty_guard[0])
                        dirty_guard[0] = None
                cp[key] = value
                events.append({"address": hex(address), "register": list(key), "value": hex(value)})
        machine.reg_write(a.UC_ARM_REG_PC, address + 4)

    def display_model(machine, pc, size, data):
        if pc == sym["baseos_early_display_start"]:
            panel = 0 if manifest["panel_rule"] == 0 else 1
            status = bytearray(struct.pack("<8I32sQ2I", 0x31505344, 1, 80, 0, panel, 0, 0,
                              fnv(bytes(manifest["boot_logo"]["width"] * manifest["boot_logo"]["height"] * 4)),
                              bytes.fromhex(manifest["generation"]), ticks[0], 0, 0))
            struct.pack_into("<I", status, 12, fnv(status))
            machine.mem_write(0x4a300300, bytes(status))
        machine.reg_write(a.UC_ARM_REG_R0, 0)
        machine.reg_write(a.UC_ARM_REG_PC, machine.reg_read(a.UC_ARM_REG_LR))
    for name in ("baseos_early_display_start", "baseos_early_display_poll", "baseos_early_display_finish"):
        prepare_hooks.append(m.hook_add(u.UC_HOOK_CODE, display_model, begin=sym[name], end=sym[name]))

    for address in cp_instructions: m.hook_add(u.UC_HOOK_CODE, cp_step, begin=address, end=address)
    def before_mmu(machine, access, address, size, value, data):
        assert size == 1 or address % size == 0, (hex(address), size)
    dirty_guard[0] = m.hook_add(u.UC_HOOK_MEM_READ | u.UC_HOOK_MEM_WRITE, before_mmu)
    m.emu_start(0x4a000000, 0x50000000, count=500000000)
    kernel_call_entry = manifest.get("kernel_smc_entry", 0x40080000)
    assert calls == [[0x8000ff04, kernel_call_entry, 0x44000000, 1]], calls
    expected_sram_writes = [(0x03000000,sram_initial[0x03000000] & 0x80000000),
                            (0x03000004,sram_initial[0x03000004] & 0xfeffffff)]
    assert sram_writes == expected_sram_writes, sram_writes
    assert not panel_samples, "ready display must supply its cached panel"
    assert logo_calls == [0x44000000], "kernel preparation did not call the linked logo producer once"
    assert not panel_writes, "ready display must not redo panel steps"
    for hook in display_hooks + prepare_hooks:
        m.hook_del(hook)
    assert not cp[0, 1, 0, 0] & 0x1005
    if "kernel_smc_entry" in manifest:
        from unicorn import arm64_const as x
        witnessed = manifest.get("entry_checksum_witness", False)
        for damaged in ((None, "kernel", "fdt") if witnessed else (None,)):
            probe = u.Uc(u.UC_ARCH_ARM64, u.UC_MODE_ARM)
            probe.mem_map(0x40000000, 0x10000000)
            probe.mem_map(0x07000000, 4096)
            probe.mem_write(0x4a000000, code)
            for address, length in ((0x40080000, len(expected_kernel)), (0x44000000, 0x100000), (0x4a300100, 8)):
                probe.mem_write(address, bytes(m.mem_read(address, length)))
            if damaged:
                address = 0x40080080 if damaged == "kernel" else 0x44000000
                probe.mem_write(address, bytes([probe.mem_read(address, 1)[0] ^ 1]))
            for index, value in enumerate((0x44000000, 0, 0, 0)):
                probe.reg_write(x.UC_ARM64_REG_X0 + index, value)
            probe.hook_add(u.UC_HOOK_CODE, lambda m, pc, size, data: m.emu_stop(), begin=0x40080000, end=0x40080000)
            probe.hook_add(u.UC_HOOK_MEM_WRITE,
                lambda m, access, address, size, value, data: m.emu_stop() if value & 0xffff in (0x9001, 0x9002) else None,
                begin=0x0700013c, end=0x0700013f)
            probe.emu_start(kernel_call_entry, 0x50000000, count=250000000)
            stage = struct.unpack("<I", probe.mem_read(0x0700013c, 4))[0]
            if damaged:
                assert stage == 0x42539001 + int(damaged == "fdt")
            else:
                assert probe.reg_read(x.UC_ARM64_REG_PC) == 0x40080000
                assert [probe.reg_read(x.UC_ARM64_REG_X0 + index) for index in range(4)] == [0x44000000, 0, 0, 0]
                assert stage == (0x42530033 if witnessed else 0x42530031)
                if manifest.get("kernel_exception_witness"):
                    assert probe.reg_read(x.UC_ARM64_REG_VBAR_EL1) == manifest["kernel_fault_vectors"]
                    fault_seen = []
                    def fault_write(machine, access, address, size, value, data):
                        fault_seen.append(value)
                        machine.emu_stop()
                    hook = probe.hook_add(u.UC_HOOK_MEM_WRITE, fault_write, begin=0x0700013c, end=0x0700013f)
                    for vector in range(16):
                        pc = 0x4098d22c if vector % 2 == 0 else 0xffffff8008d40184
                        probe.reg_write(x.UC_ARM64_REG_ELR_EL1, pc)
                        probe.emu_start(manifest["kernel_fault_vectors"] + 128 * vector, 0x50000000, count=100)
                        assert fault_seen[-1] == pc & 0xffffffff
                    probe.hook_del(hook)
        if witnessed:
            (directory / "kernel-entry-check.bin").write_bytes(bytes(m.mem_read(0x4a300100, 8)))
    assert bytes(m.mem_read(0x40080000, len(expected_kernel))) == expected_kernel
    properties = {(path, name): value for path, name, _, value in fdt.walk(bytes(m.mem_read(0x44000000, 0x100000)))}
    assert properties["/chosen", "baseos,native-boot"] == bytes.fromhex(manifest["generation"])
    assert struct.unpack(">2Q", properties["/memory@40000000", "reg"]) == (0x40000000, ram_bytes)
    command = properties["/chosen", "bootargs"].rstrip(b"\0").decode()
    reasons = ("button", "irq", "usb", "charger", "battery", "unknow")
    boot_reason, boot_reason_source = struct.unpack_from("<2I", record, 232) if record_version == 2 else (5, 0)
    assert "bootreason=" + reasons[boot_reason] in command.split()
    assert "androidboot.mode=normal" in command.split()
    assert "androidboot.serialno=" + serial in command.split()
    assert "snum=" + serial in command.split()
    low_panel = "old" if manifest["panel_rule"] == 0 else "boe"
    assert f"disp_reserve={manifest['boot_logo']['width'] * manifest['boot_logo']['height'] * 4},0x{pixel_address:x}" in command.split() and "lcd_type=" + low_panel in command.split()
    assert ("maxcpus=1" in command.split()) == (manifest.get("diagnostic_maxcpus") == 1)
    assert properties["/soc@03000000/hdmi@06000000", "status"] == b"okay\0"
    offset = 2048
    parts = []
    for field in (8, 16, 24, 1632, 1648):
        length = struct.unpack_from("<I", image, field)[0]
        parts.append(image[offset:offset + length])
        offset += (length + 2047) & ~2047
    assert bytes(m.mem_read(0x42000000, len(parts[1]))) == parts[1]
    assert properties["/chosen", "linux,initrd-start"] == struct.pack(">Q", 0x42000000)
    assert properties["/chosen", "linux,initrd-end"] == struct.pack(">Q", 0x42000000 + len(parts[1]))
    reserve = bytes(m.mem_read(0x44000000, 0x100000))
    at = struct.unpack_from(">I", reserve, 16)[0]
    reservations = []
    while True:
        entry = struct.unpack_from(">2Q", reserve, at)
        at += 16
        if entry == (0, 0): break
        reservations.append(entry)
    assert (0x42000000, len(parts[1])) in reservations and (0x48000000, 0x1000000) in reservations
    source_properties = {(path, name): value for path, name, _, value in fdt.walk(parts[4])}
    allowed = {(path, name) for path, name in properties if path == "/dram"}
    allowed |= {("/chosen", name) for name in ("bootargs", "linux,initrd-start", "linux,initrd-end",
                                               "baseos,native-boot", "baseos,native-timing", "baseos,boot-display")}
    allowed |= {("/memory@40000000", "reg")}
    allowed |= {("/soc@03000000/disp@01000000", n) for n in ("boot_disp", "boot_disp1", "boot_disp2")}
    changed = {key for key in source_properties.keys() | properties.keys() if source_properties.get(key) != properties.get(key)}
    assert changed <= allowed, changed - allowed
    assert all(properties["/soc@03000000/disp@01000000", n] == struct.pack(">I", v) for n, v in manifest["early_display"]["handoff"].items())
    if not prepared_source:
        live_properties = {(path, name): value for path, name, _, value in fdt.walk((inventory / "live.dtb").read_bytes())}
        live_allowed = allowed | {key for key in live_properties if "/disp@" in key[0] and key[1].startswith(("boot_disp", "boot_fb"))}
        live_changed = {key for key in live_properties.keys() | properties.keys() if live_properties.get(key) != properties.get(key)}
        assert live_changed <= live_allowed, live_changed - live_allowed
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "variant-survey"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("native_bindings", Path(__file__).resolve().parents[1] / "variant-survey/fdt-contract.py")
    binding = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(binding)
    for index, name in enumerate(binding.DRAM_PROPERTIES):
        assert properties["/dram", name] == struct.pack(">I", words[index])
    # Exercise each refusal before decompression; these calls run linked code.
    m.hook_add(u.UC_HOOK_CODE, lambda machine, pc, size, data: machine.emu_stop(), begin=0x4a0d0000, end=0x4a0d0000)
    refusals = []
    def prepare():
        m.reg_write(a.UC_ARM_REG_SP, 0x4a0f0000)
        m.reg_write(a.UC_ARM_REG_LR, 0x4a0d0000)
        m.emu_start(sym["baseos_prepare_kernel"], 0x50000000, count=1000000)
        assert m.reg_read(a.UC_ARM_REG_PC) == 0x4a0d0000
        return m.reg_read(a.UC_ARM_REG_R0)
    for offset, value, expected in ((0, 0, 1), (196, 0, 1)):
        bad = bytearray(record)
        struct.pack_into("<I", bad, offset, value)
        m.mem_write(0x4a300000, bytes(bad))
        assert prepare() == expected
        refusals.append({"record_offset": offset, "error": expected})
    if record_version == 2:
        for ram in (0x10000000, 0x20000000, 0xc0000000):
            bad = bytearray(record)
            struct.pack_into("<Q", bad, 16, ram)
            struct.pack_into("<I", bad, 80, (ram >> 20 << 16) | (words[7] & 0xffff))
            struct.pack_into("<I", bad, 196, 0)
            struct.pack_into("<I", bad, 196, fnv(bad))
            m.mem_write(0x4a300000, bytes(bad))
            assert prepare() == 1
            refusals.append({"unproven_ram_bytes": ram, "checksum_recomputed": True, "error": 1})
        for offset, value in ((4, 1), (8, 232), (232, 6), (236, 3)):
            bad = bytearray(record)
            struct.pack_into("<I", bad, offset, value)
            struct.pack_into("<I", bad, 196, 0)
            struct.pack_into("<I", bad, 196, fnv(bad))
            m.mem_write(0x4a300000, bytes(bad))
            assert prepare() == 1
            refusals.append({"record_offset": offset, "value": value, "checksum_recomputed": True, "error": 1})
        for reason, source in ((5, 1), (0, 0), (0, 2)):
            bad = bytearray(record)
            struct.pack_into("<2I", bad, 232, reason, source)
            struct.pack_into("<I", bad, 196, 0)
            struct.pack_into("<I", bad, 196, fnv(bad))
            m.mem_write(0x4a300000, bytes(bad))
            assert prepare() == 1
            refusals.append({"boot_reason": reason, "boot_reason_source": source,
                             "checksum_recomputed": True, "error": 1})
    m.mem_write(0x4a300000, bytes(record))
    for offset, value, expected in ((36, 4096, 2), (40, 1, 2), (24, 1, 2),
                                     (1632, 1, 2), (8, 0xffffffff, 3), (16, 0x100001, 3), (1648, 0, 3)):
        m.mem_write(0x45000000 + offset, struct.pack("<I", value))
        assert prepare() == expected
        m.mem_write(0x45000000 + offset, image[offset:offset + 4])
        refusals.append({"image_offset": offset, "error": expected})
    m.mem_write(0x4a300300, bytes(80))
    # Both strap outcomes execute the real policy renderer without decoding again.
    panels = []
    for level in (0, 1):
        m.mem_write(0x0300b0a0, struct.pack("<I", level * 0x40))
        m.reg_write(a.UC_ARM_REG_R0, 0x45000000)
        m.reg_write(a.UC_ARM_REG_SP, 0x4a0f0000)
        m.reg_write(a.UC_ARM_REG_LR, 0x4a0d0000)
        m.emu_start(sym["make_command_line"], 0x50000000, count=500000)
        assert m.reg_read(a.UC_ARM_REG_R0) == 0
        text = bytes(m.mem_read(sym["command_line"], 2048)).split(b"\0")[0].decode()
        expected = "old" if manifest["panel_rule"] == 0 or manifest["panel_rule"] == 2 and level else "boe"
        assert "lcd_type=" + expected in text.split()
        panels.append(expected)
    bootreason_cases = []
    if record_version == 2:
        for reason, expected in enumerate(reasons):
            changed_record = bytearray(record)
            struct.pack_into("<2I", changed_record, 232, reason, 0 if reason == 5 else 1)
            m.mem_write(0x4a300000, bytes(changed_record))
            m.reg_write(a.UC_ARM_REG_R0, 0x45000000)
            m.reg_write(a.UC_ARM_REG_SP, 0x4a0f0000)
            m.reg_write(a.UC_ARM_REG_LR, 0x4a0d0000)
            m.emu_start(sym["make_command_line"], 0x50000000, count=500000)
            assert m.reg_read(a.UC_ARM_REG_R0) == 0
            text = bytes(m.mem_read(sym["command_line"], 2048)).split(b"\0")[0].decode()
            assert "bootreason=" + expected in text.split() and "androidboot.mode=normal" in text.split()
            assert "@BOOTREASON@" not in text
            bootreason_cases.append(expected)
        m.mem_write(0x4a300000, bytes(record))
    verified_tree = bytes(m.mem_read(0x44000000, 0x100000))
    (directory / "verified-kernel.dtb").write_bytes(verified_tree)
    logo = verify_logo(m, sym, manifest, verified_tree, directory)
    logo["missing_logo_main_call_verified"] = True
    early_display = verify_display(m, sym, manifest, verified_tree, directory, record, ticks)
    logo["prepared_fdt_sha256"] = hashlib.sha256((directory / "verified-logo-kernel.dtb").read_bytes()).hexdigest()
    report = {"passed": True, "loader_sha256": manifest["loader_sha256"],
              "image_sha256": manifest["image_sha256"], "kernel_sha256": hashlib.sha256(expected_kernel).hexdigest(),
              "kernel_bytes_exact": len(expected_kernel), "final_call_r0_r3": list(map(hex, calls[0])),
              "final_a32_sctlr": cp[0, 1, 0, 0], "kernel_smc_entry": kernel_call_entry,
              "translation_checks": translations, "modeled_cp15": events, "bootargs": command,
              "panel_results": panels, "refusals": refusals, "hardware_boot": False,
              "panel_gpio_writes": panel_writes, "panel_sample_ticks": panel_samples,
              "spl_panel_record_used": bool(spl_panel),
              "native_record_version": record_version, "native_record_bytes": record_bytes,
              "boot_reason": reasons[boot_reason], "boot_reason_source": boot_reason_source,
              "bootreason_render_cases": bootreason_cases,
              "unchanged_fdt_properties": len(source_properties.keys() - changed),
              "changed_fdt_properties": [list(key) for key in sorted(changed)], "initrd_exact": True,
              "live_fdt_static_properties_exact": not prepared_source,
              "fdt_reference_kind": "prepared-source" if prepared_source else "live-device",
              "sid_model_source": "fixture" if prepared_source else "inventory-serial",
              "serial_modeled": serial,
              "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "verified_kernel_dtb_sha256": hashlib.sha256(verified_tree).hexdigest(),
              "boot_logo": logo,
              "early_display": early_display,
              "sram_release_writes": [{"address": hex(address), "value": hex(value)}
                                           for address, value in sram_writes],
              "sram_release_after_kernel_preparation": prepare_completed[0],
              "display_register_accesses": 0, "sram_release_unrelated_bits_preserved": True,
              "entry_checksum_witness_verified": manifest.get("entry_checksum_witness", False),
              "kernel_exception_witness_verified": manifest.get("kernel_exception_witness", False),
              "early_display_modeled_in_focused_test": True, "full_display_verifier": "verify-loader-display.py",
              "cache_coherency_modeled": False, "vendor_smc_dispatch_verified": False}
    if spl:
        report["spl_sha256"] = spl_manifest["spl_sha256"]
        report["record_sha256"] = hashlib.sha256(record).hexdigest()
    (directory / ("verification-from-spl.json" if spl else "verification.json")).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("passed", "loader_sha256", "kernel_bytes_exact",
                                                  "final_call_r0_r3", "panel_results")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--spl", type=Path)
    parser.add_argument("--prepared-source", action="store_true",
                        help="use the prepared source FDT only; do not claim a live-device comparison")
    args = parser.parse_args()
    verify(args.directory, args.inventory, args.spl, args.prepared_source)
