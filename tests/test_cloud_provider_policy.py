from types import SimpleNamespace
from unittest.mock import patch

import pytest
from generic_agents import gateway, retrieval
from generic_agents.cloud_provider_policy import provider_enabled


def config(value):
    return SimpleNamespace(cloud_allowed_providers=value)


def test_allowlist_fails_closed_for_unknown_or_empty_entries():
    for value in ('', 'deepseek,', 'deepseek,unknown'):
        with patch('generic_agents.cloud_provider_policy.settings', return_value=config(value)):
            assert not provider_enabled('deepseek')
    with patch('generic_agents.cloud_provider_policy.settings', return_value=config('deepseek')):
        assert provider_enabled('deepseek')
        assert not provider_enabled('qwen')


def test_disabled_provider_cannot_reach_transport_or_callback():
    capability = SimpleNamespace(data={'execution': {'provider': 'qwen'}})
    with patch('generic_agents.cloud_provider_policy.settings', return_value=config('deepseek')), \
         patch.object(gateway, '_cloud_json') as send:
        with pytest.raises(gateway.ModelBlocked, match='未发送或预留费用'):
            gateway.cloud_json(capability, [], {}, on_reserved=lambda *_: pytest.fail('reserved'))
        send.assert_not_called()


def test_enabled_provider_delegates_to_existing_transport():
    capability = SimpleNamespace(data={'execution': {'provider': 'deepseek'}})
    with patch('generic_agents.cloud_provider_policy.settings', return_value=config('deepseek')), \
         patch.object(gateway, '_cloud_json', return_value=('ok', {})) as send:
        assert gateway.cloud_json(capability, [], {}) == ('ok', {})
        send.assert_called_once()


def test_disabled_embedding_fails_before_budget_or_network():
    with patch('generic_agents.cloud_provider_policy.settings', return_value=config('deepseek')), \
         patch('generic_agents.cloud_budget.reserve') as reserve, \
         patch.object(retrieval.httpx, 'Client') as client:
        with pytest.raises(gateway.ModelBlocked, match='向量服务当前未启用'):
            retrieval.cloud_embed(None, None, ['example'])
        reserve.assert_not_called()
        client.assert_not_called()
