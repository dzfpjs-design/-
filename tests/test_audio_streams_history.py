"""Narrow historical delegation tests, not a successful PG/capture fixture.

The release validator is an interface spy here. Actual package/plan integrity and
native capture journeys must also run in Root's real PostgreSQL customer flow.
"""
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from generic_agents import audio_streams as service


class SelectedReleaseDB:
    def __init__(self, value): self.value = value
    def scalar(self, statement): return self.value


def historical(monkeypatch, *, returned=None, release_present=True, same_instance=True):
    from generic_agents import instance_data_receipts
    instance_id = str(uuid4()); release_id = str(uuid4())
    stream = SimpleNamespace(instance_id=instance_id, status='stopped')
    release = SimpleNamespace(id=release_id, instance_id=instance_id if same_instance else str(uuid4()))
    p = SimpleNamespace(workspace_id=str(uuid4()))
    binding = {'schema': 'historical-unit-only', 'business_id': 'business_original', 'binding_hash': 'a' * 64}
    seen = []
    def verify(db, actor, original):
        seen.append((actor, original))
        return returned if returned is not None else {'audio_sources': [binding]}
    monkeypatch.setattr(instance_data_receipts, 'verify_release', verify)
    return SelectedReleaseDB(release if release_present else None), p, stream, release, binding, seen


@pytest.mark.parametrize('case', ['missing', 'wrong-instance', 'changed-binding', 'missing-declaration', 'duplicate-binding'])
def test_original_release_missing_or_declaration_changed_blocks_source_details(monkeypatch, case):
    args = historical(monkeypatch, release_present=case != 'missing', same_instance=case != 'wrong-instance')
    db, p, stream, release, binding, seen = args
    if case in {'changed-binding', 'missing-declaration', 'duplicate-binding'}:
        from generic_agents import instance_data_receipts
        changed = {**binding, 'business_id': 'business_replaced'}
        plan = {'audio_sources': [changed]} if case == 'changed-binding' else {} if case == 'missing-declaration' else {'audio_sources': [binding, binding]}
        def verify(actual_db, actual_p, original):
            seen.append((actual_p, original)); return plan
        monkeypatch.setattr(instance_data_receipts, 'verify_release', verify)
    with pytest.raises(HTTPException) as error:
        service._verify_capture_release(db, p, stream, release.id, binding)
    assert error.value.status_code == 409
    if case in {'missing', 'wrong-instance'}: assert seen == []


def test_original_release_validator_error_is_preserved_without_live_or_repair_fallback(monkeypatch):
    db, p, stream, release, binding, seen = historical(monkeypatch)
    from generic_agents import instance_data_receipts
    def broken(actual_db, actual_p, original): raise HTTPException(409, 'saved package changed')
    monkeypatch.setattr(instance_data_receipts, 'verify_release', broken)
    with pytest.raises(HTTPException) as error: service._verify_capture_release(db, p, stream, release.id, binding)
    assert error.value.status_code == 409 and error.value.detail == 'saved package changed'


def test_historical_declaration_delegates_without_requiring_current_active_or_model_ready(monkeypatch):
    db, p, stream, release, binding, seen = historical(monkeypatch)
    assert service._verify_capture_release(db, p, stream, release.id, binding) is release
    assert seen == [(p, release)]
