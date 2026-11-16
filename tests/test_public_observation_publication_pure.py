"""Publication recovery for an explicitly registered public source."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from generic_agents import public_observations as public


@pytest.mark.parametrize('initial', ['draft', 'validated', 'published'])
def test_public_source_publication_recovers_from_each_state(monkeypatch, initial):
    data = public.manifest('nps_redwood').model_dump(mode='json')
    row = SimpleNamespace(id='source', data=data, namespace=public.NAMESPACE,
                          content_hash=public.digest(data), status=initial, version=1)
    calls = []
    monkeypatch.setattr(public, 'require', lambda *args: None)
    monkeypatch.setattr(public, 'scoped', lambda *args: row)
    monkeypatch.setattr(public, 'check_version', lambda obj, expected: None)
    def validate(db, principal, identity, version):
        calls.append('validate'); row.status='validated'
    def publish(db, principal, identity, version, reason):
        assert row.status == 'validated'
        calls.append('publish'); row.status='published'
    monkeypatch.setattr(public, 'validate_capability', validate)
    monkeypatch.setattr(public, 'publish', publish)
    monkeypatch.setattr(public, 'audit', lambda *args: None)
    result = public.publish_source('source', public.Publication(version=1, confirm_read_only=True),
                                   object(), SimpleNamespace(commit=lambda: None))
    assert result['status'] == 'published'
    assert calls == {'draft':['validate','publish'], 'validated':['publish'], 'published':[]}[initial]


def test_public_source_publication_rejects_changed_content_hash(monkeypatch):
    data = public.manifest('nps_redwood').model_dump(mode='json')
    row = SimpleNamespace(id='source', data=data, namespace=public.NAMESPACE,
                          content_hash='wrong', status='validated', version=1)
    monkeypatch.setattr(public, 'require', lambda *args: None)
    monkeypatch.setattr(public, 'scoped', lambda *args: row)
    monkeypatch.setattr(public, 'check_version', lambda *args: None)
    with pytest.raises(HTTPException) as error:
        public.publish_source('source', public.Publication(version=1, confirm_read_only=True), object(), object())
    assert error.value.status_code == 409
