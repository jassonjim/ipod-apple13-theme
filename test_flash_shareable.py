"""Offline transaction tests for the generic flasher; no device is opened."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import flash_shareable_patch as flasher
import shareable_patch as patcher


MANIFEST = Path(__file__).with_name(patcher.MANIFEST_NAME)


class TransactionChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, _, cls.sectors = patcher.load_manifest(MANIFEST)
        cls.before = bytes(patcher.SIZE)
        target = bytearray(cls.before)
        for at in cls.sectors:
            target[at] = 0xA5
        cls.target = bytes(target)
        cls.order = [s for s in cls.sectors if s != 0x4000] + [0x4000]

    def run_transaction(self, short=False, bad_readback=False):
        medium = bytearray(self.before)
        writes = []

        def pread(fd, n, at):
            data = bytes(medium[at:at + n])
            if bad_readback and writes and at == writes[-1]:
                data = bytes([data[0] ^ 1]) + data[1:]
            return data

        def pwrite(fd, data, at):
            writes.append(at)
            count = 1024 if short else len(data)
            medium[at:at + count] = data[:count]
            return count

        with tempfile.TemporaryDirectory() as name, \
             patch.object(flasher.os, 'pread', side_effect=pread), \
             patch.object(flasher.os, 'pwrite', side_effect=pwrite), \
             patch.object(flasher, 'sync_cache'), patch.object(flasher, 'guard'), \
             patch.object(flasher, 'timed', side_effect=lambda call, *args: call(*args)):
            try:
                attempted, completed = flasher.write_transaction(
                    42, self.before, self.target, self.order, {}, Path(name))
            except Exception as error:
                return medium, writes, error
        return medium, writes, completed

    def test_checksum_sector_is_last_and_full_target_is_reached(self):
        medium, writes, completed = self.run_transaction()
        self.assertEqual(medium, self.target)
        self.assertEqual(writes, self.order)
        self.assertEqual(writes[-1], 0x4000)
        self.assertEqual(completed, self.order)

    def test_short_write_and_readback_mismatch_stop_before_checksum(self):
        for short, bad in ((True, False), (False, True)):
            _, writes, error = self.run_transaction(short=short, bad_readback=bad)
            self.assertIsNotNone(error)
            self.assertEqual(len(writes), 1)
            self.assertNotIn(0x4000, writes)


if __name__ == '__main__':
    unittest.main(verbosity=2)
