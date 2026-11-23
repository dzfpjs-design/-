"""Synthetic image graph: real local reservation, never the user's report."""
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents import gateway, goal_queries, native_goals
from generic_agents import goal_budget_snapshot_candidate_v2 as candidate
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import AuthSession, CloudCall, Invocation, Job, Task
from generic_agents.security import Principal
from generic_agents.worker import process_once
from generic_agents.workflow_graph_goal_guard_v133 import KEY, pending_steps
from tests.test_authored_image_acceptance_pg_v243 import actor_client, approval, command, queued_job, task
from tests.test_authored_image_workflow_pg_v243 import BASE, good, exact, prepare_tools, configured, body
from tests.test_instance_workspace import OBJECTS, object_row
from tests.test_workflow_images import upload as upload_image
from tests.test_same_goal_advice_manual_append_pg import reject_non_loopback_network


def test_actual_local_graph_reservation_and_invocation(client, monkeypatch, reject_non_loopback_network):
    # All evidence and accounts are created by the fixture; no real report or
    # cloud service is read or called. Keep the real graph decorator/guards.
    storage = settings().storage_dir
    assert storage.name.startswith('ga-test-evidence-') and storage.is_dir() and not storage.is_symlink()
    monkeypatch.setattr(settings(), 'storage_dir', storage.resolve(strict=True))
    with ExitStack() as stack:
        me = good(client.get('/api/me')); workspace_id, owner = me['workspace_id'], me['user_id']
        operators = [actor_client(stack, workspace_id) for _ in range(3)]
        acceptor, _ = actor_client(stack, workspace_id)
        prepare_tools(client); configured(client)
        selected = body(client)
        for role, (identity, _) in zip(sorted(selected['role_bindings']), operators):
            selected['role_bindings'][role] = identity
        preview = good(client.post(BASE+'/preview', content=exact(selected)))
        good(client.post(BASE+'/adopt', content=exact({**selected,
            'schema':'authored-image-workflow-adopt-command-v243','request_id':str(uuid4()),
            'preview_hash':preview['preview_hash'],'consent':True})),201)
        current = good(client.get('/api/setup/draft'))
        compilation = {'revision':current['revision'],'objects':OBJECTS,
            'role_bindings':{role:actor['user_id'] for role,actor in preview['binding']['role_bindings'].items()}}
        checked = good(client.post('/api/instances/check', json=compilation)); assert not checked['general_issues']
        release = good(client.post('/api/instances', json={**compilation,
            'confirmed_configuration_hash':checked['configuration_hash'],'confirm_configuration':True}),201)
        instance = next(row for row in good(client.get('/api/instances')) if row['id'] == release['instance_id'])
        good(client.post('/api/instances/'+instance['id']+'/activate',json={'version':instance['version'],
            'release_id':release['id'],'business_ids':release['available_business_ids'],'confirm_workflow_scope':True}))
        bid = release['available_business_ids'][0]; unit = str(uuid4()); root = '/api/workspace'
        good(client.post(root+'/approval-settings-v175/units',json={'request_id':str(uuid4()),
            'unit_id':unit,'expected_version':0,'name':'合成图像核查组织','parent_id':None,'enabled':True}))
        grants = [(identity,'approval_'+str(index)) for index,(identity,_) in enumerate(operators)]
        grants += [(identity,'execute') for identity in [owner,*[identity for identity,_ in operators]]]
        grants += [(acceptor,'acceptance')]
        for identity,role in grants:
            good(client.post(root+'/approval-settings-v175/roles',json={'request_id':str(uuid4()),
                'grant_id':str(uuid4()),'expected_version':0,'unit_id':unit,'user_id':identity,'role_key':role,'enabled':True}))
        good(client.post(root+'/approval-settings-v175/policies',json={'request_id':str(uuid4()),
            'instance_id':instance['id'],'business_id':bid,'expected_version':0,'name':'合成独立审核',
            'scope_id':unit,'stages':[{'name':'审批'+str(index+1),'role_key':'approval_'+str(index),'scope_id':unit} for index in range(3)],
            'executor':{'name':'执行人','role_key':'execute','scope_id':unit},
            'acceptance':{'name':'验收人','role_key':'acceptance','scope_id':unit},'enabled':True}))
        entity = object_row(client,release); image = good(upload_image(client,release),201)
        created = good(client.post(root+'/instances/'+instance['id']+'/tasks',json={
            'release_id':release['id'],'business_id':bid,'entity_ids':[entity['id']],
            'image_ids':[image['id']],'title':'合成图像预算核验','request_id':str(uuid4())}),201)
        task_id = created['id']; value = copy.deepcopy(approval(client,task_id)['value'])
        field = next(iter(entity['attributes']))
        value['goal_spec'] = {'purpose':'核对合成图像与对象档案，记录本地工具处理结果。',
            'entity_ids':[entity['id']],'required_fields':[field],
            'subgoals':[{'id':'check','description':'核对原始对象及图像','criterion_ids':['evidence','human']}],
            'criteria':[{'id':'evidence','kind':'evidence_sufficient','description':'原始资料齐全'},
                {'id':'human','kind':'human_confirmation','description':'人工核查'}],
            'evidence':{'source':'archive','minimum_verification':'recorded',
                'time':{'mode':'archive','timezone':'Asia/Shanghai'}},
            'observation_mode':'existing_only','max_steps':12,'max_tool_calls':12,'stop_on_conflict':True}
        command(client,task_id,'revise',value=value)
        command(client,task_id,'submit')
        for _,operator in operators:
            pending = approval(operator,task_id)
            command(operator,task_id,'decide',decision='approve',submission_hash=pending['submission_hash'])
        command(client,task_id,'start')
        job_id = queued_job(task(client,task_id),'quality')
        with Session(engine()) as db:
            auth = db.scalar(select(AuthSession).where(AuthSession.user_id == owner,
                AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp()))
            assert auth is not None
            principal = Principal(owner, workspace_id, me['role'], auth.id)
            before = set(db.scalars(select(Invocation.id).where(Invocation.workspace_id == workspace_id)))
            assert not list(db.scalars(select(CloudCall.id).where(CloudCall.workspace_id == workspace_id)))
        entered = threading.Event(); release_gate = threading.Event(); original = gateway.invoke
        def hold(db,p,capability_id,role,inputs,key,**kwargs):
            entered.set(); assert release_gate.wait(90), 'reservation test gate timed out'
            return original(db,p,capability_id,role,inputs,key,**kwargs)
        monkeypatch.setattr(gateway,'invoke',hold)
        def compare(*, check_pending=False):
            with Session(engine()) as db:
                row = db.get(Task,task_id)
                expected = {'calls':goal_queries.call_count(db,principal,row),
                    'steps':native_goals.total_steps(db,principal,row),'queued_v2':0}
                waiting = pending_steps(row)
            with Session(engine()) as db:
                db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
                row = db.get(Task,task_id)
                assert candidate.read_snapshot(db,principal,row) == expected
                if check_pending:
                    row.title = '保留的未保存标题'
                    assert candidate.read_snapshot(db,principal,row) == expected
                    entry = next(iter(row.data[KEY]['items'].values()))
                    original_attempt = entry['attempt']; entry['attempt'] += 1
                    assert not row._sa_instance_state.attrs.data.history.has_changes()
                    with pytest.raises(HTTPException): candidate.read_snapshot(db,principal,row)
                    assert entry['attempt'] == original_attempt + 1 and row.title == '保留的未保存标题'
                    db.rollback()
            return expected,waiting
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(process_once,job_id=job_id,kinds=['execute_full_semantic_graph_node'],
                deliver=False,workspace_id=workspace_id)
            try:
                assert entered.wait(90), 'real worker did not reach its saved reservation'
                with Session(engine()) as db:
                    assert set(db.scalars(select(Invocation.id).where(Invocation.workspace_id == workspace_id))) == before
                reserved,waiting = compare(check_pending=True); assert reserved['calls'] == 1 and waiting == 1
                print('REAL_LOCAL_GRAPH_RESERVATION_MATCH',flush=True)
            finally:
                release_gate.set()
            assert future.result(timeout=90)
        with Session(engine()) as db:
            ids = set(db.scalars(select(Invocation.id).where(Invocation.workspace_id == workspace_id))) - before
            assert len(ids) == 1 and db.get(Invocation, ids.pop()).status == 'succeeded'
            assert db.get(Job, job_id).status == 'succeeded'
            assert not list(db.scalars(select(CloudCall.id).where(CloudCall.workspace_id == workspace_id)))
        finished,waiting = compare(); assert finished['calls'] == 1 and waiting == 0
        print('REAL_LOCAL_GRAPH_INVOCATION_MATCH',flush=True)
