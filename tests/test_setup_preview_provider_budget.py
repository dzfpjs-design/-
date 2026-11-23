"""Synthetic setup previews exercise provider policy without dispatching cloud calls."""
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import CloudCall, Job
from generic_agents.gateway import ModelBlocked
from generic_agents import cloud_budget_policy, setup_understanding
from tests.test_setup_understanding import prepared
from tests.test_setup_understanding import start, process
from tests.test_setup_understanding_v4 import wire
from tests.test_vertical_slice import publish


def test_understanding_preview_uses_published_provider_without_reserving(client, monkeypatch):
    body, _ = prepared(client)
    body.update(contract_version=3, request_id=str(uuid4()))
    deepseek = json.loads(Path('examples/deepseek.json').read_text())
    deepseek['name'] = 'setup_preview_provider_policy_test'
    cap = publish(client, deepseek)
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 1)
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 1)

    blocked = client.post('/api/setup/understanding/preview', json=body)
    assert blocked.status_code == 409 and '预算' in blocked.json()['detail']
    allowed = client.post('/api/setup/understanding/preview', json={**body, 'model_id': cap['id']})
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()['provider'] == 'deepseek'
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall)) == 0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == 'cloud_budget')) == 0


def test_preview_reports_unverifiable_policy_without_claiming_quota_exhaustion(monkeypatch):
    def cannot_verify(*args, **kwargs):
        raise ModelBlocked('账本损坏')
    monkeypatch.setattr(cloud_budget_policy, 'budget_status', cannot_verify)
    with pytest.raises(HTTPException) as error:
        setup_understanding.preview_budget(object(), object(), 9, cap=object(), detailed=True)
    assert error.value.status_code == 409
    assert '无法核对' in error.value.detail and '未预留或发送' in error.value.detail
    assert '额度不足' not in error.value.detail


def test_deepseek_setup_journey_records_one_mocked_dispatch_with_zero_daily_limit(client, monkeypatch):
    body, _ = prepared(client)
    model = json.loads(Path('examples/deepseek.json').read_text())
    model['name'] = 'setup_unlimited_journey_test'
    cap = publish(client, model)
    body.update(contract_version=4, request_id=str(uuid4()), model_id=cap['id'])
    sent, _ = wire(monkeypatch)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr(settings(), 'deepseek_api_key', SecretStr('synthetic-not-a-real-key'))
    monkeypatch.setattr(settings(), 'cloud_daily_token_limit', 0)
    monkeypatch.setattr(settings(), 'cloud_daily_call_limit', 0)
    row, preview = start(client, body)
    process(row)
    result = client.get('/api/setup/understanding/' + row['id']).json()
    assert result['status'] == 'review', result.get('error')
    assert len(sent) == 1
    replay = client.post('/api/setup/understanding', json={**body, 'preview_hash':preview['preview_hash'], 'consent':True})
    assert replay.status_code == 202 and replay.json()['id'] == row['id']
    with Session(engine()) as db:
        calls = list(db.scalars(select(CloudCall)))
        assert len(calls) == 1 and calls[0].status == 'succeeded'
        assert calls[0].data['budget_policy_v1'] == 'deepseek_unlimited'
