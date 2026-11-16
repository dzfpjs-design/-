"""Root-run synthetic PG schema workflow; author never executes these tests.

Uses the existing v112 real authority/persistence path with synthetic document,
OCR and provider IO. It does not certify model quality or real-document meaning.
"""
import copy
import json
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, SetupDraft, SetupReportDraft, SetupRevision, Task
from generic_agents.report_full_semantic_schema_models import FullSemanticSchemaAdoption as Adoption
from generic_agents import report_full_semantic_schema_adoption as schema
from tests.test_report_evidence_pipeline import reply
from tests.test_report_full_semantic_source_review_postgres import v7_scene, reading_scene, local_runtime
from tests.test_report_full_semantic_region_source_postgres import full_source_scene, source_scene2, confirm_page
from tests.test_report_full_semantic_region_mapping_postgres import started
from tests.test_report_full_semantic_handoff_postgres import PRIVATE_NOTE
from tests import test_material_ocr as ocr_test


ROOT = '/api/setup/full-semantic-schemas/'


@pytest.fixture
def schema_seed(source_scene2):
    seed = source_scene2
    for page in (1, 2): confirm_page(seed, page)
    ocr_test.confirm_document(seed['client'], seed['scene']['copy'])
    client, path, state, _, _, _, sid = started(seed)
    for ref in state['manifest']['refs']:
        found = reply(client.get(path + '/sessions/' + sid + '/candidates', params={'ref_hash': ref['ref_hash']}))
        assert found['candidates'], ref['path']
        mapping = found['candidates'][0]
        reply(client.post(path + '/sessions/' + sid + '/entries', json={
            'schema': 'report-full-semantic-region-entry-command-v1', 'request_id': str(uuid4()),
            'ref_hash': ref['ref_hash'], 'expected_version': 0, 'mapping': mapping,
            'image_acknowledgements': [segment['unit_id'] for segment in mapping['segments']],
            'privacy_acknowledged': True}))
    command = {'schema': 'report-full-semantic-region-proof-preview-command-v1', 'session_id': sid,
        'manifest_hash': state['manifest']['manifest_hash'], 'configuration_revision': state['configuration']['revision'],
        'configuration_hash': state['configuration']['hash']}
    preview = reply(client.post(path + '/proof-preview', json=command))
    saved = reply(client.post(path + '/proofs', json={**command,
        'schema': 'report-full-semantic-region-proof-command-v1', 'request_id': str(uuid4()),
        'preview_hash': preview['preview_hash'], 'consent': True}))
    proof_id = saved['receipt']['resource_id']
    assert reply(client.get(path + '/proofs/' + proof_id))['current_source_verified'] is True
    return {**seed, 'schema_path': ROOT + seed['record']['id'], 'proof_id': proof_id}


def command(seed):
    state = reply(seed['client'].get(seed['schema_path']))
    return {'schema': 'report-full-semantic-schema-preview-command-v1', 'proof_id': seed['proof_id'],
        'configuration_revision': state['configuration']['revision'], 'configuration_hash': state['configuration']['hash'],
        'selection': {'object_names': [item['name'] for item in state['objects']],
            'relationship_names': [item['name'] for item in state['relationships']]},
        'relationship_policies': {item['name']: {'cardinality': 'many' if item['cardinality'] == 'unspecified' else item['cardinality'],
            'allow_self': False} for item in state['relationships']}}


def adopted(seed):
    body = command(seed); client, path = seed['client'], seed['schema_path']
    preview = reply(client.post(path + '/preview', json=body))
    body = {**body, 'schema': 'report-full-semantic-schema-command-v1', 'request_id': str(uuid4()),
        'preview_hash': preview['preview_hash'], 'semantic_mode': 'workspace_schema_design', 'consent': True}
    return body, preview, reply(client.post(path + '/adoptions', json=body))


def test_actual_schema_adoption_saved_replayed_reopened_and_cold_origins(schema_seed):
    seed = schema_seed; client, path = seed['client'], seed['schema_path']
    with Session(engine()) as db:
        before = copy.deepcopy(db.scalar(select(SetupDraft)).data)
        private = copy.deepcopy(db.get(SetupReportDraft, seed['report']['id']).data)
        tasks, calls = db.query(Task).count(), db.query(CloudCall).count()
    body, preview, accepted = adopted(seed)
    assert accepted['current_matches'] and accepted['plan'] == preview['plan']
    assert reply(client.post(path + '/adoptions', json=body)) == accepted
    recovered = reply(client.get(path + '/requests/' + body['request_id']))
    assert recovered['found'] and recovered['receipt'] == accepted['receipt']
    assert reply(client.get(path + '/adoptions/' + accepted['id'])) == accepted
    state = reply(client.get(path))
    assert state['adoptions'][0]['receipt'] == accepted['receipt'] and state['execution_supported'] is False
    text = json.dumps(accepted, ensure_ascii=False)
    assert PRIVATE_NOTE not in text and 'synthetic@example.invalid' not in text
    with Session(engine()) as db:
        config = db.scalar(select(SetupDraft)); plan = accepted['plan']
        header = config.data['fullSemanticSchemaAdoptions'][0]
        assert set(header) == {'id', 'plan_hash', 'material_ids'} and header['id'] == accepted['id']
        assert header['plan_hash'] == plan['plan_hash']
        assert set(header['material_ids']) == {seed['scene']['original']['id'], seed['scene']['copy']['id']}
        assert config.data['flows'] == before['flows'] and config.data['fullSemanticDesigns'] == before['fullSemanticDesigns']
        assert db.get(SetupReportDraft, seed['report']['id']).data == private
        assert db.query(Task).count() == tasks and db.query(CloudCall).count() == calls and db.query(Adoption).count() == 1
        assert schema.checked_adoption(db, seed['scene']['p'], db.get(Adoption, accepted['id']), require_current=True)['plan'] == plan
        for item in plan['objects']:
            assert schema.verify_origin(db, seed['scene']['p'], config.data, item['name'], 'object') == set(header['material_ids'])
        for item in plan['relationships']:
            assert schema.verify_origin(db, seed['scene']['p'], config.data, item['name'], 'relationship') == set(header['material_ids'])
        stored = db.scalar(select(SetupRevision).where(SetupRevision.draft_id == config.id,
            SetupRevision.version == accepted['receipt']['accepted_revision']))
        assert digest(stored.data) == accepted['receipt']['configuration_hash']


def test_nonce_is_first_cas_conflicts_and_no_same_name_overwrite(schema_seed):
    seed = schema_seed; client, path = seed['client'], seed['schema_path']
    body, _, saved = adopted(seed)
    changed = copy.deepcopy(body); changed['relationship_policies'][next(iter(changed['relationship_policies']))]['allow_self'] = True
    assert client.post(path + '/adoptions', json=changed).json()['detail']['code'] == 'full_schema_nonce_conflict'
    assert reply(client.post(path + '/adoptions', json=body))['receipt'] == saved['receipt']
    stale = {**body, 'request_id': str(uuid4())}
    assert client.post(path + '/adoptions', json=stale).json()['detail']['code'] == 'full_schema_stale'
    next_body = command(seed)
    conflicts = reply(client.post(path + '/preview', json=next_body))
    assert conflicts['conflicts']
    conflict_body = {**next_body, 'schema': 'report-full-semantic-schema-command-v1', 'request_id': str(uuid4()),
        'preview_hash': conflicts['preview_hash'], 'semantic_mode': 'workspace_schema_design', 'consent': True}
    assert client.post(path + '/adoptions', json=conflict_body).json()['detail']['code'] == 'full_schema_name_conflict'
    assert not reply(client.get(path + '/requests/' + conflict_body['request_id']))['found']
    with Session(engine()) as db: assert db.query(Adoption).count() == 1


def test_existing_receipt_and_origins_do_not_expire_merely_when_configuration_revision_advances(schema_seed):
    seed = schema_seed; _, _, saved = adopted(seed)
    with Session(engine()) as db:
        config = db.scalar(select(SetupDraft)); data = copy.deepcopy(config.data)
        data['instanceProfile']['goal'] = data.get('instanceProfile', {}).get('goal', '') + '保留既有结构的无关修改'
        config.data = data; config.version += 1
        db.add(SetupRevision(workspace_id=config.workspace_id, draft_id=config.id, version=config.version, data=data)); db.commit()
    current = reply(seed['client'].get(seed['schema_path'] + '/adoptions/' + saved['id']))
    assert current['receipt'] == saved['receipt'] and not current['current_matches']
    with Session(engine()) as db:
        config = db.scalar(select(SetupDraft))
        for obj in saved['plan']['objects']:
            assert schema.verify_origin(db, seed['scene']['p'], config.data, obj['name'], 'object')


@pytest.mark.parametrize('consent', [False, 1, 'true', None])
def test_schema_consent_is_exact_boolean_and_failure_never_reserves_nonce(schema_seed, consent):
    seed = schema_seed; body = command(seed); path = seed['schema_path']; client = seed['client']
    preview = reply(client.post(path + '/preview', json=body)); nonce = str(uuid4())
    body.update(schema='report-full-semantic-schema-command-v1', request_id=nonce,
        preview_hash=preview['preview_hash'], semantic_mode='workspace_schema_design', consent=consent)
    assert client.post(path + '/adoptions', json=body).status_code == 422
    assert not reply(client.get(path + '/requests/' + nonce))['found']


def test_audit_failure_leaves_no_adoption_or_configuration_write(schema_seed, monkeypatch):
    seed = schema_seed; client, path = seed['client'], seed['schema_path']
    body = command(seed); preview = reply(client.post(path + '/preview', json=body))
    body.update(schema='report-full-semantic-schema-command-v1', request_id=str(uuid4()),
        preview_hash=preview['preview_hash'], semantic_mode='workspace_schema_design', consent=True)
    with Session(engine()) as db: before = copy.deepcopy(db.scalar(select(SetupDraft)).data)
    def broken(*args, **kwargs): raise RuntimeError('synthetic audit refusal')
    monkeypatch.setattr(schema, 'audit', broken)
    with pytest.raises(RuntimeError, match='synthetic audit refusal'):
        client.post(path + '/adoptions', json=body)
    assert not reply(client.get(path + '/requests/' + body['request_id']))['found']
    with Session(engine()) as db:
        assert db.query(Adoption).count() == 0 and db.scalar(select(SetupDraft)).data == before


def test_final_actor_revocation_after_preview_validation_prevents_all_writes(schema_seed, monkeypatch):
    from generic_agents.models import User
    seed = schema_seed; client, path = seed['client'], seed['schema_path']
    body = command(seed); preview = reply(client.post(path + '/preview', json=body))
    body.update(schema='report-full-semantic-schema-command-v1', request_id=str(uuid4()),
        preview_hash=preview['preview_hash'], semantic_mode='workspace_schema_design', consent=True)
    original = schema._plan
    with Session(engine()) as db: before = copy.deepcopy(db.scalar(select(SetupDraft)).data)
    def revoked(*args, **kwargs):
        value = original(*args, **kwargs)
        with Session(engine()) as db:
            db.get(User, seed['scene']['p'].user_id).active = False; db.commit()
        return value
    monkeypatch.setattr(schema, '_plan', revoked)
    assert client.post(path + '/adoptions', json=body).status_code in (401, 403)
    with Session(engine()) as db:
        assert db.query(Adoption).count() == 0 and db.scalar(select(SetupDraft)).data == before


def test_locked_confirmation_is_rechecked_after_successful_public_proof_read(schema_seed, monkeypatch):
    from types import SimpleNamespace
    seed = schema_seed; client, path = seed['client'], seed['schema_path']
    body = command(seed); preview = reply(client.post(path + '/preview', json=body))
    body.update(schema='report-full-semantic-schema-command-v1', request_id=str(uuid4()),
        preview_hash=preview['preview_hash'], semantic_mode='workspace_schema_design', consent=True)
    original = schema.positions.current_source
    def changed_confirmation(*args, **kwargs):
        value = original(*args, **kwargs)
        if kwargs.get('lock'):
            return (*value[:4], SimpleNamespace(id=str(uuid4())))
        return value
    monkeypatch.setattr(schema.positions, 'current_source', changed_confirmation)
    response = client.post(path + '/adoptions', json=body)
    assert response.json()['detail']['code'] == 'full_schema_source_stale'
    assert not reply(client.get(path + '/requests/' + body['request_id']))['found']
    with Session(engine()) as db: assert db.query(Adoption).count() == 0


def test_checked_row_and_origin_reject_resealed_plan_header_or_accepted_revision_forgery(schema_seed):
    seed = schema_seed; _, _, accepted = adopted(seed); client, path = seed['client'], seed['schema_path']
    with Session(engine()) as db:
        row = db.get(Adoption, accepted['id']); original = copy.deepcopy(row.data)
        forged = copy.deepcopy(original); forged['plan']['objects'][0]['fields'].pop()
        forged['plan']['plan_hash'] = digest({k:v for k,v in forged['plan'].items() if k != 'plan_hash'})
        forged['ledger_hash'] = digest({k:v for k,v in forged.items() if k != 'ledger_hash'})
        row.data = forged; db.commit()
    assert client.get(path + '/adoptions/' + accepted['id']).json()['detail']['code'] == 'full_schema_integrity'
    with Session(engine()) as db:
        db.get(Adoption, accepted['id']).data = original; db.commit()
    assert reply(client.get(path + '/adoptions/' + accepted['id']))['receipt'] == accepted['receipt']
    with Session(engine()) as db:
        config = db.scalar(select(SetupDraft)); p = seed['scene']['p']; name = accepted['plan']['objects'][0]['name']
        mutations = ('origin_schema', 'origin_name', 'origin_path', 'origin_hash', 'extra', 'header_materials', 'header_hash', 'definition')
        for mutation in mutations:
            value = copy.deepcopy(config.data); origin = value['instanceObjectOrigins'][name]
            if mutation == 'origin_schema': origin['schema'] = 'legacy'
            elif mutation == 'origin_name': origin['name'] = '别的对象'
            elif mutation == 'origin_path': origin['owner_path'] = '/objects/99'
            elif mutation == 'origin_hash': origin['owner_value_hash'] = '0' * 64
            elif mutation == 'extra': origin['approved'] = True
            elif mutation == 'header_materials': value['fullSemanticSchemaAdoptions'][0]['material_ids'] = []
            elif mutation == 'header_hash': value['fullSemanticSchemaAdoptions'][0]['plan_hash'] = '0' * 64
            elif mutation == 'definition': next(item for item in value['instanceObjects'] if item['name'] == name)['fields'][0]['label'] = '改名'
            with pytest.raises(HTTPException): schema.verify_origin(db, p, value, name, 'object')


def test_source_current_flag_and_locked_formal_confirmation_are_both_required(schema_seed, monkeypatch):
    seed = schema_seed; body = command(seed); original = schema.positions.get_proof
    def not_current(*args, **kwargs):
        value = original(*args, **kwargs); return {**value, 'current_source_verified': False}
    monkeypatch.setattr(schema.positions, 'get_proof', not_current)
    response = seed['client'].post(seed['schema_path'] + '/preview', json=body)
    assert response.json()['detail']['code'] == 'full_schema_source_stale'
    with Session(engine()) as db: assert db.query(Adoption).count() == 0


def test_old_origin_material_branches_remain_usable_and_new_headers_are_bounded():
    from generic_agents.setup_object_proposals import origin_material_ids as object_ids
    from generic_agents.setup_relation_proposals import origin_material_ids as relation_ids
    config = {'instanceObjectOrigins': {'对象': {'object': {'citations': [{'material_id': 'old-a'}],
        'fields': [{'citations': [{'material_id': 'old-b'}]}]}}},
        'instanceRelationOrigins': {'关系': {'relationship': {'citations': [{'material_id': 'old-c'}]}}}}
    assert object_ids(config, ['对象']) == {'old-a', 'old-b'} and relation_ids(config, ['关系']) == {'old-c'}
    origin = {'schema': schema.ORIGIN, 'adoption_id': str(uuid4()), 'plan_hash': 'a' * 64,
        'kind': 'object', 'name': '新对象', 'owner_path': '/objects/0', 'owner_value_hash': 'b' * 64}
    config['instanceObjectOrigins']['新对象'] = origin
    config['fullSemanticSchemaAdoptions'] = [{'id': origin['adoption_id'], 'plan_hash': origin['plan_hash'],
        'material_ids': sorted([str(uuid4()), str(uuid4())])}]
    assert object_ids(config, ['新对象']) == set(config['fullSemanticSchemaAdoptions'][0]['material_ids'])
    config['fullSemanticSchemaAdoptions'][0]['unexpected'] = True
    with pytest.raises(HTTPException): object_ids(config, ['新对象'])


def test_real_draft_put_cannot_erase_all_schema_markers_and_relabel_adopted_objects_as_manual(schema_seed):
    seed = schema_seed; _, _, accepted = adopted(seed); client = seed['client']
    before = reply(client.get('/api/setup/draft'))
    downgraded = copy.deepcopy(before['configuration'])
    downgraded.pop('fullSemanticSchemaAdoptions')
    downgraded.pop('instanceObjectOrigins')
    downgraded.pop('instanceRelationOrigins')
    assert downgraded['instanceObjects'] == before['configuration']['instanceObjects']
    assert downgraded['instanceRelationTypes'] == before['configuration']['instanceRelationTypes']
    refused = client.put('/api/setup/draft', json={'revision': before['revision'], 'configuration': downgraded})
    assert refused.status_code == 409, refused.text
    unchanged = reply(client.get('/api/setup/draft'))
    assert unchanged['revision'] == before['revision'] and unchanged['configuration'] == before['configuration']
    assert reply(client.get(seed['schema_path'] + '/adoptions/' + accepted['id']))['receipt'] == accepted['receipt']


def test_real_goal_save_survives_stale_source_while_new_compilation_is_blocked(schema_seed):
    from generic_agents.models import SetupMaterial
    seed = schema_seed; _, _, accepted = adopted(seed); client = seed['client']
    before = reply(client.get('/api/setup/draft'))
    changed = copy.deepcopy(before['configuration'])
    changed['instanceProfile']['goal'] = '仅改工作目标，保留已采用的数据结构和来源'
    saved = reply(client.put('/api/setup/draft', json={'revision': before['revision'], 'configuration': changed}))
    assert saved['configuration']['instanceProfile']['goal'] == changed['instanceProfile']['goal']
    assert saved['configuration']['fullSemanticSchemaAdoptions'] == before['configuration']['fullSemanticSchemaAdoptions']
    with Session(engine()) as db:
        # Actual source state changes in the synthetic test store; no readiness
        # function or approval gate is patched to manufacture a stale result.
        material = db.get(SetupMaterial, seed['scene']['copy']['id'])
        material.archived = True; db.commit()
    stale = reply(client.get('/api/setup/draft')); changed_again = copy.deepcopy(stale['configuration'])
    changed_again['instanceProfile']['goal'] = '正式来源变化后仍可保存无关工作目标，编译必须重新核对来源'
    saved_again = reply(client.put('/api/setup/draft', json={
        'revision': stale['revision'], 'configuration': changed_again}))
    assert saved_again['configuration']['instanceProfile']['goal'] == changed_again['instanceProfile']['goal']
    assert saved_again['configuration']['instanceObjectOrigins'] == before['configuration']['instanceObjectOrigins']
    assert saved_again['configuration']['instanceRelationOrigins'] == before['configuration']['instanceRelationOrigins']
    assert reply(client.get(seed['schema_path'] + '/adoptions/' + accepted['id']))['receipt'] == accepted['receipt']
    roles = {step['role']: seed['scene']['p'].user_id
        for flow in saved_again['configuration']['flows'].values() for step in flow['steps'] if step['agent'] == '人工确认'}
    refused = client.post('/api/instances/check', json={'revision': saved_again['revision'],
        'objects': saved_again['configuration']['instanceObjects'], 'role_bindings': roles})
    assert refused.status_code == 409, refused.text
