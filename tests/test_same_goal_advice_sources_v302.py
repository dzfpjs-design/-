"""Read-only source/consent boundaries with explicit SQL and origin IO spies.

The original V207 source/producer authentication and evidence file read are
declared IO boundaries. Actual reference contracts, scoped current-reader SQL,
byte/hash checks, V199 independent grant selection and V197 cloud parts run.
This is not real PG, storage, approval, worker or cloud verification.
"""
import copy
import hashlib
from contextlib import contextmanager
from types import SimpleNamespace as NS
from uuid import UUID

import pytest
from fastapi import HTTPException

from generic_agents import same_goal_advice_sources_v302 as s
from generic_agents import evidence, business_reasoning_v197 as reasoning
from generic_agents.contracts import digest
from tests.test_goal_evaluation_append_command_v297 import predicate


def uid(number): return str(UUID(int=number))


@pytest.fixture
def scene(monkeypatch):
    p = NS(workspace_id=uid(1), user_id=uid(2), role='operator')
    task = s.Task(id=uid(3), workspace_id=p.workspace_id, owner_id=uid(4), package_id=uid(5),
        data={'instance_id': uid(6), 'instance_release_id': uid(7)})
    entries = [{'source_name': '合成原批准资料', 'version_sequence': 1, 'location_label': '原文块1',
        'partial': True, 'warnings': ['固定所选摘录，未业务核对'], 'text': '甲😀乙：只读资料。'}]
    data = {'entries': entries, 'origin': {'explicit_original_source_IO': True}}
    source = s.documents.SourceSet(id=uid(8), workspace_id=p.workspace_id, task_id=task.id,
        instance_id=task.data['instance_id'], release_id=task.data['instance_release_id'], actor_id=task.owner_id,
        input_key='runtime_document_sources', version=1, source_set_hash=digest(data), data=data)
    selected = {'submission_id': uid(9), 'submission_hash': 'a' * 64, 'source_set_id': source.id,
        'source_set_hash': source.source_set_hash, 'input_key': source.input_key}
    raw = s.documents.render(entries)
    file = s.Evidence(id=uid(10), workspace_id=p.workspace_id, version=1, media_type='text/plain',
        sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw), data={'cloud_model_ids': []})
    packet = s.packets.Packet(id=uid(11), workspace_id=p.workspace_id, task_id=task.id, source_set_id=source.id,
        version=1, actor_id=uid(12), evidence_id=file.id, sha256=file.sha256, invocation_id=uid(13), job_id=uid(14), data={})
    proof = {'packet_id': packet.id, 'evidence_id': file.id, 'sha256': file.sha256, 'task_id': task.id,
        'producer_job_id': packet.job_id, 'invocation_id': packet.invocation_id,
        'source_set_id': source.id, 'source_set_hash': source.source_set_hash, 'producer_hash': 'b' * 64}
    model_data = {'execution': {'provider': 'qwen', 'task': 'chat'}, 'data_policy': {'cloud_allowed': True}}
    model = s.Capability(id=uid(15), workspace_id=p.workspace_id, kind='foundation_model', status='published',
        release_version='1.0.0', content_hash=digest(model_data), validated_hash=digest(model_data), data=model_data)
    state = NS(p=p, task=task, source=source, selected=selected, file=file, packet=packet, proof=proof, model=model,
        active=True, member_role=p.role, origin_valid=True, producer_valid=True, reads=[], source_calls=[], packet_calls=[],
        contents={file.id: raw}, rows={s.Task: [task], s.documents.SourceSet: [source], s.packets.Packet: [packet],
            s.Capability: [model], s.Evidence: [file], s.Audit: []})
    class DB:
        protected = 0
        @property
        def no_autoflush(self):
            @contextmanager
            def protect():
                self.protected += 1
                try: yield
                finally: self.protected -= 1
            return protect()
        def scalars(self, query):
            assert self.protected and query._for_update_arg is None
            model = query.column_descriptions[0]['entity']; state.reads.append(model)
            assert p.workspace_id in query.compile().params.values()
            return [row for row in state.rows.get(model, []) if predicate(query.whereclause, row)]
        def scalar(self, query):
            assert self.protected and query._for_update_arg is None
            expression = query.column_descriptions[0]['expr']
            if expression is s.User.id:
                identity = NS(id=p.user_id, user_id=p.user_id, workspace_id=p.workspace_id,
                    active=state.active, role=state.member_role)
                assert 'memberships' in str(query) and 'users' in str(query)
                return p.user_id if predicate(query.whereclause, identity) else None
            rows = self.scalars(query); assert len(rows) <= 1
            return rows[0] if rows else None
        def add(self, *args): pytest.fail('Read-only sources tried to add durable state')
        def execute(self, *args): pytest.fail('Read-only sources tried an unaccounted SQL effect')
        def flush(self): pytest.fail('Read-only sources tried to flush')
        def commit(self): pytest.fail('Read-only sources tried to commit')
    state.db = DB()
    def approved(db, principal, actual_task, reference):
        assert db is state.db and principal is p and actual_task is task
        state.source_calls.append(copy.deepcopy(reference))
        s.need(state.origin_valid and reference == {'source_set_id': source.id, 'source_set_hash': source.source_set_hash})
        return source, copy.deepcopy(selected)
    def checked(db, principal, actual):
        assert db is state.db and principal is p and actual is packet
        state.packet_calls.append(actual.id); s.need(state.producer_valid)
        return copy.deepcopy(proof)
    def read(db, principal, identity):
        assert db is state.db and principal is p
        rows = [item for item in state.rows[s.Evidence] if item.id == identity and item.workspace_id == p.workspace_id]
        s.need(len(rows) == 1); return rows[0], state.contents[identity]
    monkeypatch.setattr(s.packets, 'approved_set', approved)  # Original approval/native/OCR/ACK IO only.
    monkeypatch.setattr(s.packets, 'checked_packet', checked)  # Original SDK/Job/dispatch/PRODUCED IO only.
    monkeypatch.setattr(evidence, 'evidence_bytes', read)  # Immutable storage read, not byte acceptance.
    return state


def source_ref(scene):
    return {'kind': 'approved_source_set', 'source_set_ref': {'source_set_id': scene.source.id,
        'source_set_hash': scene.source.source_set_hash}}


def packet_ref(scene):
    return {'kind': 'document_packet_v207', 'packet': {'id': scene.packet.id, 'version': 1,
        'evidence_id': scene.file.id, 'sha256': scene.file.sha256,
        'source_set_id': scene.source.id, 'source_set_hash': scene.source.source_set_hash}}


def grant(scene, *, model=None, version=None):
    model = model or scene.model
    item = s.Audit(id=uid(20 + len(scene.rows[s.Audit])), workspace_id=scene.p.workspace_id, actor_id=scene.p.user_id,
        action='evidence.cloud_authorize', object_id=scene.file.id,
        data={'model_id': model.id, 'reason': '合成文本逐模型同意', 'sha256': scene.file.sha256,
            'version': version if version is not None else scene.file.version})
    scene.rows[s.Audit].append(item)
    scene.file.data = {**scene.file.data, 'cloud_model_ids': [model.id]}
    return item


def test_real_reference_render_and_approved_metadata_are_detached_without_a_fake_packet_or_evidence(scene):
    x = scene; reference = source_ref(x)
    resolved = s.resolve_documents(x.db, x.p, x.task, reference)
    assert resolved['selection'] == reference and resolved['approved_selection'] == x.selected
    assert resolved['content'] == x.contents[x.file.id] and resolved['sha256'] == x.file.sha256
    assert resolved['size_bytes'] == len(resolved['content']) and resolved['packet_receipt'] is None
    assert resolved['evidence_ref'] is None and x.packet_calls == [] and x.source_calls == [reference['source_set_ref']]
    resolved['entries'][0]['text'] = 'mutated DTO'; resolved['approved_selection']['input_key'] = 'mutated'
    resolved['selection']['source_set_ref']['source_set_id'] = uid(99)
    again = s.resolve_documents(x.db, x.p, x.task, reference)
    assert again['entries'] == x.source.data['entries'] and again['approved_selection'] == x.selected


def test_packet_path_requires_original_producer_and_exact_original_bytes_and_filters_receipt_metadata(scene):
    x = scene; x.proof['not_a_transport_pin'] = {'secret': 'must never leave the IO proof'}
    result = s.resolve_documents(x.db, x.p, x.task, packet_ref(x))
    assert x.packet_calls == [x.packet.id] and len(x.source_calls) == 1
    assert result['evidence_ref'] == {'evidence_id': x.file.id, 'sha256': x.file.sha256,
        'size_bytes': x.file.size_bytes, 'media_type': 'text/plain'}
    assert 'not_a_transport_pin' not in result['packet_receipt']
    assert result['packet_receipt']['producer_job_id'] == x.packet.job_id
    assert result['content'] == x.contents[x.file.id]


@pytest.mark.parametrize('fault', ['missing_approval', 'cross_task_source', 'cross_task_packet', 'producer_missing',
    'wrong_ref_hash', 'render_drift', 'bytes_hash', 'mimetype', 'proof_task', 'proof_producer'])
def test_document_sources_cannot_be_adopted_by_self_hash_or_foreign_or_incomplete_origin(scene, fault):
    x = scene; reference = packet_ref(x)
    if fault == 'missing_approval': x.origin_valid = False
    elif fault == 'cross_task_source': x.source.task_id = uid(99)
    elif fault == 'cross_task_packet': x.packet.task_id = uid(99)
    elif fault == 'producer_missing': x.producer_valid = False
    elif fault == 'wrong_ref_hash': reference['packet']['source_set_hash'] = 'f' * 64
    elif fault == 'render_drift':
        x.contents[x.file.id] = b'Unapproved replacement'; x.file.sha256 = hashlib.sha256(x.contents[x.file.id]).hexdigest()
        x.file.size_bytes = len(x.contents[x.file.id]); x.packet.sha256 = x.file.sha256
        reference['packet']['sha256'] = x.file.sha256; x.proof['sha256'] = x.file.sha256
    elif fault == 'bytes_hash': x.contents[x.file.id] += b'corrupt'
    elif fault == 'mimetype': x.file.media_type = 'image/png'
    elif fault == 'proof_task': x.proof['task_id'] = uid(99)
    else: x.proof['producer_job_id'] = uid(99)
    with pytest.raises(HTTPException): s.resolve_documents(x.db, x.p, x.task, reference)


@pytest.mark.parametrize('reference', [None, {}, {'kind': 'latest'},
    {'kind': 'approved_source_set', 'source_set_ref': {'source_set_id': uid(8), 'source_set_hash': 'a' * 64}, 'text': 'client text'}])
def test_no_empty_packet_or_unregistered_document_source_is_invented(scene, reference):
    with pytest.raises(HTTPException): s.resolve_documents(scene.db, scene.p, scene.task, reference)
    assert not scene.source_calls and not scene.packet_calls


@pytest.mark.parametrize('fault', ['inactive', 'member_changed', 'task_other_workspace'])
def test_current_reader_and_task_scope_are_rechecked_on_each_read(scene, fault):
    x = scene
    assert s.resolve_documents(x.db, x.p, x.task, source_ref(x))['task_id'] == x.task.id
    if fault == 'inactive': x.active = False
    elif fault == 'member_changed': x.member_role = 'viewer'
    else: x.task.workspace_id = uid(99)
    with pytest.raises(HTTPException): s.resolve_documents(x.db, x.p, x.task, source_ref(x))


def test_cloud_text_has_real_byte_pins_original_parts_and_independent_grants_without_a_send(scene):
    x = scene; event = grant(x)
    result = s.cloud_documents(x.db, x.p, [x.file.id], x.model)
    assert result['evidence_pins'] == [{'evidence_id': x.file.id, 'sha256': x.file.sha256,
        'version': 1, 'size_bytes': x.file.size_bytes, 'media_type': 'text/plain', 'model_profile_id': x.model.id}]
    assert result['consent_pins'] == [{'evidence_id': x.file.id, 'model_profile_id': x.model.id,
        'audit_id': event.id, 'audit_hash': s.consent._grant_hash(event)}]
    with x.db.no_autoflush:
        assert result['parts'] == reasoning._evidence(x.db, x.p, [x.file.id], x.model)[1]
    assert result['documents'][0]['content'] == x.contents[x.file.id]
    assert result['model_pin'] == {'id': x.model.id, 'content_hash': x.model.content_hash, 'release_version': '1.0.0'}
    result['evidence_pins'][0]['sha256'] = 'f' * 64
    assert s.cloud_documents(x.db, x.p, [x.file.id], x.model)['evidence_pins'][0]['sha256'] == x.file.sha256


def test_repeated_real_grants_pin_one_deterministic_receipt_without_erasing_authorization_history(scene):
    x=scene;first=grant(x);second=grant(x)
    before=copy.deepcopy([{'id':event.id,'data':event.data} for event in x.rows[s.Audit]])
    result=s.cloud_documents(x.db,x.p,[x.file.id],x.model)
    selected=min([first,second],key=lambda event:event.id)
    assert len(result['evidence_pins'])==len(result['consent_pins'])==1
    assert result['consent_pins'][0]['audit_id']==selected.id
    assert result['consent_pins'][0]['audit_hash']==s.consent._grant_hash(selected)
    assert [{'id':event.id,'data':event.data} for event in x.rows[s.Audit]]==before
    with x.db.no_autoflush:
        assert len(s.consent._grants(x.db,x.file,x.model))==2


def test_three_cloud_documents_keep_explicit_order_and_each_original_byte_and_consent(scene):
    x = scene; grant(x); identifiers = [x.file.id]
    for number, raw in ((40, '另一个原文😀'.encode('utf-8')), (41, b'\xef\xbb\xbfOriginal text with BOM')):
        file = s.Evidence(id=uid(number), workspace_id=x.p.workspace_id, version=2, media_type='text/plain',
            sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw), data={'cloud_model_ids': [x.model.id]})
        x.rows[s.Evidence].append(file); x.contents[file.id] = raw; identifiers.append(file.id)
        x.rows[s.Audit].append(s.Audit(id=uid(number + 10), workspace_id=x.p.workspace_id,
            actor_id=x.p.user_id, action='evidence.cloud_authorize', object_id=file.id,
            data={'model_id': x.model.id, 'sha256': file.sha256, 'version': 2, 'reason': '合成独立授权'}))
    identifiers.reverse()
    result = s.cloud_documents(x.db, x.p, identifiers, x.model)
    assert [item['evidence_ref']['evidence_id'] for item in result['documents']] == identifiers
    assert [item['evidence_id'] for item in result['evidence_pins']] == identifiers
    assert [item['evidence_id'] for item in result['consent_pins']] == identifiers
    assert [item['content'] for item in result['documents']] == [x.contents[key] for key in identifiers]
    assert result['parts'][1] == {'type': 'text', 'text': 'Original text with BOM'}


@pytest.mark.parametrize('fault', ['no_audit', 'no_list', 'other_model_audit', 'wrong_grant_sha', 'future_grant_version',
    'bad_bytes', 'bad_size', 'invalid_text', 'binary', 'model_unpublished', 'model_hash_drift',
    'model_cloud_denied', 'model_other_workspace', 'evidence_other_workspace', 'inactive_reader'])
def test_current_text_cloud_adoption_rejects_missing_consent_or_bytes_or_model_authority(scene, fault):
    x = scene; event = grant(x)
    if fault == 'no_audit': x.rows[s.Audit].clear()
    elif fault == 'no_list': x.file.data = {**x.file.data, 'cloud_model_ids': []}
    elif fault == 'other_model_audit': event.data['model_id'] = uid(99)
    elif fault == 'wrong_grant_sha': event.data['sha256'] = 'f' * 64
    elif fault == 'future_grant_version': event.data['version'] = x.file.version + 1
    elif fault == 'bad_bytes': x.contents[x.file.id] += b'corrupt'
    elif fault == 'bad_size': x.file.size_bytes += 1
    elif fault == 'invalid_text':
        x.contents[x.file.id] = b'\xff'; x.file.sha256 = hashlib.sha256(b'\xff').hexdigest(); x.file.size_bytes = 1
        event.data['sha256'] = x.file.sha256
    elif fault == 'binary': x.file.media_type = 'image/png'
    elif fault == 'model_unpublished': x.model.status = 'draft'
    elif fault == 'model_hash_drift': x.model.content_hash = 'f' * 64
    elif fault == 'model_cloud_denied':
        x.model.data['data_policy']['cloud_allowed'] = False
        x.model.content_hash = x.model.validated_hash = digest(x.model.data)
    elif fault == 'model_other_workspace': x.model.workspace_id = uid(99)
    elif fault == 'evidence_other_workspace': x.file.workspace_id = uid(99)
    else: x.active = False
    with pytest.raises(HTTPException): s.cloud_documents(x.db, x.p, [x.file.id], x.model)


def test_later_real_consent_version_does_not_erase_original_source_but_never_restores_a_removed_current_grant(scene):
    x = scene; grant(x)
    first = s.cloud_documents(x.db, x.p, [x.file.id], x.model)
    x.file.version = 3  # Later normal metadata/consent changes retain the immutable original bytes.
    assert s.resolve_documents(x.db, x.p, x.task, packet_ref(x))['content'] == x.contents[x.file.id]
    assert s.cloud_documents(x.db, x.p, [x.file.id], x.model)['evidence_pins'][0]['version'] == 3
    assert first['evidence_pins'][0]['version'] == 1
    x.file.data = {**x.file.data, 'cloud_model_ids': [uid(99)]}
    with pytest.raises(HTTPException): s.cloud_documents(x.db, x.p, [x.file.id], x.model)
    assert s.resolve_documents(x.db, x.p, x.task, packet_ref(x))['content'] == x.contents[x.file.id]


@pytest.mark.parametrize('ids', [[], [uid(10)] * 2, [uid(10), uid(11), uid(12), uid(13)], ['not-a-uuid'], (uid(10),)])
def test_cloud_document_set_is_bounded_unique_and_canonical(scene, ids):
    with pytest.raises(HTTPException): s.cloud_documents(scene.db, scene.p, ids, scene.model)
