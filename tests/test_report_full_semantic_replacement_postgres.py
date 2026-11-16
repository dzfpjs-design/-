"""Root-run synthetic PG replacement: immutable versions, real auth/CAS/rollback.

Only provider HTTP is synthetic. Human additions are explicitly unconfirmed;
no source fact, execution permission, physical restart or model quality is proved.
"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Barrier
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall, Membership, SetupDraft, SetupRevision, User, Workspace
from generic_agents.report_configuration_models import ReportConfigurationRecord
from generic_agents.security import password_hash
from generic_agents import report_full_semantic_replacement as replacement_api
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline import reply
from tests.test_report_evidence_pipeline_v3 import all_rows, fees
from tests.test_report_evidence_pipeline_v5 import call_ids
from tests.test_report_evidence_pipeline_v7_postgres import (
    BASE, NativeHTTP, v7_scene, mapped, reduced, storage_files, call_job_rows,
)
from tests.test_report_full_semantic_handoff_postgres import human_value, PRIVATE_NOTE, DESIGN
from tests.test_report_full_semantic_handoff_security_postgres import authenticated, PUBLIC_PASSWORD


REPLACE = 'report-full-semantic-replace-command-v1'
VERSION = '/api/setup/full-semantic-design-versions/'
WHOLE = 'whole_result_excluding_private_note_v1'
OUTSIDE = '合成不参与替换的业务'
RENAMED = '人工明确改名后的待核业务'
ADDED = '人工本次新增待核业务'
FULL = '人工新增有环待核业务'
PARTIAL = '人工新增不完整待核业务'


def rows():
    return {name: {identity: json.loads(raw) for identity, raw in values.items()}
        for name, values in all_rows().items()}


def evidence_snapshot(http, draft):
    return call_job_rows(), fees(call_ids(draft)), storage_files(), len(http.requests)


def assert_evidence(http, draft, expected, *, jobs=True):
    current = evidence_snapshot(http, draft)
    assert current[1:] == expected[1:]
    if jobs:
        assert current[0] == expected[0]
    else:
        # Normal login owns real limiter/session writes; no CloudCall exemption.
        assert current[0][CloudCall.__tablename__] == expected[0][CloudCall.__tablename__]


def save_manual(client, draft, value):
    saved = reply(client.put(BASE + '/' + draft['id'] + '/review', json={
        'revision': draft['revision'], 'request_id': str(uuid4()), 'value': value}))
    assert saved['current_matches'] is True and saved['draft']['review_value'] == value
    assert saved['draft']['model_result'] == draft['model_result']
    assert saved['draft']['model_result_hash'] == draft['model_result_hash']
    return saved


def command(manual, config, target=None):
    draft = manual['draft']
    body = {'schema': REPLACE if target else 'report-full-semantic-handoff-command-v1',
        'request_id': str(uuid4()), 'report_revision': draft['revision'],
        'review_receipt_hash': manual['receipt']['receipt_hash'],
        'manual_value_hash': digest(draft['review_value']), 'model_result_hash': draft['model_result_hash'],
        'source_hash': draft['preview']['source_hash'], 'catalog_hash': draft['review_value']['result']['catalog_hash'],
        'context_hash': digest(draft['preview']['map_preparation']['context']),
        'configuration_revision': config['revision'], 'configuration_hash': digest(config['configuration']),
        'share_scope': WHOLE}
    if target:
        body.update(target_handoff_id=target['id'], target_record_hash=target['record_hash'],
            target_design_hash=target['design']['design_hash'])
    return body


def initial(scene, http):
    client = scene['client']
    _, _, _, middle = mapped(scene, http)
    _, _, _, generated = reduced(scene, http, middle)
    manual = save_manual(client, generated, human_value(generated['model_result']))
    config = reply(client.get('/api/setup/draft'))
    body = command(manual, config)
    preview = reply(client.post(BASE + '/' + generated['id'] + '/full-configuration-preview', json=body))
    submit = {**body, 'preview_hash': preview['preview_hash'], 'consent': True}
    accepted = reply(client.post(BASE + '/' + generated['id'] + '/full-configuration-handoff', json=submit))
    return generated, manual, accepted, submit


def edited(value, marker):
    """Change every feasible family, using only clearly HUMAN unknown additions.

    The unchanged synthetic source does not prove an object relationship, so
    its original empty relationship array must remain empty, not be fabricated.
    """
    out = copy.deepcopy(value); result = out['result']
    result['domain'] += '；人工修订' + marker
    result['global_claims'][-1]['statement'] += '；人工修订' + marker
    result['objects'][-1]['fields'][0]['label'] += marker
    business = next(item for item in result['businesses'] if item['name'] == FULL)
    business['trigger'] += marker; business['output'] += marker
    business['steps'][0]['purpose'] += marker
    business['steps'][1]['output'] += marker
    business['participants'][0]['responsibility'] += marker
    business['interactions'][0]['purpose'] += marker
    business['conditions'][0]['statement'] += marker
    business['coordination'][0]['wait_rule'] += marker
    result['questions'].append('人工修订仍待核对：' + marker)
    out['note'] += '私人修订' + marker
    assert result['relationships'] == value['result']['relationships'] == []
    return out


def prepared(client, manual, config, target):
    body = command(manual, config, target)
    before = rows()
    preview = reply(client.post(BASE + '/' + manual['draft']['id'] + '/full-configuration-replacement-preview', json=body))
    assert rows() == before
    assert preview['schema'] == 'report-full-semantic-replacement-preview-v1'
    assert preview['body'] == body and preview['conflicts'] == []
    assert preview['previous'] == {'handoff_id': target['id'], 'record_hash': target['record_hash'], 'design': target['design']}
    assert preview['source_status'] == 'unconfirmed' and preview['can_confirm'] is preview['can_publish'] is False
    assert preview['design']['result'] == manual['draft']['review_value']['result']
    assert PRIVATE_NOTE not in json.dumps(preview, ensure_ascii=False)
    return preview, {**body, 'preview_hash': preview['preview_hash'], 'consent': True}


def one_commit(before, after, record_id):
    assert {table for table in before if before[table] != after[table]} == {
        SetupDraft.__tablename__, SetupRevision.__tablename__, Audit.__tablename__, ReportConfigurationRecord.__tablename__}
    for model in (SetupRevision, Audit, ReportConfigurationRecord):
        old, new = before[model.__tablename__], after[model.__tablename__]
        assert len(new) == len(old) + 1
        assert all(new[key] == value for key, value in old.items())
    assert set(after[ReportConfigurationRecord.__tablename__]) - set(before[ReportConfigurationRecord.__tablename__]) == {record_id}
    assert len(before[SetupDraft.__tablename__]) == len(after[SetupDraft.__tablename__])


def accept(client, identity, preview, body):
    before = rows()
    accepted = reply(client.post(BASE + '/' + identity + '/full-configuration-replacement', json=body))
    assert accepted['schema'] == 'report-full-semantic-replacement-reply-v1'
    assert accepted['is_active'] is accepted['current_matches'] is True
    record, receipt = accepted['record'], accepted['receipt']
    assert record['schema'] == 'report-full-semantic-replacement-v1'
    assert receipt['schema'] == 'report-full-semantic-replacement-receipt-v1'
    assert record['design'] == preview['design'] and record['materials'] == preview['materials']
    assert record['changes'] == preview['changes'] and record['reset_settings'] == preview['reset_settings']
    assert receipt['accepted_revision'] == body['configuration_revision'] + 1
    for key in ('target_handoff_id', 'target_record_hash', 'target_design_hash'):
        assert record[key] == receipt[key] == body[key]
    assert record['id'] == receipt['handoff_id'] == preview['design']['handoff_id']
    assert record['record_hash'] == receipt['record_hash']
    after = rows()
    one_commit(before, after, record['id'])
    old_records, new_records = before[ReportConfigurationRecord.__tablename__], after[ReportConfigurationRecord.__tablename__]
    assert set(new_records) - set(old_records) == {record['id']}
    assert all(new_records[key] == old for key, old in old_records.items())
    ledger = new_records[record['id']]
    assert ledger['kind'] == 'full_replace' and ledger['parent_id'] == body['target_handoff_id']
    assert ledger['data']['command'] == body and ledger['data']['record'] == record and ledger['data']['receipt'] == receipt
    assert PRIVATE_NOTE not in json.dumps(accepted, ensure_ascii=False)
    return accepted


def assert_projection(preview, value):
    design = preview['design']
    assert design['result'] == value['result'] and design['manual_value_hash'] == digest(value)
    for bi, (binding, business) in enumerate(zip(design['business_bindings'], value['result']['businesses'], strict=True)):
        assert binding['candidate_index'] == bi and binding['name'] == business['name']
        for kind, collection in (('step', 'steps'), ('participant', 'participants'),
                ('interaction', 'interactions'), ('condition', 'conditions'), ('coordination', 'coordination')):
            assert binding[kind + '_ids'] == [str(uuid5(UUID(design['handoff_id']), f'{DESIGN}/{kind}/{bi}/{i}'))
                for i in range(len(business[collection]))]
        flow, ids = preview['flows'][business['name']], binding['step_ids']
        assert flow['confirmedAt'] is flow['confirmedSignature'] is None
        for i, step in enumerate(business['steps']):
            got = flow['steps'][i]
            assert got['id'] == ids[i]
            assert all(got[key] == step[key] for key in ('name', 'purpose', 'agent', 'role', 'output'))
            assert got['condition'] == step['branch_condition']
            assert got['nextId'] == ('end' if step['next_index'] is None else ids[step['next_index']])
            assert got['elseId'] == ('end' if step['else_index'] is None else ids[step['else_index']])


def test_full_replacement_versions_lossless_same_names_then_explicit_rename_remove_and_cold_read(v7_scene, monkeypatch, account):
    fence(); scene, http = v7_scene, NativeHTTP(monkeypatch)
    client = scene['client']
    generated, manual1, first, first_submit = initial(scene, http)
    identity, original = generated['id'], copy.deepcopy(first['record'])
    evidence = evidence_snapshot(http, generated)
    original_row = rows()[ReportConfigurationRecord.__tablename__][original['id']]

    # Normal configuration API saves unrelated work and explicit unreviewed
    # settings. Empty operational slots are not a registered capability grant.
    cfg = copy.deepcopy(first['configuration']['configuration'])
    cfg.setdefault('instanceProfile', {}).update(name='合成版本替换实例', domain='合成协议核对')
    cfg['businesses'].append(OUTSIDE)
    cfg['flows'][OUTSIDE] = {'trigger': '人工发起', 'output': '无关业务原样保留', 'steps': [],
        'confirmedAt': None, 'confirmedSignature': None}
    cfg['flows'][FULL]['steps'][0].update(execution={}, planning_tools=[])
    resource_id = str(uuid4())
    cfg['resources'].append({'id': resource_id, 'name': '合成关联资源',
        'businesses': [FULL, PARTIAL, OUTSIDE, ADDED], 'status': 'candidate', 'checks': {}, 'lastError': None})
    cfg.setdefault('setupNotes', {}).update({name: '人工关联说明：' + name for name in (FULL, PARTIAL, OUTSIDE, ADDED)})
    cfg.setdefault('gaps', []).extend({'business': name, 'reason': '人工待核缺口'} for name in (FULL, PARTIAL, OUTSIDE, ADDED))
    cfg.setdefault('businessAliases', {}).update({'保留业务旧称': FULL, FULL: '人工旧别称', '无关旧称': OUTSIDE})
    configured = reply(client.put('/api/setup/draft', json={'revision': first['configuration']['revision'], 'configuration': cfg}))
    cold_config = reply(client.get('/api/setup/draft'))
    assert cold_config == configured
    configured = cold_config  # Policy iteration order is the actual stored JSONB order.
    cfg = configured['configuration']
    untouched = {key: copy.deepcopy(cfg[key]) for key in ('materials', 'connections', 'instanceProfile')}
    outside = copy.deepcopy(cfg['flows'][OUTSIDE])
    outside_policy = copy.deepcopy(cfg['automation']['policies'][OUTSIDE])

    value2 = edited(manual1['draft']['review_value'], '第二版')
    manual2 = save_manual(client, manual1['draft'], value2)
    # A relationship without positive source evidence remains invalid; neither
    # replacement nor a manual label supplies missing original citations.
    invalid = copy.deepcopy(value2)
    invalid['result']['relationships'].append({'name': '人工未证关系', 'description': '人工未证关系',
        'source_type': invalid['result']['objects'][-1]['name'], 'target_type': invalid['result']['objects'][-1]['name'],
        'cardinality': 'unspecified', 'citations': [], 'inference_reason': '人工猜测不能替代原文关系'})
    before = rows()
    bad = client.put(BASE + '/' + identity + '/review', json={
        'revision': manual2['draft']['revision'], 'request_id': str(uuid4()), 'value': invalid})
    assert bad.status_code == 422 and rows() == before
    preview2, submit2 = prepared(client, manual2, configured, original)
    assert_projection(preview2, value2)
    names2 = [b['name'] for b in value2['result']['businesses']]
    assert preview2['changes'] == {'added_businesses': [], 'removed_businesses': [],
        'retained_businesses': names2, 'changed_sections': ['domain', 'global_claims', 'objects', 'businesses', 'questions']}
    settings2 = preview2['reset_settings']
    assert settings2 == {'step_tools': 1, 'automation_businesses': [n for n in cfg['automation']['policies'] if n in names2],
        'removed_resource_links': 0, 'removed_notes': 0, 'removed_gaps': 0, 'aliases_removed': 2}
    second = accept(client, identity, preview2, submit2)
    config2 = reply(client.get('/api/setup/draft'))['configuration']
    assert config2 == second['configuration']['configuration']
    assert config2['fullSemanticDesigns'] == [second['record']['design']]
    for name in names2:
        assert config2['flows'][name] == preview2['flows'][name]
        assert config2['automation']['policies'][name]['flow_review']['status'] == 'needs_review'
    for key in ('resources', 'setupNotes', 'gaps'):
        assert config2[key] == cfg[key]
    assert config2['businessAliases'] == {name: target for name, target in cfg['businessAliases'].items()
        if name not in names2 and target not in names2}
    assert config2['flows'][OUTSIDE] == outside and config2['automation']['policies'][OUTSIDE] == outside_policy
    assert all(config2[key] == value for key, value in untouched.items())
    assert reply(client.get(BASE + '/' + identity)) == manual2['draft']

    # Rename is deliberately remove+add. The original partial business is also
    # removed, while a new partial business carries no invented steps or roles.
    value3 = edited(value2, '第三版')
    businesses = value3['result']['businesses']
    renamed = next(b for b in businesses if b['name'] == FULL); renamed['name'] = RENAMED
    partial = copy.deepcopy(next(b for b in businesses if b['name'] == PARTIAL)); partial['name'] = ADDED
    value3['result']['businesses'] = [b for b in businesses if b['name'] != PARTIAL] + [partial]
    manual3 = save_manual(client, manual2['draft'], value3)
    preview3, submit3 = prepared(client, manual3, second['configuration'], second['record'])
    assert_projection(preview3, value3)
    assert preview3['changes']['added_businesses'] == [RENAMED, ADDED]
    assert preview3['changes']['removed_businesses'] == [FULL, PARTIAL]
    assert preview3['changes']['retained_businesses'] == [n for n in names2 if n not in (FULL, PARTIAL)]
    assert preview3['reset_settings'] == {'step_tools': 0,
        'automation_businesses': [n for n in config2['automation']['policies'] if n in set(names2) | {RENAMED, ADDED}],
        'removed_resource_links': 3, 'removed_notes': 3, 'removed_gaps': 3, 'aliases_removed': 0}
    third = accept(client, identity, preview3, submit3)
    config3, newest = third['configuration']['configuration'], third['record']
    assert config3['fullSemanticDesigns'] == [newest['design']]
    new_names = [b['name'] for b in value3['result']['businesses']]
    names_before = [item if isinstance(item, str) else item['name'] for item in cfg['businesses']]
    actual_names = [item if isinstance(item, str) else item['name'] for item in config3['businesses']]
    assert [n for n in actual_names if n in new_names] == new_names
    assert [n for n in actual_names if n not in new_names] == [n for n in names_before if n not in names2]
    assert set(config3['flows']) == (set(cfg['flows']) - set(names2)) | set(new_names)
    assert all(config3['flows'][name] == flow for name, flow in cfg['flows'].items() if name not in names2)
    assert config3['flows'][OUTSIDE] == outside and config3['automation']['policies'][OUTSIDE] == outside_policy
    assert all(config3['flows'][name] == flow for name, flow in preview3['flows'].items())
    assert next(r for r in config3['resources'] if r['id'] == resource_id)['businesses'] == [OUTSIDE]
    assert config3['setupNotes'] == {name: note for name, note in cfg['setupNotes'].items()
        if name not in (FULL, PARTIAL, ADDED)}
    assert config3['gaps'] == [g for g in cfg['gaps'] if g.get('business') not in (FULL, PARTIAL, ADDED)]
    assert all(config3[key] == value for key, value in untouched.items())
    assert FULL not in config3['automation']['policies'] and PARTIAL not in config3['automation']['policies']
    assert all(config3['automation']['policies'][name]['flow_review']['status'] == 'needs_review' for name in preview3['flows'])
    assert rows()[ReportConfigurationRecord.__tablename__][original['id']] == original_row
    assert_evidence(http, generated, evidence)

    version_views = {}
    checkpoint = rows()
    chain = (original, second['record'], newest)
    expected_history = [{key: r[key] for key in ('record_hash', 'accepted_revision', 'actor_id')} | {
        'handoff_id': r['id'], 'design_hash': r['design']['design_hash'], 'report_revision': r['design']['report_revision']} for r in chain]
    for record in chain:
        view = reply(client.get(VERSION + record['id'])); version_views[record['id']] = view
        assert view['schema'] == 'report-full-semantic-version-view-v1' and view['record'] == record
        assert view['active_handoff_id'] == newest['id'] and view['history'] == expected_history
        assert view['is_active'] is view['can_replace'] is (record['id'] == newest['id'])
        assert view['source_status'] == 'unconfirmed' and view['execution_supported'] is False
        assert view['view_hash'] == digest({k: v for k, v in view.items() if k != 'view_hash'})
        assert PRIVATE_NOTE not in json.dumps(view, ensure_ascii=False)
    assert reply(client.get('/api/setup/full-semantic-handoffs/' + original['id']))['record'] == original
    replay = reply(client.post(BASE + '/' + identity + '/full-configuration-replacement', json=submit2))
    assert replay['record'] == second['record'] and replay['receipt'] == second['receipt']
    assert replay['is_active'] is replay['current_matches'] is False and replay['configuration'] == third['configuration']
    assert reply(client.get(BASE + '/' + identity + '/full-configuration-replacement-receipts/' + submit2['request_id'])) == replay
    old_replay = reply(client.post(BASE + '/' + identity + '/full-configuration-handoff', json=first_submit))
    assert old_replay['record'] == original and old_replay['receipt'] == first['receipt'] and old_replay['current_matches'] is False
    historical = command(manual3, third['configuration'], original)
    rejected = client.post(BASE + '/' + identity + '/full-configuration-replacement-preview', json=historical)
    assert rejected.status_code == 409 and rejected.json()['detail']['code'] == 'full_semantic_target_changed'
    changed_nonce = {**submit2, 'target_handoff_id': newest['id']}
    assert client.post(BASE + '/' + identity + '/full-configuration-replacement', json=changed_nonce).status_code == 409
    for ancestor in (first, second):
        attempted = copy.deepcopy(ancestor['configuration']['configuration'])
        response = client.put('/api/setup/draft', json={'revision': third['configuration']['revision'], 'configuration': attempted})
        assert response.status_code in (409, 422)
        response = client.post('/api/setup/draft/restore/preview', json={
            'revision': third['configuration']['revision'], 'target_revision': ancestor['receipt']['accepted_revision']})
        assert response.status_code in (409, 422)
    assert rows() == checkpoint
    assert reply(client.get(BASE + '/' + identity)) == manual3['draft']
    checked = reply(client.post('/api/instances/check', json={
        'revision': third['configuration']['revision'], 'objects': [], 'role_bindings': {}}))
    assert checked['semantic_plan']['designs'] == [newest['design']]
    for business in checked['businesses']:
        if business['name'] in preview3['flows']:
            assert business['available'] is False and business['execution_mode'] == 'blocked'
    assert rows() == checkpoint
    assert_evidence(http, generated, evidence)
    # Fresh authenticated GETs are a cold session check, not a physical restart.
    with authenticated(client.app, account, scene['p'].workspace_id, 'admin') as fresh:
        cold = rows(); after_login = evidence_snapshot(http, generated)
        assert reply(fresh.get(BASE + '/' + identity)) == manual3['draft']
        assert reply(fresh.get('/api/setup/draft')) == third['configuration']
        for record in chain:
            assert reply(fresh.get(VERSION + record['id'])) == version_views[record['id']]
        assert reply(fresh.get(BASE + '/' + identity + '/full-configuration-replacement-receipts/' + submit2['request_id'])) == replay
        assert rows() == cold
        assert_evidence(http, generated, after_login)
    assert_evidence(http, generated, evidence, jobs=False)


def test_full_replacement_real_auth_cas_nonce_races_and_late_audit_rollback(v7_scene, monkeypatch, account):
    fence(); scene, http = v7_scene, NativeHTTP(monkeypatch)
    owner = scene['client']
    generated, manual1, first, _ = initial(scene, http)
    identity = generated['id']; base = BASE + '/' + identity
    evidence = evidence_snapshot(http, generated)
    manual2 = save_manual(owner, manual1['draft'], edited(manual1['draft']['review_value'], '并发版'))
    preview, body = prepared(owner, manual2, first['configuration'], first['record'])
    url, preview_url = base + '/full-configuration-replacement', base + '/full-configuration-replacement-preview'
    receipts = base + '/full-configuration-replacement-receipts/'
    snapshot = rows()
    assert owner.get(receipts + str(uuid4())).status_code == 404 and rows() == snapshot
    for field in ('review_receipt_hash', 'manual_value_hash', 'model_result_hash', 'source_hash', 'catalog_hash', 'context_hash',
            'target_record_hash', 'target_design_hash', 'report_revision', 'configuration_revision'):
        forged = copy.deepcopy(body); forged['request_id'] = str(uuid4())
        forged[field] = forged[field] + 1 if field.endswith('revision') else '0' * 64
        response = owner.post(preview_url, json={k: v for k, v in forged.items() if k not in ('preview_hash', 'consent')})
        assert response.status_code == 409 and rows() == snapshot, (field, response.text)
    unknown_target = command(manual2, first['configuration'], first['record'])
    unknown_target['target_handoff_id'] = str(uuid4())
    response = owner.post(preview_url, json=unknown_target)
    assert response.status_code == 409 and response.json()['detail']['code'] == 'full_semantic_target_changed'
    assert rows() == snapshot
    for consent in (1, 'true', False, None):
        assert owner.post(url, json={**body, 'consent': consent}).status_code == 422
        assert rows() == snapshot
    assert_evidence(http, generated, evidence)

    # Separate ordinary sessions authenticate real synthetic ORM memberships.
    peer_name, viewer_name = 'replace_peer_' + uuid4().hex, 'replace_viewer_' + uuid4().hex
    with Session(engine()) as db:
        peer = User(username=peer_name, password_hash=password_hash(PUBLIC_PASSWORD))
        viewer = User(username=viewer_name, password_hash=password_hash(PUBLIC_PASSWORD))
        workspace = Workspace(name='合成版本替换隔离工作区 ' + uuid4().hex)
        db.add_all([peer, viewer, workspace]); db.flush(); other_workspace = workspace.id
        db.add_all([Membership(user_id=peer.id, workspace_id=scene['p'].workspace_id, role='operator'),
            Membership(user_id=viewer.id, workspace_id=scene['p'].workspace_id, role='viewer'),
            Membership(user_id=scene['p'].user_id, workspace_id=other_workspace, role='operator')])
        db.commit()
    with ExitStack() as clients:
        peer = clients.enter_context(authenticated(owner.app, (peer_name, PUBLIC_PASSWORD), scene['p'].workspace_id, 'operator'))
        viewer = clients.enter_context(authenticated(owner.app, (viewer_name, PUBLIC_PASSWORD), scene['p'].workspace_id, 'viewer'))
        other_scope = clients.enter_context(authenticated(owner.app, account, other_workspace, 'operator'))
        sessions = [clients.enter_context(authenticated(owner.app, account, scene['p'].workspace_id, 'admin')) for _ in range(2)]
        assert sessions[0].cookies.get('ga_session') != sessions[1].cookies.get('ga_session')
        snapshot = rows(); authenticated_evidence = evidence_snapshot(http, generated)
        shared = reply(peer.get(VERSION + first['record']['id']))
        assert shared['record'] == first['record'] and shared['can_replace'] is False
        assert reply(viewer.get(VERSION + first['record']['id']))['can_replace'] is False
        assert other_scope.get(VERSION + first['record']['id']).status_code == 404
        for client in (peer, other_scope):
            assert client.post(preview_url, json={k: v for k, v in body.items() if k not in ('preview_hash', 'consent')}).status_code == 404
            assert client.post(url, json=body).status_code == 404
            assert client.get(receipts + body['request_id']).status_code == 404
        assert viewer.post(url, json=body).status_code == 403
        assert rows() == snapshot

        # Failure is injected after config/ledger/revision mutation but before
        # commit. Fresh independent Sessions must see no partial transaction.
        def audit_failure(*args, **kwargs):
            raise RuntimeError('synthetic_late_replacement_audit_failure')
        with monkeypatch.context() as fault:
            fault.setattr(replacement_api, 'audit', audit_failure)
            with pytest.raises(RuntimeError, match='synthetic_late_replacement_audit_failure'):
                sessions[0].post(url, json=body)
        assert rows() == snapshot
        assert sessions[1].get(receipts + body['request_id']).status_code == 404
        assert reply(sessions[1].get('/api/setup/draft')) == first['configuration']
        assert reply(sessions[1].get(BASE + '/' + identity)) == manual2['draft']
        assert_evidence(http, generated, authenticated_evidence)

        def race(commands):
            barrier = Barrier(2)
            def send(client, payload):
                barrier.wait(timeout=10)
                return client.post(url, json=payload)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(send, client, payload) for client, payload in zip(sessions, commands, strict=True)]
                return [future.result(timeout=30) for future in futures]

        # Same original current target/CAS, independently previewed nonces.
        alternate = {**command(manual2, first['configuration'], first['record'])}
        alternate_preview = reply(owner.post(preview_url, json=alternate))
        alternate = {**alternate, 'preview_hash': alternate_preview['preview_hash'], 'consent': True}
        before_race = rows()
        responses = race((body, alternate))
        assert sorted(r.status_code for r in responses) == [200, 409]
        winner_index = next(i for i, r in enumerate(responses) if r.status_code == 200)
        winner, winner_body = reply(responses[winner_index]), (body, alternate)[winner_index]
        loser = responses[1 - winner_index]
        assert loser.json()['detail']['code'] == 'configuration_conflict'
        after_race = rows(); table = ReportConfigurationRecord.__tablename__
        one_commit(before_race, after_race, winner['record']['id'])
        assert set(after_race[table]) - set(before_race[table]) == {winner['record']['id']}
        assert all(after_race[table][key] == value for key, value in before_race[table].items())
        assert winner['receipt']['accepted_revision'] == first['configuration']['revision'] + 1
        assert winner['record']['design']['result'] == manual2['draft']['review_value']['result']
        assert owner.get(receipts + (body, alternate)[1 - winner_index]['request_id']).status_code == 404
        assert peer.get(receipts + winner_body['request_id']).status_code == 404
        snapshot = rows()
        assert reply(sessions[1 - winner_index].post(url, json=winner_body))['receipt'] == winner['receipt']
        assert rows() == snapshot

        # A second genuinely changed saved review tests same-nonce serialization
        # without constructing a fake successful response or touching the model.
        manual3 = save_manual(owner, manual2['draft'], edited(manual2['draft']['review_value'], '同编号版'))
        third_preview, same = prepared(owner, manual3, winner['configuration'], winner['record'])
        before_same = rows()
        replies = [reply(response) for response in race((same, copy.deepcopy(same)))]
        assert replies[0]['record'] == replies[1]['record'] and replies[0]['receipt'] == replies[1]['receipt']
        assert replies[0]['is_active'] is replies[1]['is_active'] is True
        latest = replies[0]; after_same = rows()
        one_commit(before_same, after_same, latest['record']['id'])
        assert set(after_same[table]) - set(before_same[table]) == {latest['record']['id']}
        assert all(after_same[table][key] == value for key, value in before_same[table].items())
        assert latest['receipt']['accepted_revision'] == winner['configuration']['revision'] + 1
        assert latest['record']['design'] == third_preview['design']
        replay_snapshot = rows()
        old_reply = reply(owner.post(url, json=winner_body))
        assert old_reply['record'] == winner['record'] and old_reply['receipt'] == winner['receipt']
        assert old_reply['is_active'] is old_reply['current_matches'] is False
        assert old_reply['configuration'] == latest['configuration']
        changed = {**same, 'manual_value_hash': '0' * 64}
        response = owner.post(url, json=changed)
        assert response.status_code == 409 and response.json()['detail']['code'] == 'full_semantic_nonce_conflict'
        historical = command(manual3, latest['configuration'], first['record'])
        response = owner.post(preview_url, json=historical)
        assert response.status_code == 409 and response.json()['detail']['code'] == 'full_semantic_target_changed'
        assert rows() == replay_snapshot
        assert reply(owner.get(BASE + '/' + identity)) == manual3['draft']
        assert_evidence(http, generated, authenticated_evidence)
        assert PRIVATE_NOTE not in json.dumps(latest, ensure_ascii=False)
    assert_evidence(http, generated, evidence, jobs=False)
