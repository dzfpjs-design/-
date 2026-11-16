"""Real registered image workflow, three authenticated humans and V175 basis.

Only test account setup creates User/Membership rows. Configuration, registry,
adoption, publication, roles, approvals, Task, drafts, completions, exact worker
Jobs, tools, original files and report artifacts use their normal product paths.
This is PG/API acceptance; Root separately proves the running product consumer.
"""
import copy
import hashlib
import json
from contextlib import ExitStack
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents import models as m, authored_image_result_integrity_v243 as integrity
from generic_agents import workflow_data_quality_v219 as quality, workflow_quality_archive_v220 as archive
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.security import password_hash
from generic_agents.worker import process_once
from tests.test_authored_image_workflow_pg_v243 import BASE,good,exact,prepare_tools,configured,body
from tests.test_instance_workspace import OBJECTS,object_row
from tests.test_workflow_images import upload as upload_image

WORKSPACE = '/api/workspace'


def actor_client(stack,workspace_id):
    username,password = 'image_accept_'+uuid4().hex, uuid4().hex
    with Session(engine()) as db:
        user = m.User(username=username,password_hash=password_hash(password))
        db.add(user); db.flush(); identity = user.id
        db.add(m.Membership(user_id=identity,workspace_id=workspace_id,role='operator')); db.commit()
    client = stack.enter_context(TestClient(app))
    login = good(client.post('/api/login',json={'username':username,'password':password}))
    client.headers.update({'X-CSRF-Token':login['csrf_token'],'X-Workspace-ID':workspace_id})
    return identity,client


def approval(client,task_id): return good(client.get(WORKSPACE+'/tasks/'+task_id+'/approval-v175'))
def task(client,task_id): return good(client.get(WORKSPACE+'/tasks/'+task_id))


def command(client,task_id,operation,**extra):
    current = approval(client,task_id)
    value = {'request_id':str(uuid4()),'expected_version':current['version'],
        'reason':'依据原任务真实结果及职责完成本次'+operation,**extra}
    result = good(client.post(WORKSPACE+'/tasks/'+task_id+'/approval-v175/'+operation,content=exact(value)))
    return value,result


def queued_job(value,operation):
    matches = [entry['job_id'] for activation_id,entry in value['graph_jobs'].items()
        if value['graph_runtime']['activations'][activation_id]['node_id'] == operation and entry['phase'] == 'queued']
    assert len(matches) == 1,(operation,matches)
    return matches[0]


def report_diagnostic(value,job_id):
    """One compact failure explanation; never dump Task, inputs or raw output."""
    def brief(error): return None if error is None else str(error)[:240]
    with Session(engine()) as db:
        job = db.get(m.Job,job_id)
        call = (db.scalar(select(m.Invocation).where(m.Invocation.workspace_id == job.workspace_id,
            m.Invocation.idempotency_key == job.data.get('invocation_key'))) if job else None)
        transport = value.get('graph_jobs',{}).get(job.data.get('activation_id'),{}) if job else {}
        root = settings().storage_dir
        return {'task_status':value.get('status'),'graph_status':value.get('graph_runtime',{}).get('status'),
            'transport':{key:brief(transport.get(key)) for key in ('phase','reason','invocation_status')},
            'job':{'status':job.status if job else None,'error':brief(job.data.get('error')) if job else None},
            'invocation':{'status':call.status if call else None,'error':brief(call.data.get('error')) if call else None},
            'storage_root_is_physical':root.absolute() == root.resolve(strict=True)}


@pytest.fixture
def completed_image_case(client,monkeypatch):
    # Only resolve the known disposable directory provided by public conftest.
    # macOS /var aliases /private/var; production keeps strict NOFOLLOW fences.
    storage = settings().storage_dir
    assert storage.name.startswith('ga-test-evidence-') and storage.is_dir() and not storage.is_symlink()
    monkeypatch.setattr(settings(),'storage_dir',storage.resolve(strict=True))
    with ExitStack() as stack:
        me = good(client.get('/api/me')); workspace_id,owner = me['workspace_id'],me['user_id']
        operators = [actor_client(stack,workspace_id) for _ in range(3)]
        acceptor,accept_client = actor_client(stack,workspace_id)
        prepare_tools(client); configured(client)
        selected = body(client)
        for role,(identity,_) in zip(sorted(selected['role_bindings']),operators): selected['role_bindings'][role] = identity
        preview = good(client.post(BASE+'/preview',content=exact(selected)))
        adopted = good(client.post(BASE+'/adopt',content=exact({**selected,
            'schema':'authored-image-workflow-adopt-command-v243','request_id':str(uuid4()),
            'preview_hash':preview['preview_hash'],'consent':True})),201)
        current = good(client.get('/api/setup/draft'))
        compilation = {'revision':current['revision'],'objects':OBJECTS,
            'role_bindings':{role:actor['user_id'] for role,actor in preview['binding']['role_bindings'].items()}}
        checked = good(client.post('/api/instances/check',json=compilation))
        assert not checked['general_issues']
        release = good(client.post('/api/instances',json={**compilation,
            'confirmed_configuration_hash':checked['configuration_hash'],'confirm_configuration':True}),201)
        instance = next(row for row in good(client.get('/api/instances')) if row['id'] == release['instance_id'])
        good(client.post('/api/instances/'+instance['id']+'/activate',json={'version':instance['version'],
            'release_id':release['id'],'business_ids':release['available_business_ids'],'confirm_workflow_scope':True}))
        business_id = release['available_business_ids'][0]
        unit = str(uuid4())
        good(client.post(WORKSPACE+'/approval-settings-v175/units',json={'request_id':str(uuid4()),
            'unit_id':unit,'expected_version':0,'name':'本次图像技术复核组织','parent_id':None,'enabled':True}))
        grants = [(identity,'approval_'+str(index)) for index,(identity,_) in enumerate(operators)]
        grants += [(identity,'execute') for identity in [owner,*[identity for identity,_ in operators]]]
        grants += [(acceptor,'acceptance')]
        for identity,role in grants:
            good(client.post(WORKSPACE+'/approval-settings-v175/roles',json={'request_id':str(uuid4()),
                'grant_id':str(uuid4()),'expected_version':0,'unit_id':unit,'user_id':identity,'role_key':role,'enabled':True}))
        good(client.post(WORKSPACE+'/approval-settings-v175/policies',json={'request_id':str(uuid4()),
            'instance_id':instance['id'],'business_id':business_id,'expected_version':0,'name':'三级批准与独立技术验收',
            'scope_id':unit,'stages':[{'name':'正式批准'+str(index+1),'role_key':'approval_'+str(index),'scope_id':unit} for index in range(3)],
            'executor':{'name':'原任务执行人','role_key':'execute','scope_id':unit},
            'acceptance':{'name':'独立验收人','role_key':'acceptance','scope_id':unit},'enabled':True}))
        entity = object_row(client,release); image = good(upload_image(client,release),201)
        created = good(client.post(WORKSPACE+'/instances/'+instance['id']+'/tasks',json={
            'release_id':release['id'],'business_id':business_id,'entity_ids':[entity['id']],
            'image_ids':[image['id']],'title':'真实图像技术复核与独立验收','request_id':str(uuid4())}),201)
        task_id = created['id']; assert created['status'] == 'approval_draft'
        command(client,task_id,'submit')
        for _,operator in operators:
            pending = approval(operator,task_id)
            command(operator,task_id,'decide',decision='approve',submission_hash=pending['submission_hash'])
        assert approval(client,task_id)['status'] == 'approved'
        command(client,task_id,'start')
        for operation in ('quality','dedup'):
            identity = queued_job(task(client,task_id),operation)
            assert process_once(job_id=identity,kinds=['execute_full_semantic_graph_node'],deliver=False,workspace_id=workspace_id)
        by_actor = dict(operators); human_records = []
        for index in range(3):
            current_task = task(client,task_id)
            assert len(current_task['graph_pending_humans']) == 1
            pending = current_task['graph_pending_humans'][0]; operator = by_actor[pending['actor_id']]
            output = {'result':'操作员'+str(index+1)+'已对照原图与技术结果，独立保留本环节意见'}
            draft = {'request_id':str(uuid4()),'activation_id':pending['activation_id'],
                'version':pending['version'],'cancel_epoch':current_task['graph_runtime']['cancel_epoch'],'draft':output}
            saved = good(operator.put(WORKSPACE+'/tasks/'+task_id+'/graph/draft',json=draft))
            assert task(operator,task_id)['graph_pending_humans'][0]['draft'] == output
            pending = saved['graph_pending_humans'][0]
            completed = {'request_id':str(uuid4()),'activation_id':pending['activation_id'],
                'version':pending['version'],'cancel_epoch':saved['graph_runtime']['cancel_epoch'],'output':output}
            good(operator.post(WORKSPACE+'/tasks/'+task_id+'/graph/complete',json=completed))
            replay = good(operator.post(WORKSPACE+'/tasks/'+task_id+'/graph/complete',json=completed))
            recovery = good(operator.get(WORKSPACE+'/tasks/'+task_id+'/graph/requests/'+completed['request_id']))
            assert recovery['found'] and recovery['request']['actor_id'] == pending['actor_id']
            assert sum(row['request_id'] == completed['request_id'] for row in replay['workflow_runtime']['receipts']) == 1
            human_records.append((pending['actor_id'],output))
        identity = queued_job(task(client,task_id),'report')
        assert process_once(job_id=identity,kinds=['execute_full_semantic_graph_node'],deliver=False,workspace_id=workspace_id)
        completed = task(client,task_id)
        assert completed['status'] == 'workflow_completed' and completed['completed'] is True, report_diagnostic(completed,identity)
        humans = [row for row in completed['workflow_runtime']['receipts'] if row['execution_mode'] == 'human_graph_result']
        assert [(row['actor'],row['output']) for row in humans] == human_records
        output = next(row['output'] for row in completed['workflow_runtime']['receipts'] if row['step_id'] == 'report')
        assert output['artifacts'].keys() == {'markdown','machine_json'}
        yield NS(client=client,accept_client=accept_client,task_id=task_id,completed=completed,
            output=output,image=image,workspace_id=workspace_id,humans=copy.deepcopy(humans),adopted=adopted)


def inventory():
    with Session(engine()) as db:
        return {model.__tablename__:db.scalar(select(func.count()).select_from(model))
            for model in (m.Job,m.Invocation,m.Audit,m.Evidence,m.Memory,m.CloudCall)}


def test_real_completed_image_report_and_three_humans_form_independent_acceptance_basis(completed_image_case):
    s = completed_image_case
    before = inventory(); view = approval(s.accept_client,s.task_id)
    assert view['acceptance_basis'] is not None and view['acceptance_issue'] is None
    assert view['permissions']['accept'] is True and approval(s.client,s.task_id)['permissions']['accept'] is False
    basis = view['acceptance_basis']
    assert basis['physical_state_verified'] is False
    assert [row for row in basis['workflow_runtime']['receipts'] if row['execution_mode'] == 'human_graph_result'] == s.humans
    assert inventory() == before  # Basis reading does not run tools or write receipts.
    accepted,receipt = command(s.accept_client,s.task_id,'accept',basis_hash=basis['basis_hash'],decision='accept')
    assert receipt['status'] == 'accepted'
    raw = exact(accepted)
    recovery = good(s.accept_client.get(WORKSPACE+'/approval-settings-v175/requests/'+accepted['request_id'],
        params={'expected_sha256':hashlib.sha256(raw).hexdigest()}))
    assert recovery['result']['status'] == 'accepted'
    assert task(s.client,s.task_id)['workflow_runtime']['receipts'] == s.completed['workflow_runtime']['receipts']


@pytest.mark.parametrize('fault',['missing_report','bad_report_bytes','missing_quality_producer_audit',
    'missing_report_producer_audit','unbound_receipt_output','bad_original_bytes',
    'missing_human_completed_audit','missing_report_node_completed_audit'])
def test_damaged_real_result_cannot_generate_or_accept_a_replacement_basis(completed_image_case,fault):
    s = completed_image_case
    original = approval(s.accept_client,s.task_id)
    assert original['acceptance_basis'] is not None
    changed_path = None; original_bytes = None; original_mode = None
    with Session(engine()) as db:
        saved = db.get(m.Task,s.task_id)
        if fault in {'missing_report','bad_report_bytes','bad_original_bytes'}:
            identity = s.image['id'] if fault == 'bad_original_bytes' else s.output['artifacts']['markdown']['evidence_id']
            changed_path = settings().storage_dir/db.get(m.Evidence,identity).storage_key
            original_bytes = changed_path.read_bytes()
            original_mode = changed_path.stat().st_mode & 0o777
            if fault == 'missing_report': changed_path.unlink()
            else: changed_path.write_bytes(b'explicit-corruption-in-this-disposable-result')
        elif fault == 'unbound_receipt_output':
            value = copy.deepcopy(saved.data)
            next(row for row in value['workflow_runtime']['receipts'] if row['step_id'] == 'report')['output']['archive_hash'] = '0'*64
            saved.data = value
        elif fault in {'missing_human_completed_audit','missing_report_node_completed_audit'}:
            human = fault == 'missing_human_completed_audit'
            receipt = (next(row for row in saved.data['workflow_runtime']['receipts'] if row['execution_mode'] == 'human_graph_result')
                if human else next(row for row in saved.data['workflow_runtime']['receipts'] if row['step_id'] == 'report'))
            action = 'workflow.graph.human_completed' if human else 'workflow.graph.node_completed'
            event = db.scalar(select(m.Audit).where(m.Audit.workspace_id == s.workspace_id,
                m.Audit.action == action,m.Audit.object_id == s.task_id,
                m.Audit.data['activation_id'].as_string() == receipt['activation_id']))
            assert event is not None; db.delete(event)
        else:
            operation = 'quality' if fault == 'missing_quality_producer_audit' else 'report'
            call_id = next(row['invocation_id'] for row in saved.data['workflow_runtime']['receipts'] if row['step_id'] == operation)
            action = quality.PRODUCED if operation == 'quality' else archive.PRODUCED
            event = db.scalar(select(m.Audit).where(m.Audit.action == action,m.Audit.object_id == call_id))
            assert event is not None; db.delete(event)
        db.commit()
    try:
        before = inventory(); view = approval(s.accept_client,s.task_id)
        assert view['acceptance_basis'] is None and view['acceptance_issue'],fault
        assert view['permissions']['accept'] is False and inventory() == before
        command_body = {'request_id':str(uuid4()),'expected_version':view['version'],'reason':'按原成功依据尝试本次技术验收',
            'basis_hash':original['acceptance_basis']['basis_hash'],'decision':'accept'}
        response = s.accept_client.post(WORKSPACE+'/tasks/'+s.task_id+'/approval-v175/accept',json=command_body)
        assert response.status_code == 409,(fault,response.text[:300])
        assert inventory() == before  # No report repair, tool rerun or success receipt.
    finally:
        if changed_path is not None:
            changed_path.write_bytes(original_bytes)
            changed_path.chmod(original_mode)


def test_non_image_family_is_unchanged_without_reading_any_result():
    class NoDatabase:
        def __getattr__(self,name): pytest.fail('Old families must not acquire image-specific checks')
    assert integrity.verify_task_results(NoDatabase(),None,None,None,{'schema':'authored-workflow-binding-v208'}) is None
