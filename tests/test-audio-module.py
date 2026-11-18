#!/usr/bin/env python3
"""Audio module ABI/cache fault injection; no Docker or vendor downloads needed."""
import contextlib
import gzip
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import audio_module as audio


def versions(values):
    return b''.join(struct.pack('<Q', crc) + name.encode().ljust(56, b'\0')
                    for name, crc in values.items())


def module_blob(*, vermagic='4.9.170 SMP aarch64', crc=123,
                include_version=True, module_size=768, text=b'original code'):
    """Minimal real ELF64 sections consumed by the production ELF parser."""
    payloads = {
        '': b'', '.text': text, '.modinfo': b'vermagic=' + vermagic.encode() + b'\0',
        '__versions': versions({'printk': crc, 'module_layout': 456}
                               if include_version else {'module_layout': 456}),
        '.gnu.linkonce.this_module': bytes(module_size),
        '.strtab': b'\0printk\0',
        '.symtab': bytes(24) + struct.pack('<IBBHQQ', 1, 16, 0, 0, 0, 0),
    }
    names = list(payloads) + ['.shstrtab']
    strings = b'\0' + b''.join(name.encode() + b'\0' for name in names[1:])
    payloads['.shstrtab'] = strings
    result = bytearray(64)
    headers = []
    for name in names:
        while len(result) % 8:
            result.append(0)
        offset = len(result)
        result.extend(payloads[name])
        headers.append(struct.pack('<IIQQQQIIQQ',
            strings.index(name.encode() + b'\0') if name else 0,
            1 if name else 0, 0, 0, offset, len(payloads[name]), 0, 0, 1, 0))
    table = len(result)
    result.extend(b''.join(headers))
    result[:4] = b'\x7fELF'
    struct.pack_into('<Q', result, 0x28, table)
    struct.pack_into('<HHH', result, 0x3A, 64, len(headers), len(headers) - 1)
    return bytes(result)


class AudioModuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.source = root / 'source'
        self.source.mkdir()
        self.late_usb = root / 'late_usb'
        self.late_usb.mkdir()
        (self.late_usb / 'h700_late_usb.c').write_text('/* usb */\n')
        (self.late_usb / 'Makefile').write_text('obj-m += h700_late_usb.o\n')
        self.rumble_pwm = root / 'rumble_pwm'
        self.rumble_pwm.mkdir()
        (self.rumble_pwm / 'h700_rumble_pwm.c').write_text('/* rumble */\n')
        (self.rumble_pwm / 'Makefile').write_text('obj-m += h700_rumble_pwm.o\n')
        self.output = root / 'out'
        (self.output / 'module').mkdir(parents=True)
        self.config = b'CONFIG_ARM64=y\n'
        self.kernel = b'Image\0IKCFG_ST' + gzip.compress(self.config) + b'IKCFG_ED'
        self.profile = {'test': {'image_sha256': audio.digest(self.kernel),
                                'config_sha256': audio.digest(self.config)}}
        self.write_profiles()
        vendor = {'.modinfo': b'vermagic=4.9.170 SMP aarch64\0',
                  '.gnu.linkonce.this_module': bytes(768),
                  '__versions': versions({'printk': 123, 'module_layout': 456})}
        for context in [patch.object(audio, 'ROOT', root),
                        patch.object(audio, 'SOURCE', self.source),
                        patch.object(audio, 'LATE_USB', self.late_usb),
                        patch.object(audio, 'RUMBLE_PWM', self.rumble_pwm),
                        patch.object(audio.kernel_abi, 'load_inputs',
                                     return_value=(self.kernel, {'vendor.ko': vendor})),
                        patch.object(audio.kernel_abi, 'exported_crcs',
                                     return_value={'printk': 123, 'module_layout': 456})]:
            context.start()
            self.addCleanup(context.stop)
        self.vendor = {'vendor.ko': vendor}
        self.blob = module_blob()
        for name in audio.MODULES:
            (self.output / f'module/{name}.ko').write_bytes(self.blob)
        (self.output / 'expected.json').write_text(json.dumps({'stamp': audio.stamp('test')}))
        silence = contextlib.redirect_stdout(io.StringIO())
        silence.__enter__()
        self.addCleanup(silence.__exit__, None, None, None)

    def write_profiles(self):
        (self.source / 'profiles.json').write_text(json.dumps(self.profile))

    def test_valid_artifact_is_sealed_and_reused(self):
        audio.verify('test', self.output)
        self.assertTrue(audio.current('test', self.output))
        record = json.loads((self.output / '.artifact.json').read_text())
        self.assertEqual(record['sha256'], audio.digest(self.blob))

    def test_wrong_target_and_kernel_hash_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unaudited audio kernel'):
            audio.inputs('wrong')
        self.profile['test']['image_sha256'] = '0' * 64
        self.write_profiles()
        with self.assertRaisesRegex(ValueError, 'unaudited audio kernel'):
            audio.inputs('test')

    def test_config_hash_mismatch_is_rejected(self):
        self.profile['test']['config_sha256'] = '0' * 64
        self.write_profiles()
        with self.assertRaisesRegex(ValueError, 'config differs'):
            audio.inputs('test')

    def test_wrong_vermagic_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'vermagic differs'):
            audio.validate_module(module_blob(vermagic='wrong'), self.kernel, self.vendor)

    def test_wrong_crc_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'symbol versions differ'):
            audio.validate_module(module_blob(crc=999), self.kernel, self.vendor)

    def test_missing_import_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unversioned imports: printk'):
            audio.validate_module(module_blob(include_version=False), self.kernel, self.vendor)

    def test_wrong_module_layout_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'struct module differs'):
            audio.validate_module(module_blob(module_size=760), self.kernel, self.vendor)

    def test_corrupt_code_is_a_cache_miss(self):
        for name in audio.MODULES:
            audio.verify('test', self.output)
            self.assertTrue(audio.current('test', self.output))
            (self.output / f'{name}.ko').write_bytes(module_blob(text=b'corrupt code!'))
            self.assertFalse(audio.current('test', self.output))

    def test_every_module_is_checked_and_published(self):
        for extra in audio.MODULES[1:]:
            (self.output / f'module/{extra}.ko').write_bytes(module_blob(crc=999))
            with self.assertRaisesRegex(ValueError, 'symbol versions differ'):
                audio.verify('test', self.output)
            self.assertFalse((self.output / '.artifact.json').exists())
            (self.output / f'module/{extra}.ko').unlink()
            with self.assertRaises(OSError):
                audio.verify('test', self.output)
            (self.output / f'module/{extra}.ko').write_bytes(self.blob)
        audio.verify('test', self.output)
        record = json.loads((self.output / '.artifact.json').read_text())
        self.assertEqual(set(record['modules']), set(audio.MODULES))
        for name in audio.MODULES:
            self.assertEqual((self.output / f'{name}.ko').read_bytes(), self.blob)

    def test_truncated_cache_metadata_is_a_cache_miss(self):
        audio.verify('test', self.output)
        (self.output / '.artifact.json').write_text('{')
        self.assertFalse(audio.current('test', self.output))

    def test_wrong_target_cache_record_is_a_miss(self):
        audio.verify('test', self.output)
        record = json.loads((self.output / '.artifact.json').read_text())
        record['target'] = 'wrong'
        (self.output / '.artifact.json').write_text(json.dumps(record))
        self.assertFalse(audio.current('test', self.output))

    def test_tampered_cached_abi_metadata_cannot_authorize_bad_module(self):
        # A forged expected.json/exports.json cannot override the actual kernel.
        (self.output / 'expected.json').write_text(json.dumps(
            {'stamp': audio.stamp('test'), 'vermagic': 'wrong', 'module_size': 760}))
        (self.output / 'exports.json').write_text(json.dumps({'printk': 999}))
        (self.output / 'module/h700_speaker_amp.ko').write_bytes(module_blob(crc=999))
        with self.assertRaisesRegex(ValueError, 'symbol versions differ'):
            audio.verify('test', self.output)
        self.assertFalse((self.output / '.artifact.json').exists())

    def test_cache_rechecks_abi_even_with_updated_output_digest(self):
        audio.verify('test', self.output)
        bad = module_blob(vermagic='wrong')
        (self.output / 'h700_speaker_amp.ko').write_bytes(bad)
        record = json.loads((self.output / '.artifact.json').read_text())
        record['sha256'] = audio.digest(bad)
        (self.output / '.artifact.json').write_text(json.dumps(record))
        self.assertFalse(audio.current('test', self.output))

    def test_changed_sources_are_a_cache_miss(self):
        for folder in (self.source, self.late_usb, self.rumble_pwm):
            (self.output / 'expected.json').write_text(json.dumps({'stamp': audio.stamp('test')}))
            audio.verify('test', self.output)
            (folder / 'changed.c').write_text('changed implementation')
            self.assertFalse(audio.current('test', self.output))


if __name__ == '__main__':
    unittest.main()
