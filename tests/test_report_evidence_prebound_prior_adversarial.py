"""Synthetic two-stage transaction adversarial tests; independent PG only."""
import copy
import json
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from generic_agents.db import engine
from generic_agents.models import Capability, CloudCall, Job, SetupReportDraft
from generic_agents.leases import attach
from generic_agents import report_evidence_prebound_transport as transport
from generic_agents import report_evidence_pipeline as evidence
from generic_agents import report_evidence_delegated_wire_v1 as wire
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents import report_evidence_journal as journal
from generic_agents import cloud_scoped_extra_reservation as scoped
from tests.test_cloud_report_plan_allowance_postgres import own_database, stage_fixture, reserve_first
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_full_reading_source import full_source_scene


@pytest.mark.parametrize('mode', ['capability', 'version', 'expire', 'detach'])
def test_prior_call_is_frozen_at_intent(reading_scene, monkeypatch, tmp_path, mode):
    monkeypatch.setattr(wire, 'root', lambda: tmp_path / 'wire')
    monkeypatch.setattr(evidence, '_journal_root', lambda *args: tmp_path / 'journal')
    fixture = stage_fixture(reading_scene, monkeypatch, deepseek=True)
    p, rid, jid, gid, cap_id, first, lease = fixture
    with Session(engine(), expire_on_commit=False) as db:
        attach(db, jid, lease); db.info['worker_actor'] = p
        first_id = reserve_first(db, fixture); db.commit()
    with Session(engine(), expire_on_commit=False) as db:
        attach(db, jid, lease); db.info['worker_actor'] = p
        row = evidence.owned(db, p, rid, True)
        ctx = wire.context(evidence, row, first['stage_id'])
        value = {'schema': 'report-evidence-map-v3', 'claims': []}
        response = {'choices': [{'message': {'content': json.dumps(value)}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}}
        sink = capture.prepare(wire.root(), ctx)
        with sink:
            sink.mark_request_started(); sink.begin_response(200, '', 'synthetic')
            sink.append(json.dumps(response).encode()); sink.finish(eof=True)
        content, receipt, _ = wire.response(evidence, row, first['stage_id'])
        binding = evidence.journal_binding(row, first['stage_id'], first_id)
        descriptor = bridge.write_missing_journal(journal, evidence._stage_journal_root(row, first['stage_id']), binding, content, receipt)
        st = evidence._stage(row, first['stage_id'])
        evidence._replace_stage(row, first['stage_id'], {**st, 'journal': descriptor})
        evidence._settle(db, p, row, first['stage_id'], receipt, True)
        evidence._consume(db, p, row, first['stage_id'])
    with Session(engine(), expire_on_commit=False) as db:
        row = db.get(SetupReportDraft, rid)
        second = row.data['maps'][1]['chunk_id']; st = evidence._stage(row, second)
        evidence._replace_stage(row, second, {**st, 'status': 'running', 'inflight': True}); db.commit()
    with Session(engine()) as db:
        old = db.get(Capability, cap_id)
        other = Capability(workspace_id=p.workspace_id, namespace=old.namespace,
            name='synthetic-adversarial-' + uuid4().hex, release_version=old.release_version,
            kind=old.kind, status=old.status, content_hash=old.content_hash,
            validated_hash=old.validated_hash, data=copy.deepcopy(old.data))
        db.add(other); db.flush(); other_id = other.id; db.commit()
    with Session(engine(), expire_on_commit=False) as db:
        attach(db, jid, lease); db.info['worker_actor'] = p
        grant = db.get(Job, gid); entry = grant.data['manifest']['stage_plan'][1]
        permit = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
            Job.idempotency_key == scoped.permit_key(jid, second)))
        second_id = scoped.reserve_scoped_tx(db, p, permit.id, cap_id, entry['token_bound'], entry['request_hash'], entry['options_hash'])
        db.commit()
    with Session(engine(), expire_on_commit=False) as db:
        row = db.get(SetupReportDraft, rid)
        messages = evidence._request(row, second)['messages']
        options = copy.deepcopy(evidence._spec(row, second)['dispatch_options'])
    with Session(engine(), expire_on_commit=False) as db:
        attach(db, jid, lease); db.info['worker_actor'] = p
        sink = transport.prepare_prebound_capture(db, p, second, second_id, messages, options)
        try:
            def mutate(session):
                prior = session.get(CloudCall, first_id)
                if mode == 'capability': prior.capability_id = other_id
                elif mode == 'version': prior.version += 1
                elif mode == 'expire': session.expire(prior)
                else: session.expunge(prior)
            event.listen(db, 'before_commit', mutate, insert=True)
            with pytest.raises(transport.PreboundTransportDenied):
                transport.commit_transport_intent(db, p, second, second_id, messages, options, sink)
        finally:
            sink.close()
    with Session(engine()) as db:
        assert db.get(CloudCall, first_id).capability_id == cap_id
        dispatch = db.scalar(select(Job).where(Job.workspace_id == p.workspace_id,
            Job.idempotency_key == scoped.dispatch_key(jid, second)))
        assert dispatch.data['state'] == 'reserved'
