import asyncio
import unittest
from unittest.mock import patch, Mock
from fastapi import HTTPException
from app import main


class ReasoningSettingsTests(unittest.TestCase):
    def test_invalid_effort_does_not_write(self):
        with patch.object(main, '_store_config') as store:
            with self.assertRaises(HTTPException) as error:
                asyncio.run(main.save_reasoning_effort(main.ReasoningEffortRequest(reasoning_effort='invalid')))
            self.assertEqual(error.exception.status_code, 400)
            store.assert_not_called()

    def test_effort_update_preserves_provider_configuration(self):
        config = Mock()
        updated = object()
        config.model_copy.return_value = updated
        with patch.object(main, 'load_agent_model_config', return_value=config), patch.object(main, '_store_config') as store:
            result = asyncio.run(main.save_reasoning_effort(main.ReasoningEffortRequest(reasoning_effort='low')))
            self.assertEqual(result, {'reasoning_effort': 'low'})
            config.model_copy.assert_called_once_with(update={'reasoning_effort': 'low'})
            store.assert_called_once_with(main.database, updated)

    def test_provider_save_without_effort_preserves_current_value(self):
        config = Mock()
        config.models = []; config.routes = {}
        with patch.object(main, 'get_model_config', return_value={'reasoning_effort': 'low'}), patch.object(main, 'save_multi_provider_config', return_value=config) as save, patch.object(main, 'model_providers_view', return_value=[]):
            asyncio.run(main.save_model_settings(main.ModelConfigRequest(providers=[])))
            self.assertEqual(save.call_args.kwargs['reasoning_effort'], 'low')

    def test_missing_provider_is_actionable(self):
        with patch.object(main, 'load_agent_model_config', return_value=None):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(main.save_reasoning_effort(main.ReasoningEffortRequest(reasoning_effort='high')))
            self.assertEqual(error.exception.status_code, 409)
