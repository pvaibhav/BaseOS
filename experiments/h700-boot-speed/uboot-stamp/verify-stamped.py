#!/usr/bin/env python3
"""Verify boot-package-stamped.bin statically and under Unicorn (no device access).

    /tmp/uboot-venv/bin/python work/rg34xxsp-stamp/verify-stamped.py
"""
from __future__ import annotations

import importlib.util
import json
import struct
import sys
import time
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_SP

sys.dont_write_bytecode = True
HERE = Path(__file__).absolute().parent
spec = importlib.util.spec_from_file_location("build_stamped", HERE / "build-stamped.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
kg, h, REPO = build.kg, build.harness, build.REPO
require = h.require
info = json.loads((HERE / "stamps.json").read_text())
STUBS = [{**s, **{k: int(s[k], 16) for k in ("stub", "call_site", "original_target", "slot_address")}}
         for s in info["stubs"]]
LOW = 0x40000000
out: list[str] = []


def say(text: str = "") -> None:
    print(text, flush=True)
    out.append(text)


original_package = build.SOURCE.read_bytes()
patched_package = build.OUTPUT.read_bytes()
require(kg.sha(patched_package) == info["patched_package_sha256"], "stamps.json does not describe the output")
original = original_package[build.ITEM:build.ITEM + build.ITEM_SIZE]
patched = patched_package[build.ITEM:build.ITEM + build.ITEM_SIZE]

# ---------------------------------------------------------------- (3) listings
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)


def listing(code: bytes, start: int, size: int) -> list[tuple[int, str, str]]:
    rows, at = [], start
    for insn in md.disasm(code[start:start + size], build.LINK + start):
        rows.append((insn.address - build.LINK, insn.bytes.hex(), f"{insn.mnemonic} {insn.op_str}"))
        at = insn.address - build.LINK + insn.size
    return rows


say("## call sites (before -> after)")
for s in STUBS:
    (_, b0, t0), = listing(original, s["call_site"], 4)
    (_, b1, t1), = listing(patched, s["call_site"], 4)
    require(t0 == f"bl #{build.LINK + s['original_target']:#x}" and t1 == f"bl #{build.LINK + s['stub']:#x}",
            f"call site {s['call_site']:#x}: {t0} / {t1}")
    say(f"slot {s['slot']}  {s['call_site']:6x}: {b0}  {t0:20s} ->  {b1}  {t1}")
say()
say("## stubs")
for s in STUBS:
    lits = 2 if s["slot"] == 0 else 1
    code_size = s["stub_size"] - 4 * lits
    rows = listing(patched, s["stub"], code_size)
    require(sum(len(b) // 2 for _, b, _ in rows) == code_size, f"stub {s['slot']} does not disassemble cleanly")
    texts = [t for _, _, t in rows]
    require(texts[0] == "push {r0, r1, r2, r3}" and texts[1] == "mrrc p15, #0, r0, r1, c14", "stub prologue")
    tail = [t for t in texts if t != "nop "]
    require(tail[-2] == "pop {r0, r1, r2, r3}" and tail[-1] == f"b.w #{build.LINK + s['original_target']:#x}",
            f"stub {s['slot']} epilogue: {tail[-2:]}")
    say(f"stub {s['slot']} at {s['stub']:#x} ({s['meaning']})")
    for at, b, t in rows:
        say(f"  {at:6x}: {b:10s} {t}")
    for n in range(lits):
        at = s["stub"] + code_size + 4 * n
        say(f"  {at:6x}: {patched[at:at + 4].hex():10s} .word {struct.unpack_from('<I', patched, at)[0]:#010x}")
    # the literal each ldr loads
    for at, b, t in rows:
        if t.startswith("ldr r") and "[pc" in t:
            say(f"           ({t.split(',')[0][4:]} <- {h.literal(patched, at):#010x})")
say()

# ---------------------------------------------------------------- (4) byte diff
diff = kg.changed(original_package, patched_package)
regions = {"package checksum (word 20)": set(range(20, 24)),
           "U-Boot checksum (item word 3)": {build.ITEM + i for i in range(12, 16)},
           "help text NUL + stubs": {build.ITEM + i for i in range(build.HELP_TEXT, int(info["stub_area"][1], 16))}}
for s in STUBS:
    regions[f"call site {s['call_site']:#x}"] = {build.ITEM + s["call_site"] + i for i in range(4)}
covered = set().union(*regions.values())
require(diff <= covered, f"changes outside the patch: {sorted(diff - covered)[:8]}")
require(patched_package[:build.ITEM] == original_package[:build.ITEM] or diff & set(range(build.ITEM)) <= set(range(20, 24)),
        "TOC1 header changed")
require(patched_package[build.ITEM + build.ITEM_SIZE:] == original_package[build.ITEM + build.ITEM_SIZE:],
        "monitor/dtbo/dtb items changed")
require(patched[0xFDAA0:] == original[0xFDAA0:] == b"\xFF" * (0x100000 - 0xFDAA0), "padding changed")
require(kg.checksum(patched_package, 20) == kg.word(patched_package, 20) and
        kg.checksum(patched, 12) == kg.word(patched, 12), "checksums")
say("## byte diff")
say(f"{len(diff)} bytes differ; every one lies in: " + ", ".join(f"{k} ({len(diff & v)})" for k, v in regions.items()))
say("the 0xFF padding 0xFDAA0..0x100000 and the monitor, dtbo and dtb items are unchanged; both checksums valid")
say()

# ---------------------------------------------------------------- emulation
counter = {"value": 0x1000000, "native": None}


class StampMachine(h.Machine):
    """Harness machine that also records low-DRAM writes and stub/target order.

    full=True moves the boot_jump_linux stop from its entry (0x1470) to 0x1486,
    just after the call at 0x1482, so stub 5 runs; the jump record is taken from
    r5 (images) and r4 (flag), which hold the entry r0/r1 there.
    """
    full = False

    def replace(self, at, model):
        if at == h.BOOT_JUMP_LINUX and self.full:
            def stop(uc, *_):
                images, flag = uc.reg_read(UC_ARM_REG_R5), uc.reg_read(UC_ARM_REG_R4)
                self.jump = {"flag": flag, "entry": self.get32(images + 0x60), "fdt": self.get32(images + 0x6C)}
                uc.emu_stop()
            self.uc.hook_add(UC_HOOK_CODE, stop, begin=build.LINK + 0x1486, end=build.LINK + 0x1486)
            return
        super().replace(at, model)

    def __init__(self, *args):
        super().__init__(*args)
        self.low_writes, self.trace = [], []
        self.uc.hook_add(UC_HOOK_MEM_WRITE, lambda uc, _a, address, size, value, _d: self.low_writes.append(
            (uc.reg_read(UC_ARM_REG_PC) - build.LINK, address, size, value & 0xFFFFFFFF)), begin=LOW, end=LOW + 0xFFF)
        names = {s["stub"]: f"stub{s['slot']}" for s in STUBS}
        names.update({s["original_target"]: f"target@{s['original_target']:#x}" for s in STUBS})
        for at, name in names.items():
                self.uc.hook_add(UC_HOOK_CODE, lambda *_, n=name: self.trace.append(n),
                                 begin=build.LINK + at, end=build.LINK + at)
        if counter["native"] is False:   # emulate the mrrc: a counter that advances per stub
            for s in STUBS:
                self.uc.hook_add(UC_HOOK_CODE, self.fake_counter, begin=build.LINK + s["stub"] + 2,
                                 end=build.LINK + s["stub"] + 2)

    def fake_counter(self, uc, address, _size, _data):
        counter["value"] += 24000
        uc.reg_write(h.UC_ARM_REG_R0, counter["value"] & 0xFFFFFFFF)
        uc.reg_write(h.UC_ARM_REG_R0 + 1, counter["value"] >> 32)
        uc.reg_write(UC_ARM_REG_PC, (address + 4) | 1)

    def stamps(self) -> list[int]:
        return [struct.unpack("<Q", self.read(build.STAMP_BASE + 8 * n, 8))[0] for n in range(6)]


def probe_counter() -> None:
    """Does Unicorn execute mrrc p15,0,r0,r1,c14, and does the value advance?"""
    m = StampMachine(patched, tree, environment, h.STOCK_CONSOLE)
    m.replace(STUBS[1]["original_target"], lambda: 0)
    values = []
    try:
        for _ in range(2):
            m.call(STUBS[1]["stub"], [])
            values.append(m.stamps()[1])
            time.sleep(0.01)
        counter["native"] = values[1] > values[0] > 0
    except h.Failed as error:
        say(f"mrrc under Unicorn failed: {error}")
        counter["native"] = False
    say(f"CNTPCT under Unicorn: two reads gave {[hex(v) for v in values]}; "
        + ("native counter used" if counter["native"] else "stubs' mrrc is replaced by a model counter (+24000 per read)"))


prefix = REPO / "work/rg34xxsp/boot-prefix.img"
environment = h.stored_environment(prefix)
derived = kg.derive("rg34xxsp", prefix)
require(derived["package"] == original_package and derived["uboot"] == original,
        "derive() does not reproduce the shipped package")
base_target = h.Target("rg34xxsp", derived, environment)
tree = base_target.tree
stamped_target = h.Target("rg34xxsp", {**derived, "uboot": patched}, environment)
fast = h.variant(base_target.shipped, h.EMPTY_CPIO, h.HEADER_CMDLINE)
require(fast == derived["boot"], "emulated fast image is not the shipped one")
say("## emulation (tests/test-fast-boot-emulation.py machinery)")
say(f"fast image {len(fast)} bytes sha256 {kg.sha(fast)[:16]} (byte-identical to the build's boot.img)")
probe_counter()


def run(target, full: bool, console: int = h.STOCK_CONSOLE) -> dict:
    StampMachine.full = full
    h.Machine = StampMachine
    try:
        started = time.time()
        result = h.boot(target, fast, console)
        result["seconds"] = time.time() - started
        return result
    finally:
        h.Machine = HarnessMachine


HarnessMachine = h.Machine


def checks(result: dict) -> list[str]:
    return [h.expect_card(result), h.expect_kernel(result, derived["kernel"]),
            h.expect_ramdisk(result, tree),
            h.expect_bootargs(result, f"{h.KNOWN_BOOTARGS} {h.HEADER_CMDLINE}")]


baseline = run(base_target, full=False)
say(f"unpatched, stock harness stop: {baseline['seconds']:.0f} s; " + "; ".join(checks(baseline)))
require(not baseline["machine"].low_writes, f"unpatched run wrote low DRAM: {baseline['machine'].low_writes[:4]}")
for full in (False, True):
    for console in (h.STOCK_CONSOLE, h.QUIET_CONSOLE):
        r = run(stamped_target, full, console)
        m = r["machine"]
        detail = checks(r)
        require(r["jump"] == baseline["jump"], f"jump record differs: {r['jump']} / {baseline['jump']}")
        require(m.env["bootargs"] == baseline["machine"].env["bootargs"] and r["tree"] == baseline["tree"],
                "bootargs or device tree differ from the unpatched run")
        require(r["reads"] == baseline["reads"], "card reads differ")
        expected = [2, 3, 4, 5] if full else [2, 3, 4]
        stubs_run = [int(n[4:]) for n in m.trace if n.startswith("stub")]
        require(stubs_run == expected, f"stubs run: {m.trace}")
        # each stub is followed directly by its original target (other targets may repeat in between)
        for n in expected:
            i = m.trace.index(f"stub{n}")
            require(m.trace[i + 1] == f"target@{STUBS[n]['original_target']:#x}", f"stub {n} not followed by its target: {m.trace[i:i + 3]}")
        writers = {pc for pc, *_ in m.low_writes}
        stub_ranges = [(s["stub"], s["stub"] + s["stub_size"]) for s in STUBS]
        require(all(any(a <= pc < b for a, b in stub_ranges) for pc in writers), f"other writers to low DRAM: {writers}")
        addresses = sorted({a for _, a, _, _ in m.low_writes})
        require(addresses == sorted(build.STAMP_BASE + 8 * n + k for n in expected for k in (0, 4)),
                f"low DRAM writes at {[hex(a) for a in addresses]}")
        values = m.stamps()
        require(values[0] == values[1] == 0, "slots 0/1 written in a run that does not include them")
        require(all(values[a] < values[b] for a, b in zip(expected, expected[1:])) and values[expected[0]] > 0,
                f"stamps not increasing: {[hex(v) for v in values]}")
        say(f"stamped, {'stop at 0x1486 (stub 5 included)' if full else 'stock harness stop at 0x1470'}, console "
            f"{console}: {r['seconds']:.0f} s; same jump {r['jump']}, same card reads, bootargs and device tree as "
            f"unpatched; {'; '.join(d.split(';')[0] for d in detail)}")
        say(f"    stub/target order: {' '.join(m.trace)}")
        say(f"    low DRAM writes (pc, address, value): " + ", ".join(f"{pc:#x}:{a:#x}={v:#x}" for pc, a, _, v in m.low_writes))
        say(f"    slots: {[hex(v) for v in values]}")
say()

# ---------------------------------------------------------------- (2) stubs 0 and 1 called directly
say("## stubs 0 and 1 called directly")
for s in STUBS[:2]:
    m = StampMachine(patched, tree, environment, h.STOCK_CONSOLE)
    m.uc.mem_write(build.STAMP_BASE - 8, b"\xAA" * (8 + 48))
    reached = []
    m.replace(s["original_target"], lambda m=m: reached.append(
        ([m.arg(n) for n in range(4)], m.uc.reg_read(UC_ARM_REG_SP), m.caller())) or 0x1234)
    args = [0x11111111, 0x22222222, 0x33333333, 0x44444444]
    result = m.call(s["stub"], args)
    raw = m.read(build.STAMP_BASE - 8, 56)
    words = struct.unpack("<14I", raw)
    values = m.stamps()
    require(len(reached) == 1 and reached[0][0] == args and reached[0][1] == h.STACK, f"pass-through: {reached}")
    require(result == 0x1234, "target's return value not passed back")
    if s["slot"] == 0:
        require(words[0] == build.MAGIC and words[1] == 0xAAAAAAAA and values[0] > 0 and values[1:] == [0] * 5,
                f"stub 0 memory: {[hex(w) for w in words]}")
    else:
        require(words[:2] == (0xAAAAAAAA,) * 2 and values[1] > 0 and
                all(values[n] == 0xAAAAAAAAAAAAAAAA for n in (0, 2, 3, 4, 5)), f"stub 1 memory: {[hex(w) for w in words]}")
    say(f"stub {s['slot']}: target {s['original_target']:#x} reached once with r0-r3 {[hex(a) for a in reached[0][0]]}"
        f" and sp {reached[0][1]:#x} unchanged, returned {result:#x} to the caller; memory from {build.STAMP_BASE - 8:#x}:"
        f" {' '.join(f'{w:08x}' for w in words)}")
say()
say("ALL CHECKS PASSED")
(HERE / "verify-output.txt").write_text("\n".join(out) + "\n")
