import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.agents.secrets import SecretServiceStore, SecretStoreError, SecretNotFound, secret_store_for


class LinuxSecretTests(unittest.TestCase):
    def test_secret_service_uses_stdin_and_separates_data_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = SecretServiceStore(Path(temporary) / 'first')
            second = SecretServiceStore(Path(temporary) / 'second')
            self.assertNotEqual(first.namespace, second.namespace)
            with patch('app.services.agents.secrets.shutil.which', return_value='/usr/bin/secret-tool'), patch(
                'app.services.agents.secrets.subprocess.run', return_value=subprocess.CompletedProcess([],0,stdout='',stderr='')
            ) as execute:
                first.put('provider', 'isolated-value')
                args = execute.call_args.args[0]
                self.assertNotIn('isolated-value',args)
                self.assertIn(first.namespace,args)
                self.assertEqual(execute.call_args.kwargs['input'],'isolated-value')
            self.assertFalse(list(Path(temporary).rglob('*.bin')))

    def test_missing_locked_and_timed_out_keyring_errors_keep_helper_output_private(self):
        store = SecretServiceStore(Path('/isolated'))
        with patch('app.services.agents.secrets.shutil.which',return_value=None), self.assertRaises(SecretStoreError):
            store.put('provider','isolated-value')
        with patch('app.services.agents.secrets.shutil.which',return_value='/usr/bin/secret-tool'):
            with patch('app.services.agents.secrets.subprocess.run',return_value=subprocess.CompletedProcess([],1,stdout='',stderr='')):
                with self.assertRaises(SecretNotFound):
                    store.get('provider')
                store.delete('provider')
            with patch('app.services.agents.secrets.subprocess.run',return_value=subprocess.CompletedProcess([],1,stdout='',stderr='isolated-value')):
                with self.assertRaises(SecretStoreError) as failure:
                    store.put('provider','isolated-value')
                self.assertNotIn('isolated-value',str(failure.exception))
            with patch('app.services.agents.secrets.subprocess.run',side_effect=subprocess.TimeoutExpired('secret-tool',20)):
                with self.assertRaises(SecretStoreError):
                    store.get('provider')

    def test_linux_selects_secret_service_without_requiring_it_during_startup(self):
        with patch('app.services.agents.secrets.sys.platform','linux'):
            self.assertIsInstance(secret_store_for(SimpleNamespace(secrets_root=Path('/isolated'))),SecretServiceStore)
