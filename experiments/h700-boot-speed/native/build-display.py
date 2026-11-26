#!/usr/bin/env python3
"""Generate a model's pinned display tables for the A32 loader's pre-Linux logo."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
KINDS = {"update": "DISPLAY_UPDATE", "write": "DISPLAY_WRITE",
         "framebuffer": "DISPLAY_FRAMEBUFFER", "delay_us": "DISPLAY_DELAY_US",
         "poll": "DISPLAY_POLL", "barrier": "DISPLAY_BARRIER"}
PHASES = ("prepare", "publish", "enable", "backlight")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def handoff(target, policy):
    contract = json.loads((HERE / "display-handoff.json").read_text())
    assert contract["version"] == 1
    model = contract["models"][target]
    assert set(model) == {"package_sha256", "vendor_uboot_sha256", "boot_disp", "boot_disp1", "boot_disp2"}
    assert model["package_sha256"] == policy["package_sha256"]
    assert model["vendor_uboot_sha256"] == policy["panel_contract"]["vendor_uboot_sha256"]
    result = {name: model[name] for name in ("boot_disp", "boot_disp1", "boot_disp2")}
    assert all(type(value) is int and 0 <= value <= 0xffffffff for value in result.values())
    assert result["boot_disp"] == 0x100
    return result


def contracts(target, policy):
    engine = json.loads((HERE / "display-engine.json").read_text())
    panel = json.loads((HERE / "panel-init.json").read_text())
    assert engine["version"] == panel["version"] == 1
    assert panel["initial_reset_delays_ms"] == [15, 50, 100]
    model, artwork = engine["models"][target], panel["models"][target]
    assert model["vendor_dtb_sha256"] == policy["early_display"]["vendor_dtb_sha256"]
    for item in (model, artwork):
        assert item["package_sha256"] == policy["package_sha256"]
        assert item["vendor_uboot_sha256"] == policy["panel_contract"]["vendor_uboot_sha256"]
    for phase in PHASES:
        operations = model[phase]
        assert operations and len(operations) <= 4096
        for op in operations:
            assert op["kind"] in KINDS
            assert set(op) == {"kind", "address", "clear_mask", "set_bits", "timeout_us"}
            assert all(type(op[key]) is int and 0 <= op[key] <= 0xffffffff
                       for key in ("address", "clear_mask", "set_bits", "timeout_us"))
            if op["kind"] == "barrier":
                assert all(op[key] == 0 for key in ("address", "clear_mask", "set_bits", "timeout_us"))
            elif op["kind"] == "delay_us":
                assert op["address"] == op["clear_mask"] == op["timeout_us"] == 0
                assert 0 < op["set_bits"] <= 1000000
            else:
                address = op["address"]
                assert address % 4 == 0 and any(start <= address < end for start, end in
                    ((0x01000000, 0x02400000), (0x03001000, 0x03002000),
                     (0x0300a000, 0x0300c000), (0x030f0000, 0x030f1000),
                     (0x06510000, 0x06512000))), hex(address)
                if op["kind"] == "poll":
                    assert 0 < op["timeout_us"] <= 100000
                    assert op["set_bits"] & ~op["clear_mask"] == 0
                else:
                    assert op["timeout_us"] == 0
                if op["kind"] == "framebuffer":
                    assert op["set_bits"] < 0x400000
    rule = policy["panel_rule"]
    assert rule in (0, 1, 2)
    for name in (("old", "boe") if rule == 2 else ("boe" if rule else "old",)):
        assert artwork[name] in panel["streams"]
    for digest in (artwork["old"], artwork["boe"]):
        if digest is None:
            continue
        stream = panel["streams"][digest]
        assert stream and all(type(op) is int and (0 <= op <= 0x1ff or 0x8000 < op <= 0x83e8) for op in stream)
        assert sha(b"".join(op.to_bytes(2, "little") for op in stream)) == digest
    return model, artwork, panel["streams"]


def build(output, target, policy, generation):
    model, panel, streams = contracts(target, policy)
    assert len(generation) == 32
    header = (f"#define BASEOS_PANEL_RULE {policy['panel_rule']}\n"
              f"#define BASEOS_PANEL_PULL_UP {int(policy['panel_pull_up'])}\n"
              f"#define BASEOS_LOGO_WIDTH {policy['boot_logo']['width']}u\n"
              f"#define BASEOS_LOGO_HEIGHT {policy['boot_logo']['height']}u\n"
              f"#define BASEOS_LOGO_RAM_END_OFFSET {policy['boot_logo']['framebuffer_from_ram_end']}u\n"
              "static const unsigned char baseos_generation[32] = {" +
              ",".join(map(str, generation)) + "};\n")
    fallback = panel["old"] or panel["boe"]
    for name in ("old", "boe"):
        values = streams[panel[name] or fallback]
        header += f"static const uint16_t baseos_panel_{name}[] = {{" + ",".join(hex(op) for op in values) + "};\n"
    (output / "early-display-generated.h").write_text(header)
    operations = ""
    for phase in PHASES:
        operations += f"static const struct display_operation baseos_display_{phase}[] = {{\n"
        for op in model[phase]:
            fields = [KINDS[op["kind"]]] + [f"0x{op[key]:x}u" for key in
                      ("address", "clear_mask", "set_bits", "timeout_us")]
            operations += "    {" + ",".join(fields) + "},\n"
        operations += "};\n"
    (output / "early-display-operations.h").write_text(operations)
    manifest = dict(target=target, generation=generation.hex(), runs_in="a32-loader",
                    tables_sha256=sha((output / "early-display-operations.h").read_bytes()),
                    streams_sha256=sha((output / "early-display-generated.h").read_bytes()),
                    vendor_dtb_sha256=model["vendor_dtb_sha256"],
                    engine_contract_sha256=sha((HERE / "display-engine.json").read_bytes()),
                    panel_contract_sha256=sha((HERE / "panel-init.json").read_bytes()),
                    hardware_qualified=False)
    return manifest
