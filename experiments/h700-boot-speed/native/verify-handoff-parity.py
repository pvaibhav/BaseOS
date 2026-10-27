#!/usr/bin/env python3
"""Compare native and vendor kernel handoffs, without accessing a device.

Run with the Unicorn/capstone Python environment, from any directory:
  /tmp/uboot-venv/bin/python experiments/h700-boot-speed/native/verify-handoff-parity.py

Missing native artifacts are built with build-release.py. Inputs can be linked
from --input-root. Existing source, loaders and verifiers are never edited.
Each capture contains the full FDT (not a reserialization), call registers,
and the bytes read at the kernel/initrd addresses. Cached captures are bound
to their inputs and emulator source hashes; --fresh replays them regardless.

Exit 0: exact parity and both live validations pass. Exit 1: policy defects.
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
MODES = ("early-lit", "no-logo", "logo-only")
# No unequal hardware values are acceptable when the hardware inputs are equal.
# Add an entry only with an exact scope, an owner's accepted reason and a test.
ACCEPTED_DIFFERENCES: dict[str, str] = {}


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
    runner.run(mode, high=inputs["pe6_high"], fault=None if mode == "early-lit" else "no-logo", unpack=real_decode)
    machine = runner.machine
    if mode == "logo-only":
        # The linked logo-only producer is the verifier's non-early path.
        # No source mutation: run it on the cold FDT after the complete loader.
        machine.mem_write(NV.DESC, descriptor)
        machine.mem_write(NV.BMP, bmp)
        stop = 0x4a0d0000
        machine.hook_add(u.UC_HOOK_CODE, lambda m, *_: m.emu_stop(), begin=stop, end=stop)
        machine.reg_write(a.UC_ARM_REG_CPSR, 0x1d3)
        machine.reg_write(a.UC_ARM_REG_R0, runner.call[2])
        machine.reg_write(a.UC_ARM_REG_SP, 0x4a0f0000)
        machine.reg_write(a.UC_ARM_REG_LR, stop)
        machine.emu_start(runner.sym["baseos_prepare_logo"], stop, count=100000000)
        assert machine.reg_read(a.UC_ARM_REG_PC) == stop and machine.reg_read(a.UC_ARM_REG_R0) == 1
    result = save_capture(directory, machine, runner.call, len(runner.kernel),
                          dict(mode=mode, real_lz4_decode=real_decode, record_hex=record.hex(),
                               bmp_sha256=sha(bmp), diagnostic_only=mode == "logo-only",
                               kernel_entry_registers="BL31 normalization inferred; identical vendor monitor service/arguments"))
    assert result["kernel"]["sha256"] == manifest["kernel_sha256"]
    return result


def view(value):
    if value is None:
        return None
    if value.endswith(b"\0") and any(value) and all(32 <= ch < 127 or ch == 0 for ch in value):
        return value.rstrip(b"\0").decode()
    return value.hex()


def policy(kind, key):
    if kind == "property":
        path, name = key
        if path == "/chosen" and name in ("baseos,native-boot", "baseos,native-timing", "baseos,boot-display"):
            return "native-metadata"
        if path == DISPLAY and name == "boot_fb0":
            return "framebuffer"
        if path == DISPLAY and name in ("boot_disp", "boot_disp1", "boot_disp2"):
            return "display-ownership"
    if kind == "bootarg" and key in ("uboot_message", "disp_reserve"):
        return "version-message" if key == "uboot_message" else "framebuffer"
    if kind == "reservations":
        return "framebuffer"  # checked against an exact framebuffer reservation below
    if kind == "encoding":
        return "fdt-encoding"
    return "UNEXPLAINED"


def compare(stock_dir, native_dir):
    stock, native = [json.loads((d / "capture.json").read_text()) for d in (stock_dir, native_dir)]
    sb, nb = [(d / "fdt.bin").read_bytes() for d in (stock_dir, native_dir)]
    sp, sn, sr = fdt_description(sb)
    np, nn, nr = fdt_description(nb)
    changes, bootargs_edits = [], []

    def add(kind, key, before, after, explanation=None):
        changes.append(dict(kind=kind, key=key, stock=before, native=after,
                            policy=explanation or policy(kind, key), accepted=False))

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
        expected = [entry for entry in nr if entry not in sr]
        fb = np.get((DISPLAY, "boot_fb0"))
        owned = None
        if fb:
            fields = [int(v, 16) for v in fb.rstrip(b"\0").decode().split(",")]
            owned = (fields[0], fields[1] * fields[2] * 4)
        # Never classify an arbitrary reservation delta as a known logo defect.
        reason = "framebuffer" if expected == [owned] and all(e in nr for e in sr) and nr == sr + expected else "UNEXPLAINED"
        add("reservations", "memreserve", sr, nr, reason)
    for name in ("r0_r3",):
        if stock[name] != native[name]:
            add("registers", name, stock[name], native[name])
    for name in ("kernel", "initrd", "fdt"):
        if stock[name]["address"] != native[name]["address"]:
            add("placement", name, stock[name]["address"], native[name]["address"])
        if name != "fdt" and (stock_dir / (name + ".bin")).read_bytes() != (native_dir / (name + ".bin")).read_bytes():
            add("payload", name, stock[name], native[name])
    if sb != nb:
        add("encoding", "full-fdt", stock["fdt"], native["fdt"])
    return dict(changes=changes, bootargs_token_edits=bootargs_edits, raw_fdt_equal=sb == nb, property_counts=[len(sp), len(np)],
                node_counts=[len(sn), len(nn)], kernel_bytes_equal=(stock_dir / "kernel.bin").read_bytes() == (native_dir / "kernel.bin").read_bytes(),
                initrd_bytes_equal=(stock_dir / "initrd.bin").read_bytes() == (native_dir / "initrd.bin").read_bytes(),
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
            if all(sha((directory / (name + ".bin")).read_bytes()) == record[name]["sha256"] for name in ("fdt", "kernel", "initrd")):
                return record
    except (OSError, ValueError, KeyError):
        pass  # A partial/interrupted cache is reconstructed, never accepted.
    # Payload deduplication is applied after a run. Detach any shared inode
    # before a fresh capture so changed bytes cannot rewrite another case.
    for name in ("fdt", "kernel", "initrd"):
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
                             (environment, gpt, Card, card_logo, Stock, fdt_description, save_capture, stock_capture, Native, native_capture))
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
                           diagnostic_only=mode == "logo-only", **compare(reference, directory))
                rows.append(row)
                write_json(base / (mode + ".diff.json"), row)
    result = dict(target=target, provenance=provenance, cases=rows,
                  policy_counts=dict(Counter(c["policy"] for row in rows for c in row["changes"])),
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
    """Run the existing real-instruction kernel consumer; no verifier edits."""
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
                                         for legacy, warm in ((False, False), (True, False), (True, True))]))
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
    checks = []
    with tempfile.TemporaryDirectory(prefix="gate-", dir=args.output) as temporary:
        native = Path(temporary)
        (native / "kernel.bin").symlink_to((original / "kernel.bin").resolve())
        for test in ("dram-property", "bootarg", "empty-node", "reservation", "register", "initrd-byte", "kernel-placement"):
            blob, payload, meta = bytearray(tree), bytearray(initrd), json.loads(metadata)
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
            else:
                meta["kernel"]["address"] ^= 64
                expected_kind, expected_key = "placement", "kernel"
            (native / "fdt.bin").write_bytes(blob)
            (native / "initrd.bin").write_bytes(payload)
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


POLICY_EXPLANATIONS = """## Policy defects and proposed changes

These are defects under the owner's strict transparency requirement, including
intentional behavior. None is on the accepted-difference allowlist. The source
locations and commit IDs below come from `git log -S` on this branch, rather
than an inferred history.

| Difference | Why it exists / source history | Kernel consequence | Proposed change and risk |
|---|---|---|---|
| Missing `uboot_message=2018.05(...)` | `205f677` introduced the minimal `native/build.py:model_policy` renderer without the vendor version token; it was never added. There is no recorded independent rationale for dropping this particular token. The vendor builds it at U-Boot `+0x58b6`. | Visible command-line data differs. All eleven kernel images contain no `uboot_message` string; no dedicated kernel consumer was identified. Generic command-line handling and userspace can still observe it. | Generate the exact token from each target's pinned U-Boot version/date/time literals and insert it at the vendor's position, preserving duplicate `gpt`/`loglevel` tokens. Low implementation risk; the tool checks the exact token sequence. |
| Different `boot_fb0` source address, missing `disp_reserve=SIZE,ADDR`, extra FDT memory reservation | `96d108d` introduced `baseos_prepare_logo` and the owned low framebuffer at `0x4b000000`. `native/build.py:model_policy` describes its source as held reserved for the whole boot. `verify-kernel-logo.py` explicitly says there is no `disp_reserve` cleanup claim. Earlier no-logo design in `205f677` deliberately advertised no splash/reserve. | `Fb_copy_boot_fb.isra.6` parses `boot_fb0`, maps and copies the source. `early_disp_reserve` records the command-line reservation; `disp_reserve_mem` dispatches `memblock_free` after the copy. Native's FDT reservation keeps its source reserved. These are different memory lifetimes, not just cosmetic addresses. The existing actual-instruction consumer is rerun for every target, with/without the legacy reserve and on warm/cold branches; results are in `kernel-consumer.json`. | Repair and mapping risks are detailed in the following two rows. |
| Same framebuffer defect: concrete repair | The stock address is an allocation result from U-Boot's real reserve/dlmalloc/env/logo sequence, not merely RAM-top minus a constant. This harness does not substitute the lab addresses. | Giving stock metadata for an unrelated physical allocation would let Linux copy or free the wrong bytes. Adding `disp_reserve` while retaining the native permanent reservation is also not an identical ownership contract. | Reproduce the per-target stock allocation/layout from the same RAM size, card environment and BMP; generate pixels at that exact address; emit the exact vendor `boot_fb0` and `disp_reserve` token; remove only the native-owned framebuffer `/memreserve/` entry. Keep the existing monitor/initrd reservations. The ARGB contents, descriptor, cache clean range and DE layer base must all follow the selected address. |
| Framebuffer relocation risk | `spl/native-logo.h` fixes pixel/BMP staging addresses; `native/early-display.c` programs and cleans that buffer; `loader.c` builds A32 section descriptors with normal cacheable DRAM only for `0x40000000..0x4fffffff`. The normal stock source on 1 GiB units is near `0x7bf...`, and on the 2 GiB SP near `0xbbf...`. | Moving only the FDT string is incorrect. The native loader currently treats the higher framebuffer area as device/XN memory. Stock-reserved RAM also must not overlap BL31, image, SPL/loader records, stacks or tables. | Change the shared logo-address contract, loader pixel producer, early-display generated framebuffer writes, clean/publish paths and A32 MMU table construction together. The SPL's A64 mappings/cache-maintenance contract must be audited for the new range: its `native-cache.c` A64 tables map only `0x40000000..0x4fffffff` as the working arena. `native-boot.c` currently preloads only the BMP at `0x4b400000`, so relocation of *pixels alone* does not automatically require moving that BMP or changing SPL MMC destinations. If SPL or its preload path begins writing/caching the high pixel range, update its A64 map/clean ranges too. Emulator checks can prove address arithmetic, non-overlap, descriptor attributes, pixel bytes, register writes and requested cache operations. They cannot prove real bus/cache visibility or physical scanout. |
| `/chosen/baseos,native-boot`, `baseos,native-timing`, and (early-lit) `baseos,boot-display` | `205f677` added generation/timing FDT stamps for native identification, health and instrumentation; `96d108d` added the authenticated early-display record. `DESIGN.md` expressly requests these stamps. `src/native-health.c` reads generation and display state through sysfs. | The stock kernels have no strings for these named properties, but generic OF/FDT interfaces expose them; they are kernel-visible differences. Native userspace health also depends on them. | Stop adding these properties and move generation/timing/display-health reporting to an explicitly agreed out-of-FDT channel. Update `src/native-health.c`, boot diagnostics and qualification contracts before removing the data; otherwise native acknowledgement/recovery can regress. A new channel itself must be assessed against the broader transparency requirement. |
| Cold/logo-only `boot_disp=0`, missing `boot_disp1/2` where the stock counterpart advertises warm ownership | In `loader.c:baseos_prepare_logo`, warm display properties are written only when `baseos_native_display_state` authenticates a ready display; the metadata-only path from `96d108d` deliberately leaves them cold. | `display_fb_request` and display initialization choose warm synchronization/size behavior using these properties. The kernel consumer replay demonstrates distinct warm and cold branches. | Match stock display state and metadata together for the same logo-availability input. Stock still initializes a blank framebuffer and emits warm `boot_disp*` plus `disp_reserve` when BOOTLOGO.BMP is absent; native currently skips that initialization. Reproduce that blank-display path too. Do not advertise warm ownership until the matching DE/TCON/panel state exists. `logo-only` here is a linked-helper diagnostic applied to the cold final tree, not a reachable successful early-display run of the current release; its additional differences are labelled diagnostic. |
| Full FDT encoding/capacity | `205f677` opens the tree with current libfdt into a fixed 1 MiB destination (`BASEOS_NATIVE_FDT_CAPACITY`). Vendor startup enlarges its TOC FDT by 4096 bytes rounded to 32; original property insertion order and string-table layout also matter. Native metadata and its extra reservation enlarge/reorder blocks further. | Header `totalsize`, offsets, property order, strings and remaining capacity differ even after a semantic property census. `/sys/firmware/fdt` exposes the full FDT. | Retain a sufficiently large allocation but reproduce the vendor's final header capacity, reservation block, insertion order, strings and padding. Apply changes in vendor order; if libfdt versions encode differently, reproduce the relevant vendor layout behavior. Check full captured bytes, rather than packing/re-serializing both trees to conceal differences. Emulator can prove exact output bytes for the tested inputs. |

The native `libfdt/fdt_rw.c:fdt_open_into` copies the source tree and raises
`totalsize`; it does not clear the remaining destination capacity. Unicorn's
unwritten RAM starts as zero. Physical unused FDT bytes can therefore contain
prior RAM contents, another reason that merely matching properties is
insufficient. Reproduce the stock capacity and initialize its spare/padding
bytes explicitly. All eleven TOC spare extents, and both live-reference TOCs,
are checked to be present and zero before the stock startup model uses zeros.

## Inputs and classification

Each target exercises all three candidate-bank slots in its pinned boot0, even
when two slots have the same DRAM type, crossed with PE6 low/high. This is 66
hardware cases for eleven targets. The bank's complete 32-word block is used;
where a prepared alternate has no trained size, word 7 receives the size
derived from its row/column/rank/width geometry. The primary fixtures are
1 GiB; the alternate fixtures are 2 GiB, exercising RAM-dependent placement. These are
handoff fixtures, not executions or qualifications of DRAM training. They do
not exhaust every possible trained geometry or electrical calibration. The SP
live reference independently validates real 2 GiB stock output.

| Input | Stock path | Native path |
|---|---|---|
| 32 DRAM words | Written at U-Boot `0x4a000000+0x30`, exactly where boot0/SPL hands them over. Actual vendor `update_fdt_para_for_kernel`/legacy bindings execute. Read PCs are recorded. | Same words at SPL ABI2 record `0x4a300000+52`; real linked loader copies the first 24 into the legacy `/dram` names. |
| RAM size | Header `+0x4f8` in MiB; `gd+0x40` and board memory bank seeded consistently, then real reserve/malloc code executes. | Same byte size at record `+16`; word 7 and record type/size must agree with the linked loader's validation. |
| PE6 | GPIO PE_DAT `0x0300b0a0` bit 6 is low/high in the imported peripheral model. Stock panel code runs. | Same GPIO bit in the imported native display model. RG28 fixed old and Cube fixed boe are checked at both levels. |
| SID serial | Fixed `1c00103dc2840671ed3`; encoded into SID `0x03006204/208/20c`. Real `set_snum` and command-line builder run. | Identical SID words; real native serial rendering runs. Live validation uses each recorded unit's own SID-derived serial. |
| Boot reason | Fixed `unknow` enum 5 at `gd+0x124`, the output boundary of PMIC power-on classification, immediately before real `bootreason` initialization. | Same enum 5/source unknown in record `+232/+236`. This tool does not execute the SPL PMIC classifier; separate repo evidence documents its vendor/native comparison. |
| Environment / GPT | Current release prepared `boot-prefix.img` GPT and CRC-checked 128 KiB env partition, with `boot-gzip/boot.img` overlaid at GPT p4. Real env driver/hash import, partition update and bootargs code execute. | Builder's release inventory uses those same env/GPT extents. Build source pins and manifest bytes are checked. |
| Logo | Exact FAT16 BOOTLOGO.BMP from that card, through the vendor FAT/SD path. No-logo replay marks only its short directory entry deleted in the in-memory card. | The identical BMP/descriptor delivered at the SPL preload boundary; no-logo supplies an empty descriptor. The standalone `work/<target>/bootlogo.bmp` is deliberately not substituted: it differs from the card artwork. |

`early-lit` and `no-logo` compare equal inputs, including logo presence. The
`logo-only` helper diagnostic uses the same BMP but deliberately bypasses the
early-display orchestration and is not a claim about a normal shipping boot.
Its captured differences are reported separately in each JSON row.

Input artefacts found and corrected: artificial low bump-allocator addresses;
omitted environment initialization/hash-table allocations; omitted PMIC
allocation; startup FDT capacity; secure-monitor/secure-OS header flags; serial,
DRAM/size and boot-reason defaults; different standalone BMP fixtures; and
comparing an absent native logo with a present stock logo. No differing
hardware-derived values are accepted after equalizing these inputs.

The earlier RG40 partition-map mismatch was a comparison against an old lab
card. Lab validation reproduces `UDISK@mmcblk0p6:primary@mmcblk0p7`; current
release parity uses `appfs@mmcblk0p6:UDISK@mmcblk0p7` on both sides. The JSON
diffs would mark any unequal `partitions` token unexplained. HDMI `okay` was
restored by this branch's `fe3872e`; any remaining HDMI property mismatch is
also unexplained, not allowlisted.

## Scope beyond the captured contract

The measured final request is A32 `[0x8000ff04, 0x40080000, 0x44000000, 1]`
on both sides. The kernel starts at `0x40080000`, initrd at `0x42000000`, and
FDT at `0x44000000`. The tool captures these registers, physical addresses
and payloads; it does not execute BL31 or Linux entry for each matrix case.
The displayed A64 `x0=FDT,x1..3=0` is the known monitor normalization, explicitly
labelled inferred in capture metadata. Equality of the A32 monitor request is
measured. It does not prove every CPU register or architectural control bit.

PMIC rails, clocks/PLLs, DRAM electrical training, pinmux/pulls, GPIO timing,
DE/TCON/DSI/PWM state, HDMI link state, interrupt/timer state, cache/TLB contents,
DMA coherency and RAM outside the captured buffers (including framebuffer
pixel contents) can remain kernel-visible. Framebuffer pixels are not compared
by this tool; both sides receive the same BMP, and the existing kernel-consumer
replay checks copy/free behavior using controlled pixels.
The tracer deliberately models many peripheral reads and non-kernel SMCs,
and bypasses CPU cleanup; it is not a complete hardware simulator. Repo
evidence (`HANDOFF.md` display-cause/platform-prerequisite sections and
`DESIGN.md` early-Linux diagnosis) records these relevant facts without a new
hardware investigation here:

- Earlier native starts left SRAM C borrowed; stock released SYSCTRL ownership
  before Linux DE initialization. The current loader contains the promoted
  release fix, independently covered by `verify-sram-release.py` across all
  eleven vendor binaries. This tool does not re-qualify physical SRAM ownership.
- Native early-display setup replaces vendor boot-logo initialization; the
  no-logo path leaves display setup to Linux. Matching `boot_disp*` values
  cannot establish matching live DE state or a valid scanout.
- Native SPL uses mainline DRAM initialization/selection and PMIC/clock setup
  rather than vendor boot0's implementation. Repo evidence includes nine
  vendor media-PLL power-enable fixes, DRAM QoS/master-mask comparisons, trained
  geometry normalization, and the 1.5.2 LPDDR4 delay fix. This harness starts
  after training and assumes the provided results agree.
- Panel prerequisites, pinmux, reset/power/backlight timing and cache publication
  have their own repo verifiers. Their models cannot certify panel/HDMI output,
  suspend/resume, cold/charger starts, or per-unit electrical behavior.

Confidence: high for the explicit captured FDT/token/register/payload findings
on the tested matrix, strengthened by both byte-exact live validations and the
actual kernel consumer replays. This is evidence of data-contract defects and
a regression gate for their repair, not proof of complete hardware transparency.
"""


def report(args, results, validations, errors):
    total = len(args.target or TARGETS)
    verdict = ("Native does not meet strict handoff transparency: every listed policy difference remains a defect."
               if any(r["policy_counts"] for r in results) else "No completed policy-defect result yet.")
    lines = ["# H700 kernel handoff parity", "", "Host-only; no device access. Loader/SPL/existing verifiers were not modified.", "",
             "## Result", "", f"{len(results)}/{total} selected models completed. {verdict}", "",
             "Exact transparency requires identical data for identical inputs. Accepted differences: **none**.", "",
             "| Target | Hardware cases | Native variants | Policy differences | Unexplained | Payload/register parity |",
             "|---|---:|---:|---|---:|---|"]
    for row in sorted(results, key=lambda r: TARGETS.index(r["target"])):
        cases = row["cases"]
        passed = all(c["kernel_bytes_equal"] and c["initrd_bytes_equal"] and c["registers_equal"] for c in cases)
        lines.append(f"| {row['target']} | {len(cases) // len(MODES)} | {len(cases)} | {', '.join(row['policy_counts'])} | {row['unexplained']} | {'exact' if passed else 'FAIL'} |")
    lines.extend(["", "## Stock replay validation", "",
                  "These runs use the corresponding lab GPT, environment, TOC, boot0 DRAM words, serial, boot reason and strap. The release runs use release inputs independently.", "",
                  "| Unit | Every FDT byte | Bytes | Live SHA256 (equals replay) | Properties / reservations |", "|---|---|---:|---|---|"])
    for v in validations:
        lines.append(f"| {v['target']} | {v['byte_exact']} | {v['header_live'][1]} | {v['live_sha256']} | {v['properties_exact']} / {v['reservations_exact']} |")
    if errors:
        lines.extend(["", "## Incomplete runs", ""] + [f"- {error}" for error in errors])
    lines.extend(["", "## Representative release values by target", "",
                  "The first low-strap bank is shown; every bank/strap/variant has a complete JSON diff.", "",
                  "| Target | RAM MiB | Stock framebuffer bases | Native base | Stock / native FDT bytes | Missing vendor token | Differing fields by variant |",
                  "|---|---|---|---|---|---|---|"])
    for result in sorted(results, key=lambda r: TARGETS.index(r["target"])):
        rows = result["cases"]
        row = next(r for r in rows if r["mode"] == "early-lit")
        base = args.output / result["target"] / row["case"]
        stock, native = [(base / name / "fdt.bin").read_bytes() for name in ("stock", "early-lit")]
        before = fdt.get(stock, DISPLAY, "boot_fb0")
        after = fdt.get(native, DISPLAY, "boot_fb0")
        addresses = set()
        for case in rows:
            if case["mode"] == "early-lit":
                path = args.output / result["target"] / case["case"] / "stock/fdt.bin"
                description = fdt.get(path.read_bytes(), DISPLAY, "boot_fb0")
                if description:
                    addresses.add("0x" + description.split(b",", 1)[0].decode())
        ram = "/".join(str(size) for size in sorted({r["hardware"]["ram_bytes"] >> 20 for r in rows}))
        vendor = json.loads((base / "stock/capture.json").read_text())
        version = next(t for t in vendor["bootargs"].split() if t.startswith("uboot_message="))
        native_base = "0x" + after.split(b",", 1)[0].decode() if after else "absent"
        counts = "; ".join(f"{mode}: {len(next(r for r in rows if r['mode'] == mode)['changes'])}" for mode in MODES)
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
