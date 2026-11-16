"""Real registered capabilities/native originals/ACK on Root's ga_test PostgreSQL.

These tests do not fabricate a paid graph origin or claim the complete V207
business journey. A normal human-authored graph entry is covered separately by
V208 and Root's normal-API acceptance before that journey can be accepted.
"""
import copy
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, runtime_task_documents as sources
from generic_agents import runtime_document_receipts as delivery
from generic_agents import workflow_document_packet_v207 as packet
from generic_agents.db import engine
from generic_agents.gateway import invoke
from generic_agents.services import audit
from generic_agents.workflow_abilities import eligible
from generic_agents.runtime_task_document_models_v207 import RuntimeTaskDocumentSourceSet as SourceSet
from generic_agents.runtime_task_document_models_v207 import RuntimeTaskDocumentSourceCommand as SourceCommand
from tests.test_business_data_gateway_pg import scene, response
from tests.test_runtime_documents_pg_v205 import instance, uploaded, run, version, counts as native_counts
from tests.test_runtime_document_parse_boundaries_v205 import pdf_bytes
from tests.test_instance_workspace import object_row, task_row
from tests.test_workflow_service_registration_v195 import publish
from tests.test_runtime_task_documents_v207 import manifest

BASE = '/api/runtime-task-documents'


def inventory(db, workspace):
    return tuple(db.scalar(select(func.count()).select_from(model).where(model.workspace_id == workspace))
        for model in (SourceSet, SourceCommand, packet.Packet, packet.Command, m.Invocation, m.CloudCall, m.Evidence))


def native_source(s, raw='甲😀乙业务依据'.encode(), name='report.txt', *, receive=True):
    release = instance(s); document = response(uploaded(s, release, raw, name)[0], 201)
    identity = document['latest_version_id']; run(s, identity)
    if receive: delivery.consume_once(s.workspace)
    saved = version(s, identity)
    selection = {'kind': 'native', 'version_id': identity, 'attempt_id': saved['latest_attempt_id'],
        'parse_hash': saved['parse_hash'], 'block_id': 'b1', 'char_span': {'start': 1, 'end': 3}}
    return release, document, saved, selection


def test_real_native_selection_preserves_codepoints_original_attempt_and_independent_ack(scene):
    s = scene; release, document, saved, selection = native_source(s)
    with Session(engine()) as db:
        before = inventory(db, s.workspace)
        selected = sources.resolve(db, s.p, selection, release['instance_id'])
        assert selected['text'] == '😀乙' and not selected['partial'] and selected['unreviewed']
        assert selected['selector'] == selection and selected['ack']['event_id'] == saved['delivery']['event_id']
        assert selected['source_name'] == saved['name']
        memory = db.get(m.Memory, selected['ack']['memory_id'])
        assert memory.status == 'candidate' and memory.data['event_id'] == selected['ack']['event_id']
        assert inventory(db, s.workspace) == before
        wrong = copy.deepcopy(selection); wrong['parse_hash'] = '0' * 64
        with pytest.raises(HTTPException): sources.resolve(db, s.p, wrong, release['instance_id'])
        with pytest.raises(HTTPException): sources.resolve(db, s.p, selection, str(uuid4()))
        assert inventory(db, s.workspace) == before


def test_unreceived_native_and_damaged_ack_cannot_be_used_as_task_sources(scene):
    s = scene; release, _, saved, selection = native_source(s, receive=False)
    with Session(engine()) as db:
        with pytest.raises(HTTPException): sources.resolve(db, s.p, selection, release['instance_id'])
    delivery.consume_once(s.workspace)
    with Session(engine()) as db:
        resolved = sources.resolve(db, s.p, selection, release['instance_id'])
        ack = db.get(m.Audit, resolved['ack']['ack_audit_id'])
        ack.data = {**ack.data, 'memory_id': str(uuid4())}; db.flush()
        with pytest.raises(HTTPException): sources.resolve(db, s.p, selection, release['instance_id'])
        db.rollback()
    with Session(engine()) as db:
        assert sources.resolve(db, s.p, selection, release['instance_id'])['ack']['event_id'] == saved['delivery']['event_id']


def test_partial_native_fixed_block_is_selectable_but_retains_partial_status_and_raw_integrity(scene):
    s = scene; release, _, saved, selection = native_source(s,
        pdf_bytes(['Original selected block', None]), 'partial.pdf')
    assert saved['status'] == 'needs_input' and saved['delivery']['verified']
    selection['char_span'] = {'start': 0, 'end': 8}
    with Session(engine()) as db:
        selected = sources.resolve(db, s.p, selection, release['instance_id'])
        assert selected['partial'] and selected['warnings'] and selected['text'] == 'Original'
        raw = db.get(m.Evidence, db.get(sources.native.Version, selection['version_id']).evidence_id)
        raw_id, old_hash = raw.id, raw.sha256; raw.sha256 = '0' * 64; db.flush()
        with pytest.raises(HTTPException): sources.resolve(db, s.p, selection, release['instance_id'])
        db.rollback()
    with Session(engine()) as db:
        assert db.get(m.Evidence, raw_id).sha256 == old_hash


@pytest.mark.parametrize('trace', [sources.COMMAND, sources.CREATED])
def test_lost_source_command_does_not_turn_saved_nonce_into_a_new_request(scene, trace):
    s = scene; identity = str(uuid4())
    with Session(engine()) as db:
        audit(db, s.p, trace, str(uuid4()), {'request_id': identity, 'deliberate_test_corruption': True})
        db.commit()
    before = native_counts(s)
    result = s.client.get(BASE + '/requests/' + identity, params={'expected_hash': 'a' * 64})
    assert result.status_code == 409 and native_counts(s) == before
    with Session(engine()) as db:
        assert db.scalar(select(SourceCommand.id).where(SourceCommand.workspace_id == s.workspace,
            SourceCommand.request_id == identity)) is None


def test_normal_legacy_task_without_175_or_declared_port_is_readonly_and_cannot_select(scene):
    s = scene; release, _, _, selection = native_source(s)
    task, _ = task_row(s.client, release, object_row(s.client, release))
    context = response(s.client.get(BASE + '/tasks/' + task['id'] + '/context'))
    assert context['input_ports'] == [] and not context['can_select'] and context['approval_version'] is None
    request = {'request_id': str(uuid4()), 'expected_task_version': task['version'], 'expected_approval_version': 1,
        'prior_set_id': None, 'prior_set_hash': None, 'input_key': 'runtime_document_sources', 'selectors': [selection]}
    with Session(engine()) as db: before = inventory(db, s.workspace)
    rejected = s.client.post(BASE + '/tasks/' + task['id'] + '/source-sets', json=request)
    assert rejected.status_code == 409
    assert s.client.get(BASE + '/tasks/' + str(uuid4()) + '/context').status_code == 404
    with Session(engine()) as db: assert inventory(db, s.workspace) == before


def test_real_registry_new_packet_requires_explicit_family_and_direct_gateway_has_no_authority(scene):
    s = scene; registered = publish(s.client, manifest())
    with Session(engine()) as db:
        cap = db.get(m.Capability, registered['id']); before = inventory(db, s.workspace)
        packet.validate_manifest(cap.data)
        with pytest.raises(HTTPException): eligible(cap, 'data')
        with pytest.raises(HTTPException): eligible(cap, 'data', allow_task_packet=True)
        eligible(cap, 'data', allow_document_packet=True)
        with pytest.raises(HTTPException):
            invoke(db, s.p, cap.id, 'data', {'source_set_ref': {'source_set_id': str(uuid4()),
                'source_set_hash': 'a' * 64}}, 'unapproved-document-' + uuid4().hex)
        assert inventory(db, s.workspace) == before


def test_real_registered_capability_is_required_for_graph_typed_port_not_marker_alone(scene):
    s = scene; registered = publish(s.client, manifest())
    with Session(engine()) as db:
        cap = db.get(m.Capability, registered['id'])
        graph = {'input_schema': {'type': 'object', 'properties': {'runtime_document_sources': sources.ref_schema()},
            'required': ['runtime_document_sources'], 'additionalProperties': False},
            'references': {'chosen': {'kind': 'object', 'nullable': False, 'source': 'input',
                'field': 'runtime_document_sources', 'node_id': None}}, 'nodes': [{'id': 'packet', 'kind': 'ability',
                'actor_id': s.p.user_id, 'execution': {'adapter': packet.ADAPTER, 'capability_id': cap.id,
                    'capability_hash': cap.content_hash, 'release_version': cap.release_version, 'agent_role': 'data', 'max_attempts': 1,
                    'input_schema': packet.input_schema(), 'output_schema': packet.output_schema(),
                    'input_bindings': {'source_set_ref': 'chosen'}, 'arguments': {}}}]}
        pins = sources.verify_graph_sources(db, s.p, graph)
        assert pins['runtime_document_sources'][0]['capability_id'] == cap.id
        wrong = copy.deepcopy(graph); wrong['references']['chosen']['source'] = 'output'
        with pytest.raises(HTTPException): sources.verify_graph_sources(db, s.p, wrong)
        cap.status = 'draft'; db.flush()
        with pytest.raises(HTTPException): sources.verify_graph_sources(db, s.p, graph)
        assert sources.verify_graph_sources(db, s.p, graph, current=False) == pins
        db.rollback()
