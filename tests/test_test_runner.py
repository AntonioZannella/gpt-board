"""The default command must include new frontend regression suites."""
import importlib.util
import tempfile
import unittest
from pathlib import Path


class TestDiscovery(unittest.TestCase):
    def test_all_supported_frontend_suites_are_discovered(self):
        source = Path(__file__).resolve().parents[1] / 'test.py'
        spec = importlib.util.spec_from_file_location('board_test_runner', source)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'tests').mkdir()
            for name in ('trackpad.test.js', 'touch-navigation.test.mjs', 'helper.js', 'example.json'):
                (root / 'tests' / name).touch()
            self.assertEqual(runner.browser_tests(root), [
                'tests/touch-navigation.test.mjs', 'tests/trackpad.test.js',
            ])
