#!/usr/bin/env python3
"""Host checks for the built firmware. No device access.

    python3 v2/firmware/tests/run.py [VARIANT ...]   after build.py has built them (default: all)

Unicorn checks run in the pinned Linux container (never on macOS), with the
existing wheels in work/mainline/payload/linux-deps. Results:
work/v2/firmware/tests.json, logs in work/v2/firmware/build/tests/.

  capsule        experiments/.../capsule/verify.py and verify-entry.py: eGON
                 checksum and layout, dispatcher choice for empty and nonzero RTC
                 pairs, resume bit, integrity refusals, watchdog arm/readback and
                 commit order, fault injection, copied SPL entry up to RMR3
  dispatcher     tests/dispatcher_diff.py: E1 and variant dispatcher instructions
                 are identical, only literal-pool constants differ
  bl31-dtb       tests/bl31_dtb.py: BL31's compiled devicetree check on the payload
                 tree, a PMIC node, and an unterminated compatible string
  helper-fdt     tests/fdt_policy.c (host clang): the helper's FDT rules, same trees
  chain          tests/chain_replay.py: compiled SPL card read + helper to BL31,
                 GPIO/SPI trace checked against the vendor panel sequence; the
                 current v2 OS payload and the larger E1 payload; tamper cases
"""
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
FW = ROOT / 'work/v2/firmware'
LOGS = FW / 'build/tests'
CARD = ROOT / 'work/rg28xx'  # stock card prefix (boot-resource holds BOOTLOGO.BMP)
PAYLOADS = {'os': ROOT / 'work/v2/os/image.bin', 'u3': ROOT / 'work/mainline/payload/u3-final/image.bin'}
sys.path.insert(0, str(HERE.parent))
sys.dont_write_bytecode = True
import build  # noqa: E402  (v2/firmware/build.py: IMAGE, restamp)


def container(name, *command):
    """Run python3 COMMAND in the toolchain image; repo at /repo, card prefix at /card."""
    args = ['docker', 'run', '--rm', '--network=none', '-v', f'{ROOT}:/repo', '-v', f'{CARD}:/card:ro',
            '-v', f'{ROOT}/work/mainline/payload/linux-deps:/deps:ro', '-e', 'PYTHONPATH=/deps',
            '-e', 'PYTHONDONTWRITEBYTECODE=1', build.IMAGE, 'python3', *command]
    return logged(name, args)


def logged(name, args):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    (LOGS / f'{name}.log').write_text(result.stdout + result.stderr)
    status = 'pass' if result.returncode == 0 else 'FAIL'
    print(f'  {status:4} {name}', flush=True)
    return status, result.stdout


def last_json(text):
    start = text.rfind('\n{') + 1 if text.rstrip().endswith('}') else -1
    return json.loads(text[start:]) if start >= 0 else None


def main(variants):
    if 'v2-rgsp' in variants:
        result = subprocess.run([sys.executable, HERE / 'rgsp.py'])
        if result.returncode:
            return result.returncode
        variants = [v for v in variants if v != 'v2-rgsp']
        if not variants:
            return 0
    fleet = [v for v in variants if v.startswith('v2-')]
    for v in fleet:
        result = subprocess.run([sys.executable, HERE / 'fleet.py', v[3:]])
        if result.returncode:
            return result.returncode
    variants = [v for v in variants if v not in fleet]
    if not variants:
        return 0
    LOGS.mkdir(parents=True, exist_ok=True)
    results = {}
    repo = lambda p: '/repo/' + str(Path(p).relative_to(ROOT))
    # Payloads stamped with each variant's generation (kernel/initramfs/DTB unchanged).
    stamped = {}
    for v in variants:
        for key, path in PAYLOADS.items():
            out = FW / f'build/payloads/{key}-{v}.bin'
            out.parent.mkdir(parents=True, exist_ok=True)
            build.restamp(path, out, v)
            stamped[v, key] = out
    dtb_dir = FW / 'build/dtb'
    dtb_dir.mkdir(parents=True, exist_ok=True)
    image = PAYLOADS['os'].read_bytes()
    ao, ab, do, db = struct.unpack_from('<4I', image, 16)
    (FW / 'build/payload.dtb').write_bytes(image[do:do + db])

    for v in variants:
        print(f'== {v}')
        cap = repo(FW / f'build/{v}/capsule')
        results[f'{v}/capsule'] = container(f'{v}-capsule', '/repo/experiments/h700-boot-speed/native/capsule/verify.py', cap)[0]
        results[f'{v}/capsule-entry'] = container(f'{v}-capsule-entry', '/repo/experiments/h700-boot-speed/native/capsule/verify-entry.py', cap)[0]
        if v != 'e1':
            results[f'{v}/dispatcher'] = container(f'{v}-dispatcher', repo(HERE / 'dispatcher_diff.py'),
                                                   repo(FW / 'build/e1/capsule'), cap)[0]
        tree, lib = FW / f'build/{v}/tree', FW / f'build/{v}/helper/libfdt'
        exe = FW / f'build/tests/fdt_policy_{v}'
        subprocess.run(['clang', '-O1', '-w', f'-I{tree}/experiments/h700-mainline/helper',
                        f'-I{tree}/experiments/h700-boot-speed/spl', f'-I{tree}/experiments/h700-mainline/include',
                        f'-I{lib}', HERE / 'fdt_policy.c', tree / 'experiments/h700-mainline/helper/fdt-final.c',
                        *sorted(lib.glob('fdt*.c')), '-o', exe], check=True)
        status, out = logged(f'{v}-helper-fdt', [exe, FW / 'build/payload.dtb', dtb_dir])
        results[f'{v}/helper-fdt'] = dict(l.split() for l in out.splitlines())
        status, out = container(f'{v}-bl31-dtb', repo(HERE / 'bl31_dtb.py'), repo(FW / f'build/{v}/tfa/bl31.elf'), repo(dtb_dir))
        results[f'{v}/bl31-dtb'] = last_json(out) if status == 'pass' else status
        early = 'early-panel.patch' in build.VARIANTS[v]['patches']
        cases = [(key, None) for key in PAYLOADS] + ([('os', 'stream'), ('os', 'fnv')] if early else [])
        for key, tamper in cases:
            name = f'{v}-chain-{key}' + (f'-tamper-{tamper}' if tamper else '')
            status, out = container(name, repo(HERE / 'chain_replay.py'), v, repo(stamped[v, key]),
                                    '--card', '/card/boot-prefix.img', *(['--tamper', tamper] if tamper else []))
            report = last_json(out) if status == 'pass' else None
            results[f'{v}/{name[len(v) + 1:]}'] = status if not report else dict(
                frames_by_spl=report['frames_sent_by_spl'], frames_by_helper=report['frames_sent_by_helper'],
                spl_frames_before_helper_reset=report['spl_frames_before_helper_reset'],
                timeline_ms=report['timeline_ms_from_payload_loaded'])
    # E1 refuses PMIC nodes in the helper and in BL31; the other variants accept them.
    # The helper always refuses an unterminated compatible string.
    failed = [k for k, v in results.items() if v == 'FAIL']
    for v in variants:
        e1 = v == 'e1'
        if results.get(f'{v}/helper-fdt') != {'payload': '0', 'pmic': '-1' if e1 else '0', 'unterminated': '-1'}:
            failed.append(f'{v}/helper-fdt')
        got = results.get(f'{v}/bl31-dtb')
        want = {'payload': 'returned', 'pmic': 'panic' if e1 else 'returned', 'unterminated': 'panic' if e1 else 'returned'}
        if not isinstance(got, dict) or any(got[n] != w for n, w in want.items()):
            failed.append(f'{v}/bl31-dtb')
    (FW / 'tests.json').write_text(json.dumps(dict(results=results, failed=failed), indent=2) + '\n')
    print('FAILED: ' + ', '.join(failed) if failed else 'all checks passed', '-> work/v2/firmware/tests.json')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:] or list(build.VARIANTS)))
