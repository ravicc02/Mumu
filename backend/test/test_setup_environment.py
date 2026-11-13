import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.services.setup_environment import setup_environment


class SetupEnvironmentTests(unittest.TestCase):
    def test_existing_runtime_browser_and_writable_storage_are_ready(self):
        with tempfile.TemporaryDirectory() as root, patch('app.services.setup_environment.chrome_executable', return_value=Path('C:/browser/chrome.exe')):
            state = setup_environment(Settings(data_root=Path(root)))
        self.assertEqual(state['status'], 'ready')
        self.assertTrue(state['backendReady'])
        self.assertTrue(all(item['status'] == 'ready' for item in state['items']))
        self.assertFalse(any(item['installable'] for item in state['items']))

    def test_missing_browser_blocks_setup_and_exposes_fixed_manual_command(self):
        with tempfile.TemporaryDirectory() as root, patch('app.services.setup_environment.chrome_executable', side_effect=RuntimeError('Browser missing')):
            state = setup_environment(Settings(data_root=Path(root)))
        self.assertEqual(state['status'], 'blocked')
        self.assertEqual(state['items'][1]['status'], 'missing')
        self.assertIn('winget install --id Google.Chrome --exact', state['items'][1]['command'])

    def test_missing_storage_cannot_claim_ready(self):
        with tempfile.TemporaryDirectory() as root, patch('app.services.setup_environment.chrome_executable', return_value=Path('chrome.exe')):
            state = setup_environment(Settings(data_root=Path(root) / 'missing'))
        self.assertEqual(state['status'], 'blocked')
        self.assertEqual(state['items'][2]['status'], 'error')
