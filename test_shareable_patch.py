"""Offline tests for the shareable delta; no private firmware dump is needed."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import shareable_patch as patcher

MANIFEST = Path(__file__).with_name(patcher.MANIFEST_NAME)


def b64(data):
    return base64.b64encode(data).decode('ascii')


def synthetic_case():
    """Return a small fake image and manifest for exercising patch mechanics."""
    size = 0x10000
    sector = 2048
    prefix = size
    osos_start, osos_length = 0x5000, 0x1000
    rsrc_start, rsrc_length = 0x7000, 0x1000
    aupd_start, aupd_length = 0x9000, 0x1000
    source = bytearray(size)
    target = bytearray(source)
    target[osos_start + 10] = 0x7f
    checksum = sum(target[osos_start:osos_start + osos_length]) & 0xffffffff
    struct.pack_into('<I', target, 0x421c, checksum)
    changes = [
        {'offset': 0x421c, 'length': 4, 'before_base64': b64(source[0x421c:0x4220]),
         'after_base64': b64(target[0x421c:0x4220]), 'reason': 'test checksum'},
        {'offset': osos_start + 10, 'length': 1, 'before_base64': b64(b'\0'),
         'after_base64': b64(b'\x7f'), 'reason': 'test payload'},
    ]
    manifest = {
        'schema': 1,
        'experiment': '002-integral-apple13-share',
        'bytes': size,
        'source_reference_sha256': '1' * 64,
        'candidate_reference_sha256': '2' * 64,
        'target_osos_sha256': hashlib.sha256(target[osos_start:osos_start + osos_length]).hexdigest(),
        'changed_byte_count': sum(a != b for a, b in zip(source, target)),
        'physical_sector_count': 2,
        'physical_write_bytes': 2 * sector,
        'sector_size': sector,
        'recovery_prefix_bytes': prefix,
        'checksum_sector': 0x4000,
        'checksum_sector_last': True,
        'sector_write_order': [0x5000, 0x4000],
        'changes': changes,
    }
    return bytes(source), bytes(target), manifest


class ShareablePatchChecks(unittest.TestCase):
    def test_manifest_matches_reviewed_counts(self):
        manifest, spans, sectors = patcher.load_manifest(MANIFEST)
        self.assertEqual(manifest['changed_byte_count'], 124977)
        self.assertEqual(len(spans), 6953)
        self.assertEqual(len(sectors), 155)

    def test_synthetic_candidate_reconstructs_and_preserves_unlisted_bytes(self):
        source, expected, manifest = synthetic_case()
        with tempfile.NamedTemporaryFile('w', suffix='.json') as f:
            json.dump(manifest, f)
            f.flush()
            with patch.object(patcher, 'SIZE', len(source)), \
                 patch.object(patcher, 'PREFIX', len(source)), \
                 patch.object(patcher, 'SECTOR', 2048), \
                 patch.object(patcher, 'OSOS_START', 0x5000), \
                 patch.object(patcher, 'OSOS_LENGTH', 0x1000), \
                 patch.object(patcher, 'RSRC_START', 0x7000), \
                 patch.object(patcher, 'RSRC_LENGTH', 0x1000), \
                 patch.object(patcher, 'AUPD_START', 0x9000), \
                 patch.object(patcher, 'AUPD_LENGTH', 0x1000), \
                 patch.object(patcher, 'TARGET_OSOS_SHA', manifest['target_osos_sha256']), \
                 patch.object(patcher, 'native_layout', return_value={'synthetic': True}):
                out, report = patcher.apply_patch(source, f.name)
        self.assertEqual(out, expected)
        self.assertEqual(report['changed_byte_count'], manifest['changed_byte_count'])
        self.assertEqual(out[0x6000], source[0x6000])

    def test_changed_reviewed_byte_is_refused(self):
        source, _, manifest = synthetic_case()
        mutated = bytearray(source)
        mutated[0x500a] = 1
        with tempfile.NamedTemporaryFile('w', suffix='.json') as f:
            json.dump(manifest, f)
            f.flush()
            with patch.object(patcher, 'SIZE', len(source)), \
                 patch.object(patcher, 'PREFIX', len(source)), \
                 patch.object(patcher, 'SECTOR', 2048), \
                 patch.object(patcher, 'OSOS_START', 0x5000), \
                 patch.object(patcher, 'OSOS_LENGTH', 0x1000), \
                 patch.object(patcher, 'RSRC_START', 0x7000), \
                 patch.object(patcher, 'RSRC_LENGTH', 0x1000), \
                 patch.object(patcher, 'AUPD_START', 0x9000), \
                 patch.object(patcher, 'AUPD_LENGTH', 0x1000), \
                 patch.object(patcher, 'TARGET_OSOS_SHA', manifest['target_osos_sha256']), \
                 patch.object(patcher, 'native_layout', return_value={'synthetic': True}), \
                 self.assertRaisesRegex(ValueError, 'Input differs at a reviewed patch range'):
                patcher.apply_patch(bytes(mutated), f.name)

    def test_manifest_overlap_and_wrong_sector_order_are_refused(self):
        original = json.loads(MANIFEST.read_text())
        for mutate in ('overlap', 'order'):
            broken = copy.deepcopy(original)
            if mutate == 'overlap':
                broken['changes'].insert(1, copy.deepcopy(broken['changes'][0]))
            else:
                broken['sector_write_order'] = list(reversed(broken['sector_write_order']))
            with tempfile.NamedTemporaryFile('w', suffix='.json') as f:
                json.dump(broken, f)
                f.flush()
                with self.assertRaises(ValueError):
                    patcher.load_manifest(f.name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
