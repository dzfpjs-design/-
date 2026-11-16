"""Normal SourceSet commands survive PG session timezone changes, without repair.

Root runs on its disposable ga_test DB. This module never selects or starts a
database and does not migrate, resign or replace any saved source/command.
"""
import copy
from contextlib import contextmanager
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from generic_agents import models as m, runtime_task_documents as sources
from generic_agents import runtime_document_receipts as delivery
from generic_agents.db import engine, session as database_session
from generic_agents.evidence import evidence_bytes
from generic_agents.runtime_task_document_models_v207 import RuntimeTaskDocumentSourceSet as SourceSet
from generic_agents.runtime_task_document_models_v207 import RuntimeTaskDocumentSourceCommand as Command
from tests.test_business_data_gateway_pg import scene, response
from tests.test_authored_workflow_pg_v208 import adopted, good
from tests.test_instance_workspace import OBJECTS, object_row
from tests.test_runtime_documents_pg_v205 import uploaded, run, version

BASE = '/api/runtime-task-documents'


@contextmanager
def request_timezone(client, zone):
    assert zone in {'UTC', 'America/Los_Angeles'}
    def scoped_session():
        with Session(engine()) as db:
            db.execute(text("SET LOCAL TIME ZONE '" + zone + "'"))
            yield db
    missing = object(); prior = client.app.dependency_overrides.get(database_session, missing)
    client.app.dependency_overrides[database_session] = scoped_session
    try: yield
    finally:
        if prior is missing: client.app.dependency_overrides.pop(database_session, None)
        else: client.app.dependency_overrides[database_session] = prior


def prepared_task(s):
    _, preview, _, _ = adopted(s.client)
    draft = good(s.client.get('/api/setup/draft'))
    body = {'revision': draft['revision'], 'objects': OBJECTS,
        'role_bindings': {role: value['user_id'] for role, value in preview['binding']['role_bindings'].items()}}
    checked = good(s.client.post('/api/instances/check', json=body))
    release = good(s.client.post('/api/instances', json={**body,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True}), 201)
    current = next(value for value in good(s.client.get('/api/instances')) if value['id'] == release['instance_id'])
    good(s.client.post('/api/instances/' + current['id'] + '/activate', json={'version': current['version'],
        'release_id': release['id'], 'business_ids': release['available_business_ids'], 'confirm_workflow_scope': True}))
    unit = str(uuid4())
    good(s.client.post('/api/workspace/approval-settings-v175/units', json={'request_id': str(uuid4()),
        'unit_id': unit, 'expected_version': 0, 'name': '公开跨时区核对范围', 'parent_id': None, 'enabled': True}))
    stage = {'name': '明确审批', 'role_key': 'reviewer', 'scope_id': unit}
    good(s.client.post('/api/workspace/approval-settings-v175/policies', json={'request_id': str(uuid4()),
        'instance_id': release['instance_id'], 'business_id': release['available_business_ids'][0], 'expected_version': 0,
        'name': '仅核原稿保存，不启动执行', 'scope_id': unit, 'stages': [stage], 'executor': stage,
        'acceptance': stage, 'enabled': True}))
    entity = object_row(s.client, release)
    task = good(s.client.post('/api/workspace/instances/' + release['instance_id'] + '/tasks', json={
        'release_id': release['id'], 'business_id': release['available_business_ids'][0], 'entity_ids': [entity['id']],
        'request_id': str(uuid4()), 'title': '原SourceSet跨会话保存核对', 'graph_inputs': {}}), 201)
    return release, task


def frozen(s, identity):
    with Session(engine()) as db:
        db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        row = db.get(SourceSet, identity)
        command = db.scalar(select(Command).where(Command.source_set_id == row.id))
        audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace,
            m.Audit.action.in_([sources.COMMAND, sources.CREATED])).order_by(m.Audit.id)))
        return copy.deepcopy({'source': (row.id, row.created_at, row.version, row.source_set_hash, row.data),
            'command': (command.id, command.version, command.exact_body, command.body_sha256, command.data),
            'audits': [(a.id, a.actor_id, a.action, a.object_id, a.data) for a in audits]})


def test_saved_source_exact_replay_get_and_normal_revise_across_non_utc_pg_sessions(scene):
    s = scene; release, task = prepared_task(s); raw = '原始😀资料必须保持'.encode()
    document = response(uploaded(s, release, raw)[0], 201); version_id = document['latest_version_id']
    assert run(s, version_id) == 'succeeded'; delivery.consume_once(s.workspace)
    native = version(s, version_id); selector = {'kind': 'native', 'version_id': version_id,
        'attempt_id': native['latest_attempt_id'], 'parse_hash': native['parse_hash'], 'block_id': 'b1',
        'char_span': {'start': 0, 'end': 4}}
    context = good(s.client.get(BASE + '/tasks/' + task['id'] + '/context'))
    command = {'request_id': str(uuid4()), 'expected_task_version': context['task_version'],
        'expected_approval_version': context['approval_version'], 'prior_set_id': None, 'prior_set_hash': None,
        'input_key': context['input_ports'][0]['key'], 'selectors': [selector]}
    path = BASE + '/tasks/' + task['id'] + '/source-sets'
    with request_timezone(s.client, 'UTC'): saved = good(s.client.post(path, json=command), 201)
    assert saved['created_at'].endswith('+00:00')
    before = frozen(s, saved['id'])
    with Session(engine()) as db:
        db.execute(text("SET LOCAL TIME ZONE 'America/Los_Angeles'"))
        row = db.get(SourceSet, saved['id'])
        assert row.created_at.utcoffset().total_seconds() != 0
        assert sources.source_view(sources.checked_set(db, s.p, row)) == saved
        original = db.get(sources.native.Version, version_id)
        assert evidence_bytes(db, s.p, original.evidence_id)[1] == raw
    with request_timezone(s.client, 'America/Los_Angeles'):
        assert good(s.client.get(BASE + '/source-sets/' + saved['id'])) == saved
        assert good(s.client.post(path, json=command), 201) == saved
        receipt = good(s.client.get(BASE + '/requests/' + command['request_id'],
            params={'expected_hash': sources.digest(command)}))
        assert receipt['found'] and receipt['result'] == saved and receipt['body'] == command
        base = '/api/workspace/tasks/' + task['id'] + '/approval-v175'
        approval = good(s.client.get(base)); value = copy.deepcopy(approval['value'])
        value['graph_inputs'][command['input_key']] = saved['ref']
        good(s.client.post(base + '/revise', json={'request_id': str(uuid4()), 'expected_version': approval['version'],
            'reason': '明确采用已保存原片段，未更换原件、来源或原请求', 'value': value}))
        assert good(s.client.get(base))['value']['graph_inputs'][command['input_key']] == saved['ref']
    assert frozen(s, saved['id']) == before
