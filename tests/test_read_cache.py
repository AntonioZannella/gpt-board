import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from board_storage import BoardStorage, DamagedFileError, empty_snapshot, write_json


class ReadCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.storage=BoardStorage(Path(self.tmp.name));self.path=self.storage.path('default','board.json')
        write_json(self.path,empty_snapshot('Original'))

    def test_cached_reads_skip_validation_but_caller_edits_are_isolated(self):
        before=self.storage.read('default')
        with patch('validation.snapshot', side_effect=AssertionError('Unchanged file was revalidated')):
            mutated=self.storage.read('default');mutated['store'].clear()
            self.assertEqual(self.storage.read('default'),before)
        before['store'].clear()
        self.assertTrue(self.storage.read('default')['store'])

    def test_replacement_same_length_and_restored_mtime_invalidates(self):
        before=self.storage.read('default');stat=self.path.stat()
        encoded=self.path.read_text().replace('Original','Modified')
        self.path.write_text(encoded);os.utime(self.path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        self.assertNotEqual(self.storage.read('default'),before)

    def test_corruption_and_deletion_never_return_cached_content(self):
        self.storage.read('default');self.path.write_text('{broken')
        with self.assertRaises(DamagedFileError):self.storage.read('default')
        self.path.unlink();self.assertIsNone(self.storage.read('default'))

    def test_cache_is_bounded_and_evicts_least_recently_read(self):
        for notebook in ['default','second','third']:
            write_json(self.storage.path(notebook,'board.json'),empty_snapshot(notebook))
            self.storage.read(notebook)
        self.assertEqual(list(self.storage.read_cache),['second','third'])
        self.storage.read('second');self.storage.read('default')
        self.assertEqual(list(self.storage.read_cache),['second','default'])
        with patch('board_storage.MAX_BOARD_BYTES',1):
            write_json(self.storage.path('fourth','board.json'),empty_snapshot('fourth'))
            self.storage.read('fourth')
            self.assertNotIn('fourth',self.storage.read_cache)

    def test_revision_changes_for_same_size_replacement_with_restored_mtime(self):
        before = self.storage.revision('default')
        stat = self.path.stat()
        replacement = self.path.with_suffix('.replacement')
        replacement.write_text(self.path.read_text().replace('Original', 'Modified'))
        os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        os.replace(replacement, self.path)
        self.assertNotEqual(self.storage.revision('default'), before)

    def test_single_record_read_does_not_copy_unrelated_records(self):
        import copy
        original = self.storage.read('default')
        key = next(iter(original['store']))
        with patch('board_storage.copy.deepcopy', wraps=copy.deepcopy) as clone:
            result = self.storage.read('default', record_id=key)
            self.assertEqual(clone.call_count, 1)
            self.assertEqual(clone.call_args.args[0], original['store'][key])
        result.clear()
        self.assertEqual(self.storage.read('default'), original)
        self.assertIsNone(self.storage.read('default', record_id='asset:absent'))

    def test_cold_single_record_read_is_isolated_and_invalidates(self):
        expected = self.storage.read('default')
        key = next(iter(expected['store']))
        self.storage.read_cache.clear()
        self.storage.read('default', record_id=key).clear()
        self.assertEqual(self.storage.read('default'), expected)
        changed = self.path.read_text().replace('Original', 'Modified')
        self.path.write_text(changed)
        self.assertEqual(self.storage.read('default', record_id=key), self.storage.read('default')['store'][key])
