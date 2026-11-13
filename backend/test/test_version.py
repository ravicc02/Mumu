import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import version


class ApplicationVersionTests(unittest.TestCase):
    def test_source_version_matches_the_product_version_file(self):
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(version.__version__, (root / 'VERSION').read_text(encoding='utf-8').strip())

    def test_frozen_bundle_reads_its_own_version_and_does_not_silently_guess(self):
        with tempfile.TemporaryDirectory() as folder:
            module = Path(folder) / 'bundle/app/version.py'
            module.parent.mkdir(parents=True)
            with patch.object(version, '__file__', str(module)):
                with self.assertRaises(RuntimeError): version.read_version()
                module.with_name('VERSION').write_text('0.5.0-rc.1\n', encoding='utf-8')
                self.assertEqual(version.read_version(), '0.5.0-rc.1')
                for invalid in ('v0.5.0', '0.5.0-01', '0.5.0-rc..1', 'broken'):
                    module.with_name('VERSION').write_text(invalid, encoding='utf-8')
                    with self.assertRaises(RuntimeError): version.read_version()
