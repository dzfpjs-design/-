"""Real offline local ASR, original Run, native receiver and future graph path.

Root alone runs this in a new disposable PostgreSQL database. The baseline
test imports only existing public paths so the sealed V135 source demonstrates
the actual missing reception, rather than an absent-module collection error.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.worker import deliver_events
from tests.test_workflow_audio import configured
from tests.test_workflow_abilities import finish_job


def good(response, status=200):
    assert response.status_code == status, response.text[:400]
    return response.json()


def test_real_audio_run_is_received_by_modern_instance_data_management(client):
    original, capability, release, attached, command = configured(client)
    base = '/api/workspace/instances/' + release['instance_id']
    assert release['snapshot']['data_requirements']['requirements_hash']
    task = finish_job(client, good(client.post(base + '/tasks', json=command), 201))
    assert task['status'] == 'workflow_waiting'
    output = task['workflow_runtime']['receipts'][0]['output']
    run_id = output['audio_run_id']
    assert run_id != original['id']
    assert '9点' in output['audio_transcription']['transcription']['text']
    assert '5点' in output['audio_transcription']['transcription']['text']
    assert output['audio_transcription']['cloud_calls'] == 0
    preview = good(client.get(base + '/native-receipts'))
    saved = good(client.post(base + '/native-receipts/sync', json={'preview_hash': preview['preview_hash']}))
    assert saved['cloud_calls'] == 0
    deliver_events()
    listing = good(client.get(base + '/native-receipts'))
    item = next(row for row in listing['records'] if row.get('record', {}).get('source_key') == 'AudioRun:' + run_id)
    assert item['record']['attributes']['已执行整片转写'] is True
    with Session(engine()) as db:
        row = db.get(m.Observation, item['id'])
        event = db.scalar(select(m.Event).where(m.Event.workspace_id == row.workspace_id,
            m.Event.idempotency_key == 'data:' + row.id))
        assert row.observed_at is None and not row.data.get('invocation_id') and 'extractor_id' not in row.data
        assert not list(db.scalars(select(m.CloudCall.id)))
        reason = event.data.get('delivery_error')
        assert item['data_received'] and event.status == 'received', (event.status, reason)
        assert db.scalar(select(m.Memory.id).where(m.Memory.workspace_id == row.workspace_id,
            m.Memory.data['event_id'].as_string() == event.id)) is not None


def test_diagnostic_native_authority_check_location(client, monkeypatch):
    import inspect
    from generic_agents import native_audio_provenance_v136 as origin
    original = origin.need

    def traced(value):
        if not value:
            caller = inspect.currentframe().f_back
            raise AssertionError('native authority failed at ' + caller.f_code.co_name + ':' + str(caller.f_lineno))
        return original(value)

    monkeypatch.setattr(origin, 'need', traced)
    test_real_audio_run_is_received_by_modern_instance_data_management(client)
