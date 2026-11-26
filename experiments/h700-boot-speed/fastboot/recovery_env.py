"""Fixed one-sector difference between an armed trial and normal boot.

The full environment CRC covers all 128 KiB. Only its first sector changes;
the trial scripts and every other byte have identical positions and values.
"""
import struct
import zlib

ORIGINAL = "sunxi_flash read 45000000 boot;bootm 45000000"
ARMED = "run baseos_trial_read baseos_trial_boot;run baseos_trial_fallback"
RESET = "mw.l 30090b4 1;mw.l 30090b8 b1;mw.l 30090b0 14af;sleep 60;run baseos_trial_fallback"


def encode(values):
    data = b"\0".join((key + "=" + value).encode("latin1") for key, value in values.items()) + b"\0\0"
    if len(data) > 0x20000 - 4:
        raise ValueError("trial environment is too large")
    data = data.ljust(0x20000 - 4, b"\0")
    return struct.pack("<I", zlib.crc32(data)) + data


def images(baseline, reset=False):
    rest = dict(baseline)
    rest.pop("boot_normal")
    rest.pop("baseos_skip_logo", None)
    rest.update(baseos_trial_read="sunxi_nand_test", baseos_trial_fallback=ORIGINAL,
                baseos_trial_boot=RESET if reset else "setenv boot_from_partion boot;bootm 45000000")
    def variant(command, flag):
        return encode(dict(boot_normal=command.ljust(128), baseos_skip_logo=flag.ljust(3), **rest))
    blobs = {"normal": variant(ORIGINAL, "no"), "armed_no": variant(ARMED, "no"),
             "armed_yes": variant(ARMED, "yes")}
    if not all(blob[512:] == blobs["normal"][512:] for blob in blobs.values()):
        raise ValueError("trial changed more than one sector")
    return blobs


def header(blobs):
    normal = blobs["normal"]
    at = normal.index(b"boot_normal=") + len(b"boot_normal=")
    flag = normal.index(b"baseos_skip_logo=") + len(b"baseos_skip_logo=")
    rows = ["/* Generated from the pinned lab environment. */",
            "static const unsigned char restored_sector[512] = {"]
    rows += ["    " + ",".join(str(byte) for byte in normal[i:i + 16]) + "," for i in range(0, 512, 16)]
    rows += ["};", f"#define BOOT_OFFSET {at}", f"#define FLAG_OFFSET {flag}",
             f'#define ARMED_BOOT "{ARMED}"']
    for name, blob in blobs.items():
        rows.append(f"#define CRC_{name.upper()} 0x{struct.unpack_from('<I', blob)[0]:08x}u")
    return "\n".join(rows) + "\n"
