#!/usr/bin/env python3
"""USB audio module ABI/cache fault injection; no Docker or vendor downloads needed."""
import contextlib
import gzip
import io
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import usb_audio_modules as usb

VERMAGIC = '4.9.170 SMP preempt mod_unload modversions aarch64'
KERNEL_EXPORTS = {'module_layout': 1, 'printk': 2, 'snd_card_new': 3}
VENDOR_CONFIG = (b'CONFIG_MODULES=y\nCONFIG_SND_USB=y\nCONFIG_SUNXI_VENDOR=y\n'
                 b'# CONFIG_SND_USB_AUDIO is not set\nCONFIG_SND_PCM=y\n')
# olddefconfig on the vanilla tree drops vendor-only options.
BASELINE_CONFIG = b'CONFIG_MODULES=y\nCONFIG_SND_USB=y\nCONFIG_SND_PCM=y\n'
EFFECTIVE_CONFIG = (b'CONFIG_MODULES=y\nCONFIG_SND_USB=y\nCONFIG_SND_HWDEP=m\n'
                    b'CONFIG_SND_USB_AUDIO=m\nCONFIG_SND_PCM=y\n')


def module_blob(*, imports, exports=(), depends='', vermagic=VERMAGIC,
                module_size=768, unversioned=()):
    """Minimal ELF64 module with a symbol table, for the production parser."""
    names = list(imports) + list(unversioned) + ['__crc_' + name for name in exports]
    strtab = b'\0' + b''.join(name.encode() + b'\0' for name in names)
    symtab = bytes(24)
    for name in names:
        offset = strtab.index(b'\0' + name.encode() + b'\0') + 1
        if name.startswith('__crc_'):
            crc = exports[name[len('__crc_'):]]
            symtab += struct.pack('<IBBHQQ', offset, 16, 0, usb.SHN_ABS, crc, 0)
        else:
            symtab += struct.pack('<IBBHQQ', offset, 16, 0, usb.SHN_UNDEF, 0, 0)
    payloads = {
        '': b'',
        '.modinfo': f'vermagic={vermagic}\0depends={depends}\0'.encode(),
        '__versions': b''.join(struct.pack('<Q', crc) + name.encode().ljust(56, b'\0')
                               for name, crc in imports.items()),
        '.gnu.linkonce.this_module': bytes(module_size),
        '.strtab': strtab,
        '.symtab': symtab,
    }
    sections = list(payloads) + ['.shstrtab']
    shstrtab = b'\0' + b''.join(name.encode() + b'\0' for name in sections[1:])
    payloads['.shstrtab'] = shstrtab
    result = bytearray(64)
    headers = []
    for name in sections:
        while len(result) % 8:
            result.append(0)
        offset = len(result)
        result.extend(payloads[name])
        headers.append(struct.pack('<IIQQQQIIQQ',
            shstrtab.index(name.encode() + b'\0') if name else 0,
            1 if name else 0, 0, 0, offset, len(payloads[name]), 0, 0, 1, 0))
    table = len(result)
    result.extend(b''.join(headers))
    result[:4] = b'\x7fELF'
    struct.pack_into('<Q', result, 0x28, table)
    struct.pack_into('<HHH', result, 0x3A, 64, len(headers), len(headers) - 1)
    return bytes(result)


def good_modules(**overrides):
    blobs = {
        'snd-hwdep.ko': dict(imports={'module_layout': 1, 'printk': 2},
                             exports={'snd_hwdep_new': 10}),
        'snd-usbmidi-lib.ko': dict(imports={'module_layout': 1, 'printk': 2},
                                   exports={'snd_usbmidi_disconnect': 11}),
        'snd-usb-audio.ko': dict(imports={'module_layout': 1, 'snd_card_new': 3,
                                          'snd_hwdep_new': 10, 'snd_usbmidi_disconnect': 11},
                                 depends='snd-usbmidi-lib,snd-hwdep'),
    }
    for name, changes in overrides.items():
        blobs[name] = {**blobs[name], **changes}
    return {name: module_blob(**fields) for name, fields in blobs.items()}


class UsbAudioModuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for path in usb.tool_files():
            (root / path.relative_to(ROOT)).parent.mkdir(parents=True, exist_ok=True)
            (root / path.relative_to(ROOT)).write_bytes(path.read_bytes())
        self.config = VENDOR_CONFIG
        self.output = root / 'work/test/usb-audio'
        vendor = {'.modinfo': f'vermagic={VERMAGIC}\0'.encode(),
                  '.gnu.linkonce.this_module': bytes(768),
                  '__versions': b''}
        self.vendor = {'vendor.ko': vendor}
        for context in [patch.object(usb, 'ROOT', root),
                        patch.object(usb.kernel_abi, 'load_inputs', side_effect=self.load_inputs),
                        patch.object(usb.audio_module.kernel_abi, 'exported_crcs',
                                     return_value=dict(KERNEL_EXPORTS))]:
            context.start()
            self.addCleanup(context.stop)
        silence = contextlib.redirect_stdout(io.StringIO())
        silence.__enter__()
        self.addCleanup(silence.__exit__, None, None, None)

    def load_inputs(self, target):
        kernel = b'Image\0IKCFG_ST' + gzip.compress(self.config) + b'IKCFG_ED'
        return kernel, self.vendor

    def built(self, blobs=None, effective=EFFECTIVE_CONFIG):
        build = usb.build_dir(usb.inputs('test')[2])
        build.mkdir(parents=True)
        for name, blob in (blobs or good_modules()).items():
            (build / name).write_bytes(blob)
        (build / 'baseline.config').write_bytes(BASELINE_CONFIG)
        (build / 'effective.config').write_bytes(effective)
        (build / '.built').touch()
        return build

    def validate(self, blobs):
        kernel, modules, _ = usb.inputs('test')
        return usb.validate_modules(blobs, kernel, modules)

    def test_valid_build_is_published_sealed_and_reused(self):
        self.built()
        usb.verify('test', self.output)
        self.assertEqual(sorted(path.name for path in self.output.glob('*.ko')),
                         sorted(usb.MODULES))
        self.assertTrue(usb.current('test', self.output))

    def test_kernel_with_builtin_driver_ships_nothing(self):
        self.config = VENDOR_CONFIG.replace(b'# CONFIG_SND_USB_AUDIO is not set',
                                            b'CONFIG_SND_USB_AUDIO=y')
        usb.verify('test', self.output)
        self.assertEqual(list(self.output.glob('*.ko')), [])
        self.assertTrue(usb.current('test', self.output))

    def test_prepare_skips_finished_shared_build(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            usb.prepare('test')
        build = Path(out.getvalue().strip())
        self.assertTrue((build / 'Module.symvers').read_text().startswith('0x00000001\tmodule_layout'))
        (build / '.built').touch()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            usb.prepare('test')
        self.assertEqual(out.getvalue(), '')

    def test_shared_build_is_rechecked_against_each_kernel(self):
        self.built()
        usb.verify('test', self.output)
        usb.audio_module.kernel_abi.exported_crcs.return_value = {**KERNEL_EXPORTS, 'printk': 9}
        with self.assertRaisesRegex(ValueError, 'printk is not exported'):
            usb.verify('test', self.output)
        self.assertFalse(usb.current('test', self.output))

    def test_unfinished_build_is_rejected(self):
        build = self.built()
        (build / '.built').unlink()
        with self.assertRaisesRegex(ValueError, 'build did not finish'):
            usb.verify('test', self.output)

    def test_unversioned_module_import_is_rejected(self):
        # modpost without snd-hwdep's exports leaves snd_hwdep_new unversioned.
        blobs = good_modules(**{'snd-usb-audio.ko': dict(
            imports={'module_layout': 1, 'snd_card_new': 3, 'snd_usbmidi_disconnect': 11},
            unversioned=['snd_hwdep_new'], depends='snd-usbmidi-lib')})
        with self.assertRaisesRegex(ValueError, 'unversioned imports: snd_hwdep_new'):
            self.validate(blobs)

    def test_missing_module_dependency_is_rejected(self):
        blobs = good_modules(**{'snd-usb-audio.ko': dict(depends='snd-usbmidi-lib')})
        with self.assertRaisesRegex(ValueError, 'does not depend on snd-hwdep'):
            self.validate(blobs)

    def test_wrong_module_import_version_is_rejected(self):
        blobs = good_modules(**{'snd-usb-audio.ko': dict(
            imports={'module_layout': 1, 'snd_hwdep_new': 99, 'snd_usbmidi_disconnect': 11},
            depends='snd-usbmidi-lib,snd-hwdep')})
        with self.assertRaisesRegex(ValueError, 'snd_hwdep_new version differs'):
            self.validate(blobs)

    def test_wrong_kernel_import_version_is_rejected(self):
        blobs = good_modules(**{'snd-hwdep.ko': dict(
            imports={'module_layout': 1, 'printk': 99}, exports={'snd_hwdep_new': 10})})
        with self.assertRaisesRegex(ValueError, 'printk is not exported'):
            self.validate(blobs)

    def test_export_already_in_kernel_is_rejected(self):
        blobs = good_modules(**{'snd-hwdep.ko': dict(
            imports={'module_layout': 1}, exports={'snd_hwdep_new': 10, 'printk': 2})})
        with self.assertRaisesRegex(ValueError, 'printk is already exported'):
            self.validate(blobs)

    def test_wrong_vermagic_and_layout_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'vermagic differs'):
            self.validate(good_modules(**{'snd-hwdep.ko': dict(vermagic='wrong')}))
        with self.assertRaisesRegex(ValueError, 'struct module differs'):
            self.validate(good_modules(**{'snd-usb-audio.ko': dict(module_size=760)}))

    def test_extra_config_change_is_rejected(self):
        self.built(effective=EFFECTIVE_CONFIG.replace(b'CONFIG_SND_PCM=y', b'CONFIG_SND_PCM=m'))
        with self.assertRaisesRegex(ValueError, 'changes other options: CONFIG_SND_PCM'):
            usb.verify('test', self.output)

    def test_corrupt_or_extra_module_is_a_cache_miss(self):
        self.built()
        usb.verify('test', self.output)
        (self.output / 'snd-hwdep.ko').write_bytes(b'corrupt')
        self.assertFalse(usb.current('test', self.output))
        usb.verify('test', self.output)
        (self.output / 'stale.ko').write_bytes(b'')
        self.assertFalse(usb.current('test', self.output))

    def test_cache_rechecks_abi_even_with_updated_digest(self):
        self.built()
        usb.verify('test', self.output)
        bad = good_modules(**{'snd-hwdep.ko': dict(vermagic='wrong')})['snd-hwdep.ko']
        (self.output / 'snd-hwdep.ko').write_bytes(bad)
        record = json.loads((self.output / '.artifact.json').read_text())
        record['modules']['snd-hwdep.ko'] = usb.digest(bad)
        (self.output / '.artifact.json').write_text(json.dumps(record))
        self.assertFalse(usb.current('test', self.output))

    def test_changed_tool_is_a_cache_miss(self):
        self.built()
        usb.verify('test', self.output)
        tool = usb.ROOT / 'build-usb-audio-modules.sh'
        tool.write_text(tool.read_text() + '\n# changed\n')
        self.assertFalse(usb.current('test', self.output))

    def test_rcs_loads_modules_in_background_in_dependency_order(self):
        rcs = (ROOT / 'overlay/etc/init.d/rcS').read_text()
        block = re.search(r'if \[ -f /lib/modules/snd-usb-audio.ko \]; then\n(.*?)\nfi', rcs, re.S)
        self.assertIsNotNone(block)
        loads = re.findall(r'/sbin/insmod /lib/modules/(\S+)\.ko', block.group(1))
        self.assertEqual(loads, [name[:-3] for name in usb.MODULES])
        self.assertTrue(block.group(1).rstrip().endswith('&'))


if __name__ == '__main__':
    unittest.main()
