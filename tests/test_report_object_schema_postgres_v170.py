"""Root-run new schema adoption with real authenticated PG product stores.

The complete imported source/proof journey uses public synthetic document/OCR
and provider IO. Human object/relationship proposals remain pending source
meaning. No source approval, instance compilation or execution is fabricated.
Only the mixed-proof negative case changes an existing row, in a rolled-back
transaction using the real typed ORM and normal API dependency sessions.
"""
import copy
import json
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine, session
from generic_agents.models import SetupReportDraft, SetupRevision
from generic_agents.report_configuration_models import ReportConfigurationRecord
from generic_agents.report_full_semantic_region_models import FullSemanticRegionProof as Proof
from generic_agents.report_full_semantic_schema_models import FullSemanticSchemaAdoption as Adoption
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline_v7_postgres import v7_scene
from tests.test_report_full_semantic_region_source_postgres import full_source_scene
from tests.test_report_evidence_pipeline import reply
from tests.test_report_object_adoption_postgres_v169 import (
    confirmed_scene, confirm_revision, commit, assert_paid_unchanged,
)
from tests.test_report_object_source_postgres_v170 import (
    object_source_scene, published_proof, assert_no_public_private_values,
)


ROOT = '/api/setup/full-semantic-schemas/'
_ABSENT = object()


@pytest.fixture
def object_schema_scene(object_source_scene):
    """Add a cited human relationship through normal revision/replacement APIs."""
    seed = {**object_source_scene}; scene = {**seed['scene']}
    value = copy.deepcopy(seed['value'])
    # These type names and the relationship text occur in the public rich
    # source's w:p3. Cardinality/uniqueness are deliberately not inferred.
    assert len(value['result']['objects']) == 2
    for obj, name in zip(value['result']['objects'], ('核对资料', '核对任务')):
        obj['name'] = name
    value['result']['relationships'].append({'name': '资料关联任务',
        'description': '人工采用的待核资料任务关联，尚未批准业务含义。',
        'source_type': '核对资料', 'target_type': '核对任务', 'cardinality': 'unspecified',
        'citations': [{'span_ref': 'w:p3/s1'}], 'inference_reason': ''})
    value['object_identity_confirmations'] = [{'object_name': obj['name'], 'acknowledge_platform_identity': True}
        for obj in reversed(value['result']['objects'])]
    value['note'] += 'Schema私人结构补充'
    confirmed = confirm_revision(seed['client'], seed['report_id'], value)
    target = {'handoff_id': seed['record']['id'], 'record_hash': seed['record']['record_hash'],
        'design_hash': seed['record']['design']['design_hash']}
    _, _, body, raw, accepted = commit(seed['client'], seed['report_id'], target=target)
    scene.update(value=value, confirmed=confirmed)
    seed.update(scene=scene, value=value, record=accepted['record'], adopted=accepted,
        path='/api/setup/full-semantic-source-reviews/' + accepted['record']['id'],
        adoption_command=body, adoption_wire=raw)
    proof = published_proof(seed)
    return {**seed, 'proof_result': proof, 'proof_id': proof['proof_id'],
        'schema_path': ROOT + seed['record']['id']}


def schema_command(seed):
    state = reply(seed['client'].get(seed['schema_path']))
    assert state['schema'] == 'report-object-schema-state-v170'
    assert state['execution_supported'] is False
    return {'schema': 'report-object-schema-preview-command-v170', 'proof_id': seed['proof_id'],
        'configuration_revision': state['configuration']['revision'],
        'configuration_hash': state['configuration']['hash'],
        'selection': {'object_names': [obj['name'] for obj in state['objects']],
            'relationship_names': [relation['name'] for relation in state['relationships']]},
        'relationship_policies': {relation['name']: {
            'cardinality': 'many' if relation['cardinality'] == 'unspecified' else relation['cardinality'],
            'allow_self': False} for relation in state['relationships']}}


def adopted_schema(seed):
    body = schema_command(seed); client, path = seed['client'], seed['schema_path']
    preview = reply(client.post(path + '/preview', json=body))
    assert preview['schema'] == 'report-object-schema-preview-v170'
    assert preview['conflicts'] == [] and preview['plan']['schema'] == 'report-object-schema-adoption-v170'
    command = {**body, 'schema': 'report-object-schema-command-v170', 'request_id': str(uuid4()),
        'preview_hash': preview['preview_hash'], 'semantic_mode': 'workspace_schema_design', 'consent': True}
    accepted = reply(client.post(path + '/adoptions', json=command))
    assert accepted['plan'] == preview['plan'] and accepted['receipt']['schema'] == 'report-object-schema-receipt-v170'
    assert_no_public_private_values(preview, accepted)
    return command, preview, accepted


def test_normal_new_schema_adoption_preserves_optional_identity_sources_and_cold_receipts(object_schema_scene, account):
    seed = object_schema_scene; client, path = seed['client'], seed['schema_path']
    before = reply(client.get('/api/setup/draft'))
    with Session(engine()) as db:
        original_report = copy.deepcopy(db.get(SetupReportDraft, seed['report_id']).data)
        old_records = {row.id: copy.deepcopy(row.data) for row in db.scalars(select(ReportConfigurationRecord))}
    command, preview, accepted = adopted_schema(seed)
    plan = accepted['plan']; config = reply(client.get('/api/setup/draft'))
    assert plan['selection']['object_names'] == [obj['name'] for obj in seed['value']['result']['objects']]
    assert plan['selection']['relationship_names'] == ['资料关联任务']
    assert plan['relationship_policies'] == {'资料关联任务': {'cardinality': 'many', 'allow_self': False}}
    assert config['configuration']['flows'] == before['configuration']['flows']
    assert config['configuration']['fullSemanticDesigns'] == before['configuration']['fullSemanticDesigns']
    configured = {obj['name']: obj for obj in config['configuration']['instanceObjects']}
    owners = {item['path']: item for item in seed['proof_result']['manifest']['owners']}
    for index, obj in enumerate(seed['value']['result']['objects']):
        actual = configured[obj['name']]
        assert set(actual) == {'schema', 'name', 'fields', 'business_identity', 'platform_identity_confirmation'}
        assert actual['schema'] == 'report-object-type-v170'
        assert actual['fields'] == [{key: field[key] for key in ('label', 'kind', 'required', 'unit')} for field in obj['fields']]
        assert all(field['required'] is False and field['unit'] is None for field in actual['fields'])
        assert actual['business_identity'] == obj['business_identity']
        ack_index = next(number for number, item in enumerate(seed['value']['object_identity_confirmations'])
            if item['object_name'] == obj['name'])
        assert actual['platform_identity_confirmation'] == seed['value']['object_identity_confirmations'][ack_index]
        source = plan['object_sources'][obj['name']]
        for key, owner_path in [('business_identity', f'/objects/{index}/business_identity'),
                ('platform_identity_confirmation', f'/object_identity_confirmations/{ack_index}')]:
            assert source[key]['owner_path'] == owner_path
            assert source[key]['owner_value_hash'] == owners[owner_path]['value_hash']
            assert source[key]['ref_hashes'] == owners[owner_path]['ref_hashes']
            assert source[key]['pending_reasons'] == owners[owner_path]['pending_reasons']
            assert owner_path in plan['pending_evidence_owner_paths']
        origin = config['configuration']['instanceObjectOrigins'][obj['name']]
        assert origin['schema'] == 'report-object-schema-origin-v170'
        assert origin['adoption_id'] == accepted['id'] and origin['plan_hash'] == plan['plan_hash']
    assert reply(client.post(path + '/adoptions', json=command)) == accepted
    recovered = reply(client.get(path + '/requests/' + command['request_id']))
    assert recovered['found'] and recovered['receipt'] == accepted['receipt']
    assert reply(client.get(path + '/adoptions/' + accepted['id'])) == accepted
    with Session(engine()) as db:
        assert db.get(SetupReportDraft, seed['report_id']).data == original_report
        assert {row.id: row.data for row in db.scalars(select(ReportConfigurationRecord))} == old_records
        stored = db.get(Adoption, accepted['id'])
        assert stored.data['schema'] == 'report-object-schema-adoption-ledger-v170'
        assert stored.data['plan'] == plan
        assert_no_public_private_values(stored.data)
    with TestClient(app) as cold:
        auth = reply(cold.post('/api/login', json={'username': account[0], 'password': account[1]}))
        cold.headers.update({'X-CSRF-Token': auth['csrf_token'], 'X-Workspace-ID': account[2]})
        assert reply(cold.get('/api/setup/draft')) == config
        assert reply(cold.get(path + '/adoptions/' + accepted['id']))['plan'] == plan
        assert reply(cold.get(path + '/requests/' + command['request_id']))['receipt'] == accepted['receipt']
    assert_no_public_private_values(config, plan)
    assert_paid_unchanged(seed['scene'])


def test_ordinary_configuration_save_cannot_forge_new_origin_identity_required_or_unit(object_schema_scene):
    seed = object_schema_scene; client = seed['client']
    _, _, accepted = adopted_schema(seed)
    before = reply(client.get('/api/setup/draft'))
    with Session(engine()) as db:
        revision_ids = set(db.scalars(select(SetupRevision.id)))
    for fault in ('origin_family', 'origin_hash', 'identity', 'required', 'unit'):
        configuration = copy.deepcopy(before['configuration'])
        first = configuration['instanceObjects'][0]
        origin = configuration['instanceObjectOrigins'][first['name']]
        if fault == 'origin_family': origin['schema'] = 'report-full-semantic-schema-origin-v1'
        elif fault == 'origin_hash': origin['owner_value_hash'] = '0' * 64
        elif fault == 'identity': first['business_identity']['explanation'] += '伪造后续身份说明'
        elif fault == 'required': first['fields'][0]['required'] = True
        else:
            numeric = next(obj for obj in configuration['instanceObjects'] if any(
                field['kind'] in {'数值', '整数'} for field in obj['fields']))
            next(field for field in numeric['fields'] if field['kind'] in {'数值', '整数'})['unit'] = '株'
        denied = client.put('/api/setup/draft', json={'revision': before['revision'], 'configuration': configuration})
        assert denied.status_code == 409, (fault, denied.text)
        assert reply(client.get('/api/setup/draft')) == before
        assert reply(client.get(seed['schema_path'] + '/adoptions/' + accepted['id']))['receipt'] == accepted['receipt']
        with Session(engine()) as db:
            assert set(db.scalars(select(SetupRevision.id))) == revision_ids
    assert_paid_unchanged(seed['scene'])


@contextmanager
def rolled_back_mixed_proof(seed):
    """Real typed negative write, with external rollback and no forged success."""
    fence(); previous = app.dependency_overrides.get(session, _ABSENT)
    with engine().connect() as connection:
        transaction = connection.begin()
        try:
            with Session(bind=connection, expire_on_commit=False, join_transaction_mode='create_savepoint') as db:
                stored = db.get(Proof, seed['proof_id'])
                forged = copy.deepcopy(stored.data)
                assert forged['proof']['schema'] == 'report-object-region-proof-v170'
                forged['proof']['schema'] = 'report-full-semantic-region-proof-v1'
                forged['proof']['proof_hash'] = digest({key: item for key, item in forged['proof'].items() if key != 'proof_hash'})
                forged['ledger_hash'] = digest({key: item for key, item in forged.items() if key != 'ledger_hash'})
                stored.data = forged
                db.commit()  # Releases only this savepoint; outer transaction remains uncommitted.
            def bound_session():
                with Session(bind=connection, expire_on_commit=False, join_transaction_mode='create_savepoint') as db:
                    yield db
            app.dependency_overrides[session] = bound_session
            yield
        finally:
            if previous is _ABSENT: app.dependency_overrides.pop(session, None)
            else: app.dependency_overrides[session] = previous
            transaction.rollback()


def test_old_commands_and_mixed_proof_family_cannot_adopt_new_objects(object_schema_scene):
    seed = object_schema_scene; client, path = seed['client'], seed['schema_path']
    body = schema_command(seed); before = reply(client.get('/api/setup/draft'))
    preview = reply(client.post(path + '/preview', json=body))
    legacy_preview = {**body, 'schema': 'report-full-semantic-schema-preview-command-v1'}
    assert client.post(path + '/preview', json=legacy_preview).status_code in (409, 422)
    legacy_adopt = {**body, 'schema': 'report-full-semantic-schema-command-v1', 'request_id': str(uuid4()),
        'preview_hash': preview['preview_hash'], 'semantic_mode': 'workspace_schema_design', 'consent': True}
    assert client.post(path + '/adoptions', json=legacy_adopt).status_code in (409, 422)
    assert reply(client.get(path + '/requests/' + legacy_adopt['request_id']))['found'] is False
    with rolled_back_mixed_proof(seed):
        denied = client.post(path + '/preview', json=body)
        assert denied.status_code == 409
    restored = reply(client.get(seed['proof_result']['path'] + '/proofs/' + seed['proof_id']))
    assert restored['proof'] == seed['proof_result']['proof'] and restored['current_source_verified'] is True
    assert reply(client.get('/api/setup/draft')) == before
    with Session(engine()) as db:
        assert list(db.scalars(select(Adoption))) == []
    assert_paid_unchanged(seed['scene'])
