"""Independent corruption tests on a normally accepted source and real APIs.

Only the negative setup damages database rows. Source creation, review,
packet execution, acceptance and case commands use the normal fixture/API.
Root runs these on a disposable PostgreSQL database; this file never proves
browser acceptance, forestry accuracy or autonomous learning benefit.
"""
import copy
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, layered_cases_v209 as cases
from generic_agents.db import engine
from tests.test_business_data_gateway_pg import scene
from tests.test_layered_cases_pg_v209 import accepted_case_source
from tests.test_instance_workspace import OBJECTS, save


BASE = '/api/workspace/layered-cases-v209'
VALUE = {'title': '独立审查回滚保护案例', 'outcome': '本地资料包和人工结果已独立工程验收',
    'experience': '先保存原依据，再由独立人员核对所提交的固定修订',
    'applicable_scope': '公开工程资料处理流程', 'caveats': '不证明真实现场状态或模型业务准确率'}


def good(response, status=200):
    assert response.status_code == status, response.text[:1200]
    return response.json()


def source_records(source):
    """Case reads must not mutate their original execution or acceptance."""
    with Session(engine()) as db:
        task = db.get(m.Task, source.task_id)
        counts = tuple(db.scalar(select(func.count()).select_from(model).where(
            model.workspace_id == source.s.workspace))
            for model in (m.Job, m.Invocation, m.CloudCall, m.Memory))
        memories = [(row.id, row.status, copy.deepcopy(row.data)) for row in db.scalars(
            select(m.Memory).where(m.Memory.workspace_id == source.s.workspace,
                m.Memory.task_id == source.task_id).order_by(m.Memory.id))]
        return copy.deepcopy((task.version, task.status, task.data, counts, memories))


def create_case(source, value=None):
    context = good(source.author_client.get(BASE + '/tasks/' + source.task_id + '/context'))
    body = {'request_id': str(uuid4()), 'expected_source_task_version': context['task_version'],
        'source_fingerprint': context['source_fingerprint'],
        'value': copy.deepcopy(VALUE if value is None else value)}
    result = good(source.author_client.post(BASE + '/tasks/' + source.task_id + '/cases', json=body), 201)
    return result['case_id']


def change(source, case_id, action, *, reviewer=False, **extra):
    client = source.reviewer_client if reviewer else source.author_client
    current = good(client.get(BASE + '/cases/' + case_id))
    body = {'request_id': str(uuid4()), 'expected_case_version': current['version'],
        'revision_id': current['revision']['id'], 'revision_hash': current['revision']['revision_hash'],
        'reason': '独立审查固定修订及原来源', **extra}
    return good(client.post(BASE + '/cases/' + case_id + '/' + action, json=body))


def approved_case(source):
    identity = create_case(source)
    change(source, identity, 'submit')
    assert change(source, identity, 'review', reviewer=True, decision='approve')['status'] == 'approved'
    return identity


def test_surviving_later_audits_prevent_rollback_to_real_earlier_case_state(accepted_case_source):
    source = accepted_case_source
    identity = approved_case(source)
    original = source_records(source)
    with Session(engine()) as db:
        row = db.get(cases.Case, identity)
        commands = list(db.scalars(select(cases.Command).where(cases.Command.case_id == identity)))
        creation = next(item for item in commands if item.operation == 'create')
        state = copy.deepcopy(creation.data['state'])
        audit_ids = list(db.scalars(select(m.Audit.id).where(m.Audit.workspace_id == source.s.workspace,
            m.Audit.action == cases.COMMAND_AUDIT,
            m.Audit.data['case_id'].as_string() == identity).order_by(m.Audit.id)))
        assert len(audit_ids) == 3
        # This is a genuinely saved earlier state, not a fabricated new hash.
        # Keeping independent later audits must still expose the lost suffix.
        row.version, row.status = state['version'], state['status']
        row.head_revision_id = state['head_revision_id']
        cases.seal(row, {**state['data'], 'command_ids': [creation.id]})
        for item in commands:
            if item.id != creation.id:
                db.delete(item)
        db.commit()

    assert source.author_client.get(BASE + '/cases/' + identity).status_code == 409
    assert source_records(source) == original
    with Session(engine()) as db:
        assert list(db.scalars(select(m.Audit.id).where(m.Audit.workspace_id == source.s.workspace,
            m.Audit.action == cases.COMMAND_AUDIT,
            m.Audit.data['case_id'].as_string() == identity).order_by(m.Audit.id))) == audit_ids


def test_search_must_not_hide_an_approved_case_with_missing_head_behind_inner_join(accepted_case_source):
    source = accepted_case_source
    identity = approved_case(source)
    original = source_records(source)
    with Session(engine()) as db:
        row = db.get(cases.Case, identity)
        row.head_revision_id = str(uuid4())
        db.commit()
    # An empty result would erase a previously approved human contribution.
    response = source.author_client.get(BASE + '/search', params={'q': VALUE['title']})
    assert response.status_code == 409, response.text[:1200]
    assert source_records(source) == original


def test_search_rejects_head_borrowed_from_real_other_case_before_keyword_filter(accepted_case_source):
    source = accepted_case_source
    identity = approved_case(source)
    unrelated = {field: '另一份记录的独立人工内容' for field in VALUE}
    other_id = create_case(source, unrelated)
    other = good(source.author_client.get(BASE + '/cases/' + other_id))
    original = source_records(source)
    with Session(engine()) as db:
        original_head = db.get(cases.Case, identity).head_revision_id
        assert db.get(cases.Revision, original_head) is not None
        assert db.get(cases.Revision, other['revision']['id']).case_id == other_id
        db.get(cases.Case, identity).head_revision_id = other['revision']['id']
        db.commit()
    # Both genuine revisions and their independent audits remain. Filtering
    # against the borrowed nonmatching value must not erase the approved case.
    response = source.author_client.get(BASE + '/search', params={'q': VALUE['title']})
    assert response.status_code == 409, response.text[:1200]
    assert source_records(source) == original


def test_stripped_task_marker_cannot_downgrade_permanent_accepted_source_to_unsupported(accepted_case_source):
    source = accepted_case_source
    with Session(engine()) as db:
        task = db.get(m.Task, source.task_id)
        assert 'layered_approval_v175' in task.data
        task.data = {key: value for key, value in task.data.items() if key != 'layered_approval_v175'}
        db.commit()
    original = source_records(source)
    response = source.author_client.get(BASE + '/tasks/' + source.task_id + '/context')
    # A permanent Managed row may remain authoritative, or the source may be
    # explicitly corrupt. It must never become the unsupported-family UI.
    assert response.status_code in {200, 409}, response.text[:1200]
    if response.status_code == 200:
        assert response.json()['source'] is not None
    assert source_records(source) == original


def test_case_history_survives_normal_successor_release_then_old_actor_rename_and_disable(accepted_case_source):
    source = accepted_case_source
    identity = approved_case(source)
    historical = good(source.reviewer_client.get(BASE + '/cases/' + identity))
    original = source_records(source)

    # A real successor publication and activation replace the active release.
    # Changing only the display name does not reinterpret the confirmed flow.
    draft = good(source.author_client.get('/api/setup/draft'))
    proposal = copy.deepcopy(draft['configuration'])
    proposal['instanceProfile']['name'] += '后继展示版本'
    revision = save(source.author_client, proposal, review_automation=True)
    compilation = {'revision': revision, 'objects': OBJECTS,
        'instance_id': source.release['instance_id'],
        'role_bindings': {key: actor['user_id'] for key, actor in source.release['snapshot']['roles'].items()}}
    checked = good(source.author_client.post('/api/instances/check', json=compilation))
    assert not checked['general_issues']
    successor = good(source.author_client.post('/api/instances', json={**compilation,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True,
        'generation_intent': 'new_release', 'request_id': str(uuid4())}), 201)
    assert successor['id'] != source.release['id']
    instance = next(item for item in good(source.author_client.get('/api/instances'))
        if item['id'] == source.release['instance_id'])
    activated = good(source.author_client.post('/api/instances/' + instance['id'] + '/activate', json={
        'version': instance['version'], 'release_id': successor['id'],
        'business_ids': successor['available_business_ids'], 'confirm_workflow_scope': True}))
    assert activated['active_release_id'] == successor['id']

    # Current user administration must not become a retroactive source gate.
    # The application currently has no account-edit API; this negative setup
    # changes only the old account row, never its Task/receipt/acceptance proof.
    old_actor = historical['author']['id']
    with Session(engine()) as db:
        actor = db.get(m.User, old_actor)
        actor.username = 'old_case_author_' + uuid4().hex[:16]
        actor.active = False
        db.commit()

    after = good(source.reviewer_client.get(BASE + '/cases/' + identity))
    assert after['status'] == historical['status'] == 'approved'
    assert after['source'] == historical['source']
    for key in ('id', 'ordinal', 'revision_hash', 'parent_revision_id', 'parent_revision_hash',
            'value', 'source_fingerprint', 'created_at', 'reason'):
        assert after['revision'][key] == historical['revision'][key]
    assert after['revision']['created_by']['id'] == old_actor
    context = good(source.reviewer_client.get(BASE + '/tasks/' + source.task_id + '/context'))
    assert context['source_fingerprint'] == historical['source']['source_fingerprint']
    assert source_records(source) == original
