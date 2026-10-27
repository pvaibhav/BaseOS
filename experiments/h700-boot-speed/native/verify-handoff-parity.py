#!/usr/bin/env python3
"""Compare native and vendor kernel handoffs, without accessing a device.

Run with the Unicorn/capstone Python environment, from any directory:
  /tmp/uboot-venv/bin/python experiments/h700-boot-speed/native/verify-handoff-parity.py

Missing native artifacts are built with build-release.py. Inputs can be linked
from --input-root. Existing source, loaders and verifiers are never edited.
Each capture contains the full FDT (not a reserialization), call registers,
and the bytes read at the kernel/initrd addresses. Cached captures are bound
to their inputs and emulator source hashes; --fresh replays them regardless.

Exit 0: stock plus the three approved trailing health properties, and both live validations pass. Exit 1: policy defects.
Exit 2: an unexplained difference, invalid reference, or incomplete replay.
Known defects are explained, never silently accepted. See ACCEPTED_DIFFERENCES.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import difflib
import gzip
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import traceback
import zlib

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
import fdt
import unicorn as u
from unicorn import arm_const as a

TARGETS = tuple("rg28xx rg34xx rg34xxsp rg35xxh rg35xxplus rg35xxpro rg35xxsp rg40xxh rg40xxv rgcubexx rgsp".split())
INPUT_LOCATIONS = (ROOT.parent / "main-152/work", ROOT / ".worktrees/main-152/work")
LIVE_LOCATIONS = (ROOT / "work/h700-loader", ROOT.parent.parent / "work/h700-loader")
DEFAULT_INPUTS = next((p for p in INPUT_LOCATIONS if p.is_dir()), INPUT_LOCATIONS[0])
DEFAULT_LIVE = next((p for p in LIVE_LOCATIONS if (p / "kernel-kallsyms.txt").is_file()), LIVE_LOCATIONS[-1])
SERIAL = "1c00103dc2840671ed3"
DISPLAY = "/soc@03000000/disp@01000000"
MODES = ("early-lit", "no-logo")
# No unequal hardware values are acceptable when the hardware inputs are equal.
# Add an entry only with an exact scope, an owner's accepted reason and a test.
ACCEPTED_DIFFERENCES = {name: "Owner-approved native startup health property, appended after the complete stock tree"
                        for name in ("baseos,native-boot", "baseos,native-timing", "baseos,boot-display")}


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


TR = module("parity_vendor_trace", HERE.parent / "uboot-trace.py")
NV = module("parity_native_display", HERE / "verify-loader-display.py")
BUILD = module("parity_native_build", HERE / "build.py")
BINDING = module("parity_dram_binding", HERE.parent / "variant-survey/fdt-contract.py")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def environment(raw):
    assert len(raw) == 131072 and zlib.crc32(raw[4:]) == struct.unpack_from("<I", raw)[0], "bad env CRC"
    return dict(p.decode("latin1").split("=", 1) for p in raw[4:].split(b"\0\0", 1)[0].split(b"\0") if b"=" in p)


def gpt(prefix):
    assert prefix[512:520] == b"EFI PART"
    header = bytearray(prefix[512:1024])
    size, crc = struct.unpack_from("<II", header, 12)
    struct.pack_into("<I", header, 16, 0)
    assert zlib.crc32(header[:size]) == crc
    at, count, stride = struct.unpack_from("<QII", header, 72)
    assert stride == 128 and count <= 128
    table = prefix[at * 512:at * 512 + count * stride]
    assert zlib.crc32(table) == struct.unpack_from("<I", header, 88)[0]
    parts = {}
    for i in range(count):
        entry = table[i * stride:(i + 1) * stride]
        if not any(entry[:16]):
            continue
        name = entry[56:128].decode("utf-16-le").rstrip("\0")
        first, last = struct.unpack_from("<QQ", entry, 32)
        parts[name] = (first * 512, (last - first + 1) * 512, i + 1)
    return parts


class Card:
    """Sparse read-only extents consumed by the imported SD card model."""
    def __init__(self, extents):
        self.extents = extents

    def __getitem__(self, key):
        assert isinstance(key, slice) and key.step is None
        start, end = key.start or 0, key.stop
        result = bytearray(end - start)
        for offset, blob in self.extents:
            lo, hi = max(start, offset), min(end, offset + len(blob))
            if lo < hi:
                result[lo - start:hi - start] = blob[lo - offset:hi - offset]
        return bytes(result)


def card_logo(card, parts):
    """Read the physical FAT16 BOOTLOGO.BMP and locate its short directory entry."""
    base, capacity, _ = parts["boot-resource"]
    header = card[base:base + 512]
    word = lambda at: struct.unpack_from("<H", header, at)[0]
    spc, reserved, fats, roots, fat_sectors = header[13], word(14), header[16], word(17), word(22)
    assert word(11) == 512 and fat_sectors and roots and spc, "prepared card must use FAT16"
    root = base + (reserved + fats * fat_sectors) * 512
    data = root + (roots * 32 + 511) // 512 * 512
    entry = None
    for at in range(root, root + roots * 32, 32):
        item = card[at:at + 32]
        if not item[0]:
            break
        if item[:11] == b"BOOTLOGOBMP" and not item[11] & 0x18:
            entry, entry_at = item, at
            break
    assert entry is not None, "card lacks BOOTLOGO.BMP"
    size = struct.unpack_from("<I", entry, 28)[0]
    cluster = struct.unpack_from("<H", entry, 26)[0]
    assert 54 <= size <= NV.CAP
    result, seen = bytearray(), set()
    while len(result) < size:
        assert 2 <= cluster < 0xfff0 and cluster not in seen, "malformed FAT16 cluster chain"
        seen.add(cluster)
        offset = data + (cluster - 2) * spc * 512
        assert base <= offset and offset + spc * 512 <= base + capacity
        result.extend(card[offset:offset + spc * 512])
        link = base + reserved * 512 + cluster * 2
        cluster = struct.unpack("<H", card[link:link + 2])[0]
    assert cluster >= 0xfff8
    bmp = bytes(result[:size])
    assert bmp[:2] == b"BM" and struct.unpack_from("<I", bmp, 2)[0] == size
    return bmp, entry_at


def small_logo_card(card, parts):
    """Change only BOOTLOGO.BMP to odd-sized artwork in the read-only card model."""
    _, entry = card_logo(card, parts)
    bmp = bytearray(90)
    bmp[:2] = b"BM"
    struct.pack_into("<I", bmp, 2, len(bmp))
    struct.pack_into("<IIiiHHII", bmp, 10, 54, 40, 3, 3, 1, 24, 0, 36)
    for y in range(3):
        for x in range(3):
            at = 54 + y * 12 + x * 3
            bmp[at:at + 3] = bytes((17 + x, 34 + y, 51 + x + y))
    base = parts["boot-resource"][0]
    header = card[base:base + 512]
    word = lambda at: struct.unpack_from("<H", header, at)[0]
    spc, reserved, fats, roots, sectors = header[13], word(14), header[16], word(17), word(22)
    cluster = struct.unpack("<H", card[entry + 26:entry + 28])[0]
    data = base + (reserved + fats * sectors) * 512 + (roots * 32 + 511) // 512 * 512
    extents = [(entry + 28, struct.pack("<I", len(bmp))), (data + (cluster - 2) * spc * 512, bytes(bmp))]
    extents += [(base + (reserved + i * sectors) * 512 + cluster * 2, b"\xff\xff") for i in range(fats)]
    modified = Card(card.extents + extents)
    assert card_logo(modified, parts)[0] == bytes(bmp)
    return modified, bytes(bmp)


class Stock(TR.Tracer):
    """Reuse the vendor tracer, replacing its allocation/startup shortcuts."""
    def __init__(self, code, tree, env, card, pe_data):
        self.sourcecode = code
        # SDCard only requires the GPT header as a contiguous buffer during
        # construction; later reads can use the sparse extent interface.
        super().__init__(code, tree, env, card[0:1024], pe_data)
        self.sd.card.image = card

    def replace(self, at, model):
        if at in (TR.harness.MALLOC, TR.harness.FREE, TR.CALLOC, TR.REALLOC,
                  TR.MEMALIGN, TR.harness.BOOT_JUMP_LINUX, TR.harness.ENV_GET,
                  TR.harness.ENV_SET, TR.harness.branch_target(self.sourcecode, TR.ENV_SET_CALL)):
            return
        super().replace(at, model)

    def text(self, address):
        # The parent's text() restricts reads to its artificial low heap.
        if address < 0x40000000:
            return ""
        return self.read(address, 4096).split(b"\0", 1)[0].decode("latin1")

    def configure(self, inputs):
        ram = inputs["ram_bytes"]
        self.uc.mem_map(0x40000000 + ram - 0x6000000, 0x6000000)
        self.uc.mem_write(TR.UBOOT + 0x30, struct.pack("<32I", *inputs["dram_words"]))
        self.put32(TR.UBOOT + 0x4f8, ram >> 20)
        self.uc.mem_write(TR.UBOOT + 0x4e8, bytes([0, 1, 0]))
        self.put32(TR.GD + 0x40, ram)
        self.put32(TR.GD + 0x44, 0)
        self.uc.mem_write(TR.harness.BOARD + 0x50, struct.pack("<QQ", 0x40000000, ram))
        self.put32(TR.GD + 0x64, TR.FDT)
        serial = inputs["serial"]
        for offset, word in zip((0x204, 0x208, 0x20c),
                                (int(serial[:3], 16) << 20, int(serial[3:11], 16), int(serial[11:], 16))):
            self.override[0x03006000 + offset] = word
        self.put32(TR.GD + 0x124, inputs["boot_reason"])

        def copy():
            dst, src, size = [self.arg(i) for i in range(3)]
            if size:
                self.uc.mem_write(dst, self.read(src, size))
            return dst

        def fill():
            dst, value, size = [self.arg(i) for i in range(3)]
            if size:
                self.uc.mem_write(dst, bytes([value & 255]) * size)
            return dst

        def gunzip():
            dst, capacity, src, length = [self.arg(i) for i in range(4)]
            payload = gzip.decompress(self.read(src, self.get32(length)))
            assert len(payload) <= capacity
            self.uc.mem_write(dst, payload)
            self.put32(length, len(payload))
            return 0

        super().replace(0x1080, copy)
        # The vendor's generic memmove copies backward one byte at a time,
        # including each libfdt structure insertion. Preserve overlap semantics
        # while avoiding millions of irrelevant virtual-clock hook crossings.
        super().replace(self.mapped(0x12108), copy)
        super().replace(0xfe0, fill)
        super().replace(self.mapped(0xf3c2), gunzip)
        # CPU cache/interrupt cleanup has no payload/FDT writes. Outside scope.
        super().replace(0xc7c, lambda: 0)

        def smc(machine, pc, size, data):
            call = [self.arg(i) for i in range(4)]
            if call[0] == 0x8000ff04:
                self.jump = dict(r0_r3=call, entry=call[1], fdt=call[2])
                machine.emu_stop()
            else:
                self.smc_calls.append(call)
                machine.reg_write(a.UC_ARM_REG_R0, 0)
                machine.reg_write(a.UC_ARM_REG_PC, TR.UBOOT + 0xb60)

        assert self.code[0xb5c:0xb60] == bytes.fromhex("700060e1"), "vendor final SMC moved"
        self.uc.hook_add(u.UC_HOOK_CODE, self.guarded(smc), begin=TR.UBOOT + 0xb5c, end=TR.UBOOT + 0xb5c)
        self.watch_reads("dram", TR.UBOOT + 0x30, 128)
        self.watch_reads("ram", TR.UBOOT + 0x4f8, 4)
        # Actual reserve_ram/reserve_mmu/reserve_uboot/initr_malloc, not a
        # top-of-RAM formula calibrated to the expected framebuffer address.
        for at in (0xf0b2, 0xee30, 0xee44, 0xee14, 0xee72, 0xf210):
            assert self.run(at, "reserve") == 0
        # env_init precedes relocation on a device. It marks the MMC env
        # driver valid; without it initr_env imports defaults instead of the
        # card and its allocation footprint changes the framebuffer address.
        env_init = self.mapped(0xf184) + 0x1dc
        assert self.code[env_init:env_init + 4] == bytes.fromhex("f8b50026")
        assert self.run(env_init, "env-init") == 0


def fdt_description(blob):
    assert len(blob) == fdt.total_size(blob)
    properties = {(p, n): v for p, n, _, v in fdt.walk(blob)}
    assert len(properties) == len(list(fdt.walk(blob))), "duplicate FDT property"
    # Also census empty nodes: the imported walker yields properties only.
    pos, stack, nodes = struct.unpack_from(">I", blob, 8)[0], [], []
    while True:
        token = struct.unpack_from(">I", blob, pos)[0]
        pos += 4
        if token == 1:
            end = blob.index(b"\0", pos)
            stack.append(blob[pos:end].decode())
            nodes.append("/".join(stack) or "/")
            pos = (end + 4) & ~3
        elif token == 2:
            stack.pop()
        elif token == 3:
            size = struct.unpack_from(">I", blob, pos)[0]
            pos = (pos + 8 + size + 3) & ~3
        elif token == 4:
            pass
        elif token == 9:
            break
        else:
            raise ValueError(f"invalid FDT token {token}")
    return properties, nodes, NV.V.fdt_reservations(blob)


def save_capture(directory, machine, registers, kernel_size, notes):
    directory.mkdir(parents=True, exist_ok=True)
    assert registers[0] == 0x8000ff04 and registers[3] == 1, registers
    kernel_address, tree_address = registers[1:3]
    tree = bytes(machine.mem_read(tree_address, fdt.total_size(bytes(machine.mem_read(tree_address, 40)))))
    props, nodes, reservations = fdt_description(tree)
    start, end = [int.from_bytes(props["/chosen", "linux,initrd-" + suffix], "big") for suffix in ("start", "end")]
    result = dict(r0_r3=registers, kernel_entry_registers=[tree_address, 0, 0, 0],
                  reservations=reservations, properties=len(props), nodes=len(nodes), notes=notes)
    for name, address, data in (("fdt", tree_address, tree), ("kernel", kernel_address, bytes(machine.mem_read(kernel_address, kernel_size))),
                                ("initrd", start, bytes(machine.mem_read(start, end - start)))):
        (directory / (name + ".bin")).write_bytes(data)
        result[name] = dict(address=address, bytes=len(data), sha256=sha(data))
    result["bootargs"] = props["/chosen", "bootargs"].rstrip(b"\0").decode()
    token, = [t for t in result["bootargs"].split() if t.startswith("disp_reserve=")]
    size, address = [int(v, 0) for v in token.split("=", 1)[1].split(",")]
    pixels = bytes(machine.mem_read(address, size))
    (directory / "framebuffer.bin").write_bytes(pixels)
    result["framebuffer"] = dict(address=address, bytes=size, sha256=sha(pixels))
    result["fdt_header"] = list(struct.unpack_from(">10I", tree))
    write_json(directory / "capture.json", result)
    return result


def stock_capture(directory, package, card, env, inputs, kernel_size):
    items = BUILD.toc_items(package)
    original = items["dtb"][:fdt.total_size(items["dtb"])]
    # Startup ef5c expands the TOC tree by 4096 bytes, rounded up to 32.
    tree = bytearray(original)
    capacity = (len(tree) + 0x101f) & ~31
    tree.extend(bytes(capacity - len(tree)))
    struct.pack_into(">I", tree, 4, capacity)
    tracer = Stock(items["u-boot"], bytes(tree), env, card, 0x40 if inputs["pe6_high"] else 0)
    tracer.configure(inputs)
    for at, phase in ((0x25d8, "pmic"), (0x12a30, "stdio-list"), (0xf1f8, "stdio"), (TR.DISPLAY_INIT, "display"),
                      (0x12a44, "serial"), (0xf1ee, "jumptable"), (0x113d0, "console"), (0xf26c, "storage"),
                      (0xf1bc, "flash"), (0xf174, "environment"), (TR.BOOTREASON_INIT, "reason"),
                      (TR.BOARD_LATE_INIT, "late"), (TR.MAIN_LOOP, "main")):
        if phase == "reason":
            # Boundary output of the PMIC power-on-cause decoder, also
            # supplied as the native SPL record's boot_reason enum.
            tracer.put32(TR.GD + 0x124, inputs["boot_reason"])
        result = tracer.run(at, phase)
        assert (result is None) == (phase == "main"), (phase, result, tracer.jump)
    assert tracer.jump and len(tracer.readers["dram"]) >= 24 and tracer.readers["ram"], "DRAM handoff not consumed"
    assert not tracer.resolved, ("unmodeled spinning peripheral", tracer.resolved)
    return save_capture(directory, tracer.uc, tracer.jump["r0_r3"], kernel_size,
                        dict(dram_readers=sorted(tracer.readers["dram"]), ram_readers=sorted(tracer.readers["ram"]),
                             allocator="actual vendor dlmalloc, env hash table and reserve/initr_malloc", phases=list(tracer.clock.phases),
                             non_kernel_smc=tracer.smc_calls, unknown_mmio_reads=len(tracer.unknown),
                             byte_models=["memcpy/memmove", "memset", "gunzip via host zlib with CRC/ISIZE checks"]))


class Native(NV.Run):
    # The display verifier's check() asserts native policy. Capture before
    # those assertions, so changes in policy are discovered by this tool.
    def check(self, name, machine, state, *args):
        assert len(state["calls"]) == 1 and state["result"] == 0, state["calls"]
        self.machine, self.call = machine, state["calls"][0]
        return machine


def native_capture(directory, native, inputs, mode, real_decode, bmp):
    manifest = json.loads((native / "manifest.json").read_text())
    assert sha((native / "loader.bin").read_bytes()) == manifest["loader_sha256"]
    assert sha((native / "kernel.bin").read_bytes()) == manifest["kernel_sha256"] == manifest["source_kernel_sha256"]
    words = inputs["dram_words"]
    record = bytearray(struct.pack("<4I4QI32I8s3I32s2I", 0x314e5342, 2, 240, manifest["model_id"],
                      inputs["ram_bytes"], 12000000, 12100000, 23000000, words[1], *words, bytes(8),
                      manifest["image_bytes"], manifest["image_fnv"], 0, bytes.fromhex(manifest["generation"]),
                      inputs["boot_reason"], 0))
    struct.pack_into("<I", record, 196, NV.fnv(record))
    target = manifest["target"]
    engine = json.loads((HERE / "display-engine.json").read_text())["models"][target]
    init = json.loads((HERE / "panel-init.json").read_text())
    names = init["models"][target]
    fallback = names["old"] or names["boe"]
    streams = [init["streams"][names[p] or fallback] for p in ("old", "boe")]
    descriptor = struct.pack("<4I", 0x31474f4c, len(bmp), NV.fnv(bmp), 0)
    runner = Native(native, engine, streams, bytes(record), bmp, descriptor, inputs["serial"])
    runner.run(mode, high=inputs["pe6_high"], fault="no-logo" if mode == "no-logo" else None, unpack=real_decode)
    machine = runner.machine
    result = save_capture(directory, machine, runner.call, len(runner.kernel),
                          dict(mode=mode, real_lz4_decode=real_decode, record_hex=record.hex(),
                               bmp_sha256=sha(bmp),
                               kernel_entry_registers="BL31 normalization inferred; identical vendor monitor service/arguments"))
    assert result["kernel"]["sha256"] == manifest["kernel_sha256"]
    return result


def view(value):
    if value is None:
        return None
    if value.endswith(b"\0") and any(value) and all(32 <= ch < 127 or ch == 0 for ch in value):
        return value.rstrip(b"\0").decode()
    return value.hex()


def without_native_metadata(blob):
    """Undo only trailing /chosen additions and trailing names; never repack a tree.

    Invalid order, offsets, padding or spare bytes are an encoding failure. The
    returned bytes must equal the stock capture, including its entire capacity.
    """
    start, strings, strings_size, structure_size = [struct.unpack_from(">I", blob, at)[0] for at in (8, 12, 32, 36)]
    pos, stack, additions = start, [], []
    chosen_closed = False
    while True:
        at = pos
        token = struct.unpack_from(">I", blob, pos)[0]
        pos += 4
        if token == 1:
            end = blob.index(b"\0", pos)
            stack.append(blob[pos:end].decode())
            pos = (end + 4) & ~3
        elif token == 2:
            if stack == ["", "chosen"]:
                chosen_closed = True
            stack.pop()
        elif token == 3:
            size, offset = struct.unpack_from(">II", blob, pos)
            end = blob.index(b"\0", strings + offset)
            name = blob[strings + offset:end].decode()
            pos = (pos + 8 + size + 3) & ~3
            if name in ACCEPTED_DIFFERENCES:
                assert stack == ["", "chosen"] and not chosen_closed
                assert size == {"baseos,native-boot": 32, "baseos,native-timing": 56, "baseos,boot-display": 80}[name]
                assert not any(blob[at + 12 + size:pos])
                additions.append((at, pos, name, offset))
            elif additions and not chosen_closed:
                raise AssertionError("native properties are not trailing /chosen additions")
        elif token == 4:
            assert not additions or chosen_closed, "NOP among native additions"
        elif token == 9:
            break
        else:
            raise AssertionError(f"bad FDT token {token}")
    assert [a[2] for a in additions] in (["baseos,native-boot", "baseos,native-timing"], list(ACCEPTED_DIFFERENCES))
    lo, hi = additions[0][0], additions[-1][1]
    assert all(a[1] == b[0] for a, b in zip(additions, additions[1:]))
    assert struct.unpack_from(">I", blob, hi)[0] == 2, "native additions are not at the end of /chosen"
    names = b"".join(a[2].encode() + b"\0" for a in additions)
    old_strings_size = strings_size - len(names)
    assert blob[strings + old_strings_size:strings + strings_size] == names
    cursor = old_strings_size
    for _, _, name, offset in additions:
        assert offset == cursor
        cursor += len(name) + 1
    assert not any(blob[strings + strings_size:]), "nonzero FDT spare capacity"
    assert start + structure_size == strings, "gap before strings"
    result = bytearray(blob[:lo] + blob[hi:strings + old_strings_size])
    result.extend(bytes(len(blob) - len(result)))
    for at, value in ((12, strings - (hi - lo)), (32, old_strings_size), (36, structure_size - (hi - lo))):
        struct.pack_into(">I", result, at, value)
    return bytes(result)


def policy(kind, key):
    if kind == "property" and key[0] == "/chosen" and key[1] in ACCEPTED_DIFFERENCES:
        return "native-metadata"
    return "UNEXPLAINED"


def compare(stock_dir, native_dir):
    stock, native = [json.loads((d / "capture.json").read_text()) for d in (stock_dir, native_dir)]
    sb, nb = [(d / "fdt.bin").read_bytes() for d in (stock_dir, native_dir)]
    sp, sn, sr = fdt_description(sb)
    np, nn, nr = fdt_description(nb)
    try:
        stock_plus_native = without_native_metadata(nb) == sb
    except (AssertionError, ValueError, IndexError, struct.error):
        stock_plus_native = False
    changes, bootargs_edits = [], []

    def add(kind, key, before, after, explanation=None):
        changes.append(dict(kind=kind, key=key, stock=before, native=after,
                            policy=("native-metadata" if stock_plus_native and kind == "encoding" else explanation or policy(kind, key)),
                            accepted=stock_plus_native and (kind == "encoding" or
                                kind == "property" and key[0] == "/chosen" and key[1] in ACCEPTED_DIFFERENCES)))

    for path, name in sorted(sp.keys() | np.keys()):
        key = (path, name)
        if sp.get(key) == np.get(key):
            continue
        if key == ("/chosen", "bootargs"):
            # Preserve duplicate tokens, values and order; a dict loses gpt=1
            # and the intentionally repeated loglevel settings.
            before = sp[key].rstrip(b"\0").decode().split()
            after = np[key].rstrip(b"\0").decode().split()
            sequence = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
            for tag, i, j, k, l in sequence.get_opcodes():
                if tag == "equal":
                    continue
                old, new = before[i:j], after[k:l]
                bootargs_edits.append(dict(operation=tag, stock_range=[i, j], native_range=[k, l], stock_tokens=old, native_tokens=new))
                names = sorted({v.split("=", 1)[0] for v in old + new})
                for field in names:
                    add("bootarg", field, [v for v in old if v.split("=", 1)[0] == field],
                        [v for v in new if v.split("=", 1)[0] == field])
            if before == after:
                add("property", key, view(sp[key]), view(np[key]))
        else:
            add("property", key, view(sp.get(key)), view(np.get(key)))
    if Counter(sn) != Counter(nn):
        add("nodes", "paths", sorted((Counter(sn) - Counter(nn)).elements()), sorted((Counter(nn) - Counter(sn)).elements()))
    if sr != nr:
        add("reservations", "memreserve", sr, nr)
    for name in ("r0_r3",):
        if stock[name] != native[name]:
            add("registers", name, stock[name], native[name])
    for name in ("kernel", "initrd", "fdt", "framebuffer"):
        if stock[name]["address"] != native[name]["address"]:
            add("placement", name, stock[name]["address"], native[name]["address"])
        if name != "fdt" and (stock_dir / (name + ".bin")).read_bytes() != (native_dir / (name + ".bin")).read_bytes():
            add("payload", name, stock[name], native[name])
    if sb != nb:
        add("encoding", "full-fdt", stock["fdt"], native["fdt"])
    return dict(changes=changes, stock_plus_native_metadata=stock_plus_native,
                bootargs_token_edits=bootargs_edits, raw_fdt_equal=sb == nb, property_counts=[len(sp), len(np)],
                node_counts=[len(sn), len(nn)], kernel_bytes_equal=(stock_dir / "kernel.bin").read_bytes() == (native_dir / "kernel.bin").read_bytes(),
                initrd_bytes_equal=(stock_dir / "initrd.bin").read_bytes() == (native_dir / "initrd.bin").read_bytes(),
                framebuffer_bytes_equal=(stock_dir / "framebuffer.bin").read_bytes() == (native_dir / "framebuffer.bin").read_bytes(),
                registers_equal=stock["r0_r3"] == native["r0_r3"],
                fdt_header_changes=[dict(word=i, stock=x, native=y) for i, (x, y) in enumerate(zip(stock["fdt_header"], native["fdt_header"])) if x != y])


def prepare(target, args, build=True):
    directory = ROOT / "work" / target
    directory.mkdir(exist_ok=True)
    for name in ("boot-gzip", "boot-prefix.img", "source.json", "bootlogo.bmp"):
        path = directory / name
        if not path.exists():
            source = args.input_root / target / name
            assert source.exists(), f"missing input {source}"
            path.symlink_to(source.resolve())
    if not build:
        return
    native = args.build_root / target
    if not (native / "native/manifest.json").exists():
        assert not native.exists(), f"incomplete build {native}; choose a fresh --build-root"
        log = args.output / (target + ".build.log")
        with log.open("w") as stream:
            subprocess.run([sys.executable, str(HERE / "build-release.py"), target, str(native)], stdout=stream, stderr=subprocess.STDOUT, check=True)
    return native / "native"


def capture_cached(directory, fingerprint, callback, fresh):
    meta = directory / "inputs.json"
    try:
        if not fresh and meta.exists() and json.loads(meta.read_text()) == fingerprint:
            record = json.loads((directory / "capture.json").read_text())
            if all(sha((directory / (name + ".bin")).read_bytes()) == record[name]["sha256"] for name in ("fdt", "kernel", "initrd", "framebuffer")):
                return record
    except (OSError, ValueError, KeyError):
        pass  # A partial/interrupted cache is reconstructed, never accepted.
    # Payload deduplication is applied after a run. Detach any shared inode
    # before a fresh capture so changed bytes cannot rewrite another case.
    for name in ("fdt", "kernel", "initrd", "framebuffer"):
        path = directory / (name + ".bin")
        if path.exists() and path.stat().st_nlink > 1:
            private = path.with_suffix(".private-tmp")
            private.write_bytes(path.read_bytes())
            os.replace(private, path)
    result = callback()
    write_json(meta, fingerprint)
    return result


def source_hashes():
    paths = [HERE.parent / "uboot-trace.py", ROOT / "tests/test-fast-boot-emulation.py", HERE.parent / "fdt.py",
             HERE / "verify-loader-display.py", HERE / "verify.py", HERE / "build.py",
             HERE / "display-engine.json", HERE / "panel-init.json", HERE / "panel-contract.json"]
    hashes = {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in paths}
    # Report prose and classification edits do not invalidate an expensive
    # capture; all code which can change captured bytes remains in the key.
    capture_code = "\n".join(inspect.getsource(obj) for obj in
                             (environment, gpt, Card, card_logo, small_logo_card, Stock, fdt_description, save_capture, stock_capture, Native, native_capture))
    hashes["parity_capture_code"] = sha(capture_code.encode())
    hashes["unicorn"] = u.__version__
    hashes["capstone"] = NV.c.__version__
    return hashes


def run_target(target, options):
    args = argparse.Namespace(**options)
    native = args.build_root / target / "native"
    manifest = json.loads((native / "manifest.json").read_text())
    for name in ("loader", "image", "kernel", "monitor"):
        assert sha((native / (name + ".bin")).read_bytes()) == manifest[name + "_sha256"], f"changed native {name} bytes"
    prefix = (ROOT / "work" / target / "boot-prefix.img").read_bytes()
    parts = gpt(prefix)
    at, size, _ = parts["env"]
    env_raw = prefix[at:at + 131072]
    env = environment(env_raw)
    boot = (ROOT / "work" / target / "boot-gzip/boot.img").read_bytes()
    package = (ROOT / "work" / target / "boot-gzip/boot-package.bin").read_bytes()
    card = Card([(0, prefix), (parts["boot"][0], boot)])
    bmp, entry_at = card_logo(card, parts)
    no_logo_card = Card(card.extents + [(entry_at, b"\xe5")])
    banks = json.loads((HERE.parent / "evidence/compatibility.json").read_text())["targets"][target]["dram_candidates"]
    contract = json.loads((HERE.parent / "evidence/compatibility.json").read_text())["targets"][target]
    assert sha(package) == contract["package_sha256"]
    items = BUILD.toc_items(package)
    stock_code = items["u-boot"]
    tree_size = fdt.total_size(items["dtb"])
    startup_capacity = (tree_size + 0x101f) & ~31
    assert len(items["dtb"]) >= startup_capacity and not any(items["dtb"][tree_size:startup_capacity]), "stock FDT spare bytes need explicit modeling"
    assert manifest["monitor_sha256"] == manifest["source_monitor_sha256"] == sha(items["monitor"]), "native monitor differs from stock"
    reference_code = BUILD.toc_items((ROOT / "work/rgsp/boot-gzip/boot-package.bin").read_bytes())["u-boot"]
    code_check = TR.harness.same_code(reference_code, stock_code)
    code_check["string_sites"] = TR.harness.same_strings(stock_code)
    for path, digest in manifest["persistent_source_hashes"].items():
        assert sha((ROOT / path).read_bytes()) == digest, f"stale native build source: {path}"
    provenance = dict(package_sha256=sha(package), env_sha256=sha(env_raw), boot_sha256=sha(boot),
                      prefix_sha256=sha(prefix), logo_sha256=sha(bmp),
                      native_manifest_sha256=sha((native / "manifest.json").read_bytes()), sources=source_hashes())
    provenance["vendor_common_code_check"] = code_check
    rows = []
    for index, bank in enumerate(banks):
        words = [int(word, 0) for word in bank["words"]]
        offset = int(bank["offset"], 0)
        assert prefix[8192 + offset:8192 + offset + 128] == struct.pack("<32I", *words)
        # Prepared alternate banks may have no trained size. Supply one valid
        # common trained result; never feed an untrained zero-size handoff.
        ram = (words[7] >> 16) << 20
        if not ram:
            # Keep a valid trained geometry: bank bits + rows + columns +
            # 32-bit bus bytes, then rank/half-width factors. The alternate
            # 0x310a single-rank and 0x30fa dual-rank fixtures are both 2 GiB.
            row_bits, column_bits = (words[6] >> 4) & 255, words[6] & 15
            ram = (1 << (row_bits + column_bits + 5)) * (2 if words[7] & 0x1000 else 1) // (2 if words[7] & 1 else 1)
        assert ram in (0x40000000, 0x80000000)
        words[7] = (ram >> 20 << 16) | (words[7] & 0xffff)
        for high in (False, True):
            case = f"bank{index}-{bank['type'].lower()}-pe6-{'high' if high else 'low'}"
            inputs = dict(dram_words=words, ram_bytes=ram, serial=SERIAL, boot_reason=5, pe6_high=high)
            base = args.output / target / case
            stamp = dict(provenance={k: v for k, v in provenance.items() if k != "vendor_common_code_check"}, hardware=inputs)
            stock = base / "stock"
            capture_cached(stock, stamp, lambda: stock_capture(stock, package, card, env, inputs, (native / "kernel.bin").stat().st_size), args.fresh)
            for mode in MODES:
                directory = base / mode
                reference = stock
                if mode == "no-logo":
                    reference = base / "stock-no-logo"
                    capture_cached(reference, dict(**stamp, logo_present=False),
                                   lambda: stock_capture(reference, package, no_logo_card, env, inputs, (native / "kernel.bin").stat().st_size), args.fresh)
                real_decode = args.real_decode or (index == 0 and not high and mode == "early-lit")
                capture_cached(directory, dict(**stamp, mode=mode, real_decode=real_decode),
                               lambda: native_capture(directory, native, inputs, mode, real_decode, bmp), args.fresh)
                row = dict(case=case, mode=mode, hardware=inputs, bank_offset=bank["offset"],
                           **compare(reference, directory))
                rows.append(row)
                write_json(base / (mode + ".diff.json"), row)
    # One additional equal-input case per model guards transparent canvas bytes
    # and crop coordinates that full-frame prepared artwork cannot exercise.
    inputs, case = rows[0]["hardware"], rows[0]["case"]
    base = args.output / target / case
    small_card, small_bmp = small_logo_card(card, parts)
    stamp = dict(provenance={k: v for k, v in provenance.items() if k != "vendor_common_code_check"},
                 hardware=inputs, logo_variant="centered-3x3", logo_sha256=sha(small_bmp))
    stock, directory = base / "stock-small-logo", base / "small-logo"
    capture_cached(stock, stamp, lambda: stock_capture(stock, package, small_card, env, inputs, (native / "kernel.bin").stat().st_size), args.fresh)
    capture_cached(directory, stamp, lambda: native_capture(directory, native, inputs, "small-logo", False, small_bmp), args.fresh)
    row = dict(case=case, mode="small-logo", hardware=inputs, **compare(stock, directory))
    consumer = module("parity_small_logo_consumer", HERE / "verify-kernel-logo.py")
    raw = fdt.get((directory / "fdt.bin").read_bytes(), DISPLAY, "boot_fb0")
    fields = [int(value, 16) for value in raw.rstrip(b"\0").decode().split(",")]
    row["kernel_consumer"] = consumer.run_case(native, fields[1], fields[2],
        pixels=(directory / "framebuffer.bin").read_bytes(), property_value=raw, legacy_reserve=True,
        warm=True, physical_address=fields[0], crop=tuple(fields[5:]))
    rows.append(row)
    write_json(base / "small-logo.diff.json", row)
    result = dict(target=target, provenance=provenance, cases=rows,
                  policy_counts=dict(Counter(c["policy"] for row in rows for c in row["changes"] if not c["accepted"])),
                  unexplained=sum(c["policy"] == "UNEXPLAINED" for row in rows for c in row["changes"]))
    write_json(args.output / target / "summary.json", result)
    return result


def validate_live(target, args):
    inventory = args.live_root / (target + "-inventory-v1")
    record = json.loads((inventory / "inventory.json").read_text())
    live = (inventory / "live.dtb").read_bytes()
    package = (inventory / "toc1.bin").read_bytes()
    source_tree = BUILD.toc_items(package)["dtb"]
    source_size = fdt.total_size(source_tree)
    capacity = (source_size + 0x101f) & ~31
    assert len(source_tree) >= capacity and not any(source_tree[source_size:capacity]), "live FDT spare bytes need explicit modeling"
    words = list(struct.unpack_from("<32I", (inventory / "boot0.bin").read_bytes(), 0x38))
    # Use the card's environment/GPT/image only for live validation. None of
    # these values leak into release parity cases.
    prefix = (ROOT / "work" / target / "boot-prefix.img").read_bytes()
    lab_prefix = (inventory / "prefix.bin").read_bytes()
    parts = gpt(lab_prefix)
    env = (inventory / "env.bin").read_bytes()
    boot = (inventory / "boot.bin").read_bytes()
    card = Card([(0, prefix), (0, lab_prefix), (parts["env"][0], env), (parts["boot"][0], boot)])
    props, _, _ = fdt_description(live)
    command = props["/chosen", "bootargs"].rstrip(b"\0").decode().split()
    reason = next(t.split("=", 1)[1] for t in command if t.startswith("bootreason="))
    inputs = dict(dram_words=words, ram_bytes=(words[7] >> 16) << 20, serial=record["serial"],
                  boot_reason=("button", "irq", "usb", "charger", "battery", "unknow").index(reason),
                  pe6_high=record["lcd_type"] == "old")
    directory = args.output / "live-validation" / target
    fingerprint = dict(inputs=inputs, package=sha(package), env=sha(env), boot=sha(boot),
                       prefix=sha(lab_prefix), logo_card=sha(prefix), sources=source_hashes())
    capture_cached(directory, fingerprint, lambda: stock_capture(directory, package, card, environment(env), inputs,
                   len(gzip.decompress(boot[2048:2048 + struct.unpack_from("<I", boot, 8)[0]]))), args.fresh)
    replay = (directory / "fdt.bin").read_bytes()
    before, bn, br = fdt_description(live)
    after, an, ar = fdt_description(replay)
    differences = [dict(path=p, property=n, live=view(before.get((p, n))), replay=view(after.get((p, n))))
                   for p, n in sorted(before.keys() | after.keys()) if before.get((p, n)) != after.get((p, n))]
    result = dict(target=target, inputs=inputs, byte_exact=live == replay, live_sha256=sha(live), replay_sha256=sha(replay),
                  properties_exact=not differences, nodes_exact=bn == an, reservations_exact=br == ar,
                  header_live=list(struct.unpack_from(">10I", live)), header_replay=list(struct.unpack_from(">10I", replay)),
                  differences=differences, sources={name: sha((inventory / name).read_bytes()) for name in ("toc1.bin", "env.bin", "boot0.bin", "live.dtb")})
    write_json(directory / "validation.json", result)
    return result


def kernel_evidence(args, targets):
    """Replay kernel consumers at both proven addresses, including the blank path."""
    consumer = module("parity_kernel_consumer", HERE / "verify-kernel-logo.py")
    rows = []
    for target in targets:
        native = args.build_root / target / "native"
        manifest = json.loads((native / "manifest.json").read_text())
        logo = manifest["boot_logo"]
        blob = (native / "kernel.bin").read_bytes()
        rows.append(dict(target=target, kernel_sha256=sha(blob),
                         strings={s: blob.count(s.encode()) for s in
                                  ("uboot_message", "disp_reserve", "boot_fb", "boot_disp", "baseos,native", "baseos,boot-display")},
                         consumer_cases=[consumer.run_case(native, logo["width"], logo["height"], legacy_reserve=legacy, warm=warm)
                                         for legacy, warm in ((False, False), (True, False), (True, True))] +
                         [consumer.run_case(native, logo["width"], logo["height"], present=present, legacy_reserve=True, warm=True,
                                            physical_address=0x40000000 + ram - logo["framebuffer_from_ram_end"])
                          for ram in logo["proven_ram_bytes"] for present in (True, False)]))
    kallsyms = args.live_root / "kernel-kallsyms.txt"
    symbols = {}
    for line in kallsyms.read_text().splitlines():
        address, _, name = line.split()[:3]
        if name in ("early_disp_reserve", "disp_reserve_mem", "Fb_copy_boot_fb.isra.6", "disp_reserve_size", "disp_reserve_base"):
            symbols[name] = address
    assert len(symbols) == 5
    write_json(args.output / "kernel-consumer.json", dict(verifier_sha256=sha((HERE / "verify-kernel-logo.py").read_bytes()),
               kallsyms_sha256=sha(kallsyms.read_bytes()), reference_symbols=symbols, models=rows))


def difference_gate_self_test(args, result):
    """Mutate real captures and require the exact unexpected field to be reported."""
    row = next(r for r in result["cases"] if r["mode"] == "early-lit")
    base = args.output / result["target"] / row["case"]
    original = base / "early-lit"
    tree = (original / "fdt.bin").read_bytes()
    initrd = (original / "initrd.bin").read_bytes()
    metadata = (original / "capture.json").read_text()
    assert compare(base / "stock", original)["stock_plus_native_metadata"], "baseline is not stock plus trailing native properties"
    checks = []
    with tempfile.TemporaryDirectory(prefix="gate-", dir=args.output) as temporary:
        native = Path(temporary)
        (native / "kernel.bin").symlink_to((original / "kernel.bin").resolve())
        for test in ("dram-property", "bootarg", "empty-node", "reservation", "register", "initrd-byte", "kernel-placement",
                     "spare-byte", "native-order", "unapproved-chosen", "framebuffer-byte"):
            blob, payload, meta = bytearray(tree), bytearray(initrd), json.loads(metadata)
            pixels = bytearray((original / "framebuffer.bin").read_bytes())
            expected_kind = expected_key = None
            if test == "dram-property":
                value = bytearray(fdt.get(blob, "/dram", "dram_clk")); value[-1] ^= 1
                fdt.set_same_size(blob, "/dram", "dram_clk", value)
                expected_kind, expected_key = "property", ("/dram", "dram_clk")
            elif test == "bootarg":
                value = fdt.get(blob, "/chosen", "bootargs")
                value = re.sub(b"lcd_type=(?:old|boe)", b"lcd_type=bad", value)
                fdt.set_same_size(blob, "/chosen", "bootargs", value)
                expected_kind, expected_key = "bootarg", "lcd_type"
            elif test == "empty-node":
                start, strings, size = [struct.unpack_from(">I", blob, at)[0] for at in (8, 12, 36)]
                name = b"unexpected-empty\0"
                addition = struct.pack(">I", 1) + name + bytes(-len(name) % 4) + struct.pack(">I", 2)
                at = start + size - 8
                blob = blob[:at] + addition + blob[at:len(blob) - len(addition)]
                struct.pack_into(">I", blob, 12, strings + len(addition))
                struct.pack_into(">I", blob, 36, size + len(addition))
                expected_kind, expected_key = "nodes", "paths"
            elif test == "reservation":
                entries = NV.V.fdt_reservations(blob)
                at = struct.unpack_from(">I", blob, 16)[0] + (len(entries) - 1) * 16
                struct.pack_into(">Q", blob, at, entries[-1][0] ^ 4)
                expected_kind, expected_key = "reservations", "memreserve"
            elif test == "register":
                meta["r0_r3"][2] ^= 4
                expected_kind, expected_key = "registers", "r0_r3"
            elif test == "initrd-byte":
                payload[0] ^= 1
                expected_kind, expected_key = "payload", "initrd"
            elif test == "kernel-placement":
                meta["kernel"]["address"] ^= 64
                expected_kind, expected_key = "placement", "kernel"
            elif test == "spare-byte":
                blob[-1] ^= 1
                expected_kind, expected_key = "encoding", "full-fdt"
            elif test == "native-order":
                additions = [(at - 12, 12 + (len(value) + 3 & ~3)) for path, name, at, value in fdt.walk(blob)
                             if path == "/chosen" and name in ACCEPTED_DIFFERENCES]
                (at, size), (following, length) = additions[:2]
                assert at + size == following
                blob[at:following + length] = blob[following:following + length] + blob[at:following]
                expected_kind, expected_key = "encoding", "full-fdt"
            elif test == "unapproved-chosen":
                assert blob.count(b"baseos,native-boot\0") == 1
                blob = blob.replace(b"baseos,native-boot\0", b"baseos,native-evil\0")
                expected_kind, expected_key = "property", ("/chosen", "baseos,native-evil")
            else:
                pixels[0] ^= 1
                expected_kind, expected_key = "payload", "framebuffer"
            (native / "fdt.bin").write_bytes(blob)
            (native / "initrd.bin").write_bytes(payload)
            (native / "framebuffer.bin").write_bytes(pixels)
            write_json(native / "capture.json", meta)
            differences = compare(base / "stock", native)["changes"]
            detected = any(d["kind"] == expected_kind and d["key"] == expected_key and d["policy"] == "UNEXPLAINED" for d in differences)
            assert detected, (test, differences)
            checks.append(dict(mutation=test, reported_kind=expected_kind, reported_key=expected_key, passed=True))
    write_json(args.output / "gate-self-test.json", dict(target=result["target"], case=row["case"], checks=checks))


def deduplicate_payloads(output):
    """Keep every per-case byte capture, sharing identical payload storage."""
    canonical = {}
    for capture in sorted(output.rglob("capture.json")):
        metadata = json.loads(capture.read_text())
        for name in ("kernel", "initrd"):
            path = capture.parent / (name + ".bin")
            key = (name, metadata[name]["sha256"])
            assert sha(path.read_bytes()) == key[1], f"capture bytes changed: {path}"
            if key not in canonical:
                canonical[key] = path
            elif not path.samefile(canonical[key]):
                temporary = path.with_suffix(".dedup-link")
                if temporary.exists():
                    temporary.unlink()
                os.link(canonical[key], temporary)
                os.replace(temporary, path)


POLICY_EXPLANATIONS = """## What is checked

The stock replay executes each pinned vendor's reserve, allocator, environment,
FAT/logo, FDT and boot sequence. Its full FDT bytes are validated independently
against the recorded RG40XX V and RG34XX SP live FDTs. No device is accessed.
The native replay executes the linked release loader from entry to its final
A32 monitor request. Every boot0 bank slot is crossed with both PE6 straps,
including 1 GiB and 2 GiB inputs, with and without the physical card's logo.
The first case per model runs the real native LZ4 decoder.
One extra centered 3-by-3 logo per model checks transparent padding and the
exact crop descriptor, then executes the kernel's cropped copy/free path.

The only accepted differences are the owner's three /chosen health properties.
They must follow every stock /chosen property, in order, with their names at the
end of the strings table. The checker removes only those exact byte spans and
compares the remaining full FDT to stock, with identical header capacity,
reservation map, structure order, strings, padding and explicitly zero spare
bytes. It does not reserialize or pack either tree. Every other property,
bootarg token (including duplicates/order), byte, address, payload and measured
A32 monitor argument must match. Framebuffer pixels are compared directly at
the address supplied by each loader's disp_reserve token.

`derive-handoff.py --jobs 4` regenerates the pinned per-model allocation rule
with actual vendor allocation code at both supported RAM sizes, with/without
the logo. Native rejects every other RAM size in SPL and A32. The loader uses
the selected address for conversion, DE layer programming and cache cleaning.
The A32 verifier checks all MMU section descriptors, including the high buffer.
SPL still reads only the BMP into its mapped low arena; it never accesses the
high pixels, so its A64 mappings and full clean-to-PoC path remain applicable.

`kernel-consumer.json` executes the actual vendor framebuffer copy, display
request and reservation cleanup paths for each model. Generated-input consumer
checks also use the captured native boot_fb0 and pixels with the stock
command-line reservation, checking the exact memblock_free address/size.

## Where host proof stops

The model assumes the supplied post-training DRAM words, SID, PMIC boot-reason
classification and straps describe the same hardware inputs. It does not
execute DRAM electrical training or prove clocks/rails/pinmux and peripheral
state identical at Linux entry. MMIO polls, counter values, PLL lock and TCON
scanlines are modeled. Recorded cache maintenance requests and exact pixels do
not prove physical DMA coherency or scanout. The equal A32 monitor request is
measured; BL31's A64 entry normalization is inferred, not replayed per case.
Hardware must confirm uninterrupted logo takeover (and blank-logo startup),
physical cache visibility, panel/backlight timing, the freed display memory,
HDMI on a TV, sleep/wake and cold/charger starts on each model/panel/RAM variant.

Unused framebuffer RAM starts zero in this replay. A separate vendor allocation
and write trace found that the absent-logo buffer is not written at this
boundary; the canvas outside a small logo's crop is also untouched. Native
clears those bytes for blank scanout. Their byte identity on hardware is not
established by the full-buffer comparison under this fixture. Actual-kernel
consumer runs with zero versus poisoned unused pixels produce identical output
and reserve/free calls: absent-logo starts never map the source, and cropped
logos copy only the advertised rectangle. The standalone evidence is in
`work/unused-framebuffer-proof.json`. Initial RAM contents and physical DE
scanout remain outside this host proof. No extra FDT or consumed-pixel
differences are allowlisted.

## Previous defects

The original e7a7cec gate exposed the missing vendor version token (introduced
by 205f677), native permanent framebuffer reservation/low address and cold
no-logo handoff (96d108d), and fixed 1 MiB FDT encoding (205f677). Those are
regressions to prevent, not accepted differences. The owner's later decision
keeps only the three native health properties. HDMI's exact stock `okay`
status remains covered (fe3872e). The lab card's older partition map is used
only for live stock validation; release parity uses release GPT/environment.
"""


def report(args, results, validations, errors):
    total = len(args.target or TARGETS)
    verdict = ("Native does not meet strict handoff transparency: every listed policy difference remains a defect."
               if any(r["policy_counts"] for r in results) else "Completed captures match stock, with only the approved trailing health properties.")
    lines = ["# H700 kernel handoff parity", "", "Host-only; no device access. Release loader and stock vendor instructions are replayed.", "",
             "## Result", "", f"{len(results)}/{total} selected models completed. {verdict}", "",
             "Identical inputs require identical handoffs. Only the three trailing native /chosen health properties are accepted.", "",
             "| Target | Hardware cases | Native variants | Policy differences | Unexplained | Payload/register parity |",
             "|---|---:|---:|---|---:|---|"]
    for row in sorted(results, key=lambda r: TARGETS.index(r["target"])):
        cases = row["cases"]
        passed = all(c["kernel_bytes_equal"] and c["initrd_bytes_equal"] and c["framebuffer_bytes_equal"] and c["registers_equal"] for c in cases)
        lines.append(f"| {row['target']} | {len({c['case'] for c in cases})} | {len(cases)} | {', '.join(row['policy_counts'])} | {row['unexplained']} | {'exact' if passed else 'FAIL'} |")
    lines.extend(["", "## Stock replay validation", "",
                  "These runs use the corresponding lab GPT, environment, TOC, boot0 DRAM words, serial, boot reason and strap. The release runs use release inputs independently.", "",
                  "| Unit | Every FDT byte | Bytes | Live SHA256 (equals replay) | Properties / reservations |", "|---|---|---:|---|---|"])
    for v in validations:
        lines.append(f"| {v['target']} | {v['byte_exact']} | {v['header_live'][1]} | {v['live_sha256']} | {v['properties_exact']} / {v['reservations_exact']} |")
    if errors:
        lines.extend(["", "## Incomplete runs", ""] + [f"- {error}" for error in errors])
    lines.extend(["", "## Representative release values by target", "",
                  "The first low-strap bank is shown; every bank/strap/variant has a complete JSON diff.", "",
                  "| Target | RAM MiB | Stock framebuffer bases | Native framebuffer bases | Stock / native FDT bytes | Vendor version token | Unapproved fields by variant |",
                  "|---|---|---|---|---|---|---|"])
    for result in sorted(results, key=lambda r: TARGETS.index(r["target"])):
        rows = result["cases"]
        row = next(r for r in rows if r["mode"] == "early-lit")
        base = args.output / result["target"] / row["case"]
        stock, native = [(base / name / "fdt.bin").read_bytes() for name in ("stock", "early-lit")]
        before = fdt.get(stock, DISPLAY, "boot_fb0")
        after = fdt.get(native, DISPLAY, "boot_fb0")
        addresses, native_addresses = set(), set()
        for case in rows:
            if case["mode"] == "early-lit":
                path = args.output / result["target"] / case["case"] / "stock/fdt.bin"
                description = fdt.get(path.read_bytes(), DISPLAY, "boot_fb0")
                if description:
                    addresses.add("0x" + description.split(b",", 1)[0].decode())
                description = fdt.get((path.parent.parent / "early-lit/fdt.bin").read_bytes(), DISPLAY, "boot_fb0")
                if description:
                    native_addresses.add("0x" + description.split(b",", 1)[0].decode())
        ram = "/".join(str(size) for size in sorted({r["hardware"]["ram_bytes"] >> 20 for r in rows}))
        vendor = json.loads((base / "stock/capture.json").read_text())
        version = next(t for t in vendor["bootargs"].split() if t.startswith("uboot_message="))
        native_base = ", ".join(sorted(native_addresses)) or "absent"
        counts = "; ".join(f"{mode}: {sum(not d['accepted'] for d in next(r for r in rows if r['mode'] == mode)['changes'])}" for mode in (*MODES, "small-logo"))
        lines.append(f"| {result['target']} | {ram} | {', '.join(sorted(addresses))} | {native_base} | {len(stock)} / {len(native)} | {version} | {counts} |")
    lines.extend(["", POLICY_EXPLANATIONS])
    lines.extend(["", "## Detailed evidence", "",
                  "Each target/case contains full `stock/fdt.bin`, `kernel.bin`, `initrd.bin`, capture metadata, identical input blocks and each native variant. `*.diff.json` lists every differing property/token/reservation/header field. `summary.json` includes input and source hashes. Identical kernel/initrd captures share hard-linked storage. `gate-self-test.json` records mutations of real captures which must be reported as unexplained: a DRAM property, bootarg, empty node, reservation, register, initrd byte and kernel placement.", "",
                  "## Rerun", "", "```sh", f"{sys.executable} experiments/h700-boot-speed/native/verify-handoff-parity.py", "```", "",
                  "Use `--target rg40xxv` for one model; `--fresh` replays valid caches; `--real-decode` executes native LZ4 on every replay. A real linked LZ4 decode is always performed for the first early-lit case per model; other cases reuse the imported verifier's explicit decoder model. Stock gzip uses host zlib, checks CRC/ISIZE and writes the decompressed bytes to the address supplied by U-Boot.", ""])
    lines.extend(["Exit 1 means explained policy defects remain; exit 2 means an unexplained difference or incomplete/invalid replay. Zero requires exact parity and both byte-exact live validations.", ""])
    (args.output / "REPORT.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", action="append", choices=TARGETS)
    parser.add_argument("--output", type=Path, default=ROOT / "work/handoff-parity")
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--live-root", type=Path, default=DEFAULT_LIVE)
    parser.add_argument("--build-root", type=Path, default=ROOT / "work/native-check")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--real-decode", action="store_true")
    args = parser.parse_args()
    assert args.jobs > 0
    for name in ("output", "input_root", "live_root", "build_root"):
        setattr(args, name, getattr(args, name).resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    targets = args.target or list(TARGETS)
    results, validations, errors = [], [], []
    for target in targets:
        prepare(target, args)
    prepare("rgsp", args, build=False)
    try:
        kernel_evidence(args, targets)
    except Exception:
        errors.append("kernel consumer: " + traceback.format_exc())
    for target in ("rg40xxv", "rg34xxsp"):
        try:
            result = validate_live(target, args)
            validations.append(result)
            print(f"live {target}: bytes={'exact' if result['byte_exact'] else 'FAIL'}, differing properties={len(result['differences'])}", flush=True)
        except Exception:
            errors.append("live " + target + ": " + traceback.format_exc())
            print(errors[-1], file=sys.stderr, flush=True)
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        tasks = {pool.submit(run_target, target, vars(args)): target for target in targets}
        for task in as_completed(tasks):
            target = tasks[task]
            try:
                result = task.result()
                results.append(result)
                print(f"{target}: {len(result['cases'])} comparisons; policies={','.join(result['policy_counts'])}; unexplained={result['unexplained']}", flush=True)
            except Exception:
                errors.append(target + ": " + traceback.format_exc())
                print(errors[-1], file=sys.stderr, flush=True)
            report(args, results, validations, errors)
    status = 2 if errors or len(validations) != 2 or any(not v["byte_exact"] for v in validations) or any(r["unexplained"] for r in results) else (1 if any(r["policy_counts"] for r in results) else 0)
    if results:
        try:
            deduplicate_payloads(args.output)
            difference_gate_self_test(args, results[0])
        except Exception:
            errors.append("difference gate self-test: " + traceback.format_exc())
            status = 2
    write_json(args.output / "summary.json", dict(targets=results, live_validation=validations, errors=errors,
               accepted_differences=ACCEPTED_DIFFERENCES, exit_status=status, device_access=False,
               verifier_sha256=sha(Path(__file__).read_bytes()),
               runtime=dict(python=sys.version, unicorn=u.__version__, capstone=NV.c.__version__)))
    report(args, results, validations, errors)
    print(f"Report: {args.output / 'REPORT.md'}; exit {status}", flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
