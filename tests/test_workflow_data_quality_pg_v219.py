"""Real PG/registry/lease/gateway/bytes/kernel/Audit/wait integration.

The inherited fixture explicitly substitutes graph SOURCE/175 authority only;
this is NOT a normal approved V184/V175 business acceptance. No paid success is
fabricated. Both local tool outputs are executed by the real Gateway; only
source/approval lookups and the Task-file membership boundary are synthetic.
Root must separately run the normal source/Task journey in H.
"""
import copy
from pathlib import Path
import stat
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, leases, worker, workflow_graph
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents import workflow_data_quality_v219 as q
from generic_agents import workflow_provenance_wait_v197 as waits
from generic_agents import layered_approval_store_v175 as approval
from generic_agents import workflow_graph_goal_guard_v133 as goal_guard
from generic_agents.contracts import digest
from generic_agents.evidence import save_evidence
from generic_agents.config import settings
from generic_agents.gateway import invoke
from generic_agents.services import audit
from tests.test_full_semantic_graph_v1 import ability, base, control, edge
from tests.test_workflow_provenance_wait_pg_v197 import scene as wait_scene
from tests.test_workflow_service_registration_v195 import publish


@pytest.fixture
def quality_scene(wait_scene, client, monkeypatch):
    s = wait_scene
    # conftest owns this TemporaryDirectory. Darwin's /var and /tmp aliases
    # must not turn a positive fixture into an O_NOFOLLOW rejection. Configure
    # only this disposable test to use its physical root; production still
    # rejects symlink components and the symlink-negative tests stay intact.
    storage = Path(settings().storage_dir)
    assert s.bound.url.database.startswith('ga_test_')
    assert storage.name.startswith('ga-test-evidence-')
    assert stat.S_ISDIR(storage.lstat().st_mode) and not storage.is_symlink()
    monkeypatch.setattr(settings(), 'storage_dir', storage.resolve(strict=True))
    qa = publish(client, q.manifest(q.QUALITY).model_dump(mode='json'))
    de = publish(client, q.manifest(q.DEDUP).model_dump(mode='json'))
    check = ability('check_originals', {}); dedup = ability('group_originals', {'quality_report':'previous_quality'})
    with Session(s.bound) as db:
        for node, result, handler in ((check, qa, q.QUALITY), (dedup, de, q.DEDUP)):
            cap=db.get(m.Capability,result['id'])
            node['actor_id'] = s.p.user_id
            node['execution'].update(capability_id=cap.id, capability_hash=cap.content_hash,
                arguments={'scope_ref': q.scope_ref()}, input_schema=q.input_schema(handler),
                output_schema=q.output_schema(), release_version=cap.release_version, max_attempts=1)
    s.graph = base([check, dedup, control('end','end')],
        [edge('check_originals','group_originals'),edge('group_originals','end')],
        'check_originals', ['check_originals','group_originals'])
    s.graph['references'] = {'previous_quality': {'kind':'object', 'nullable':False,
        'source':'output', 'field':'report', 'node_id':'check_originals'}}
    state = kernel.new_run(s.graph, {}, 10); activation = state['eligible_ability_ids'][0]
    inputs = kernel.ability_inputs(s.graph,state,activation)
    with Session(s.bound) as db:
        a = save_evidence(db, s.p, b'public technical original\n', 'text/plain')
        b = save_evidence(db, s.p, b'public technical original\n', 'text/plain')
        s.evidence_ids = [a.id,b.id]
        task, job = db.get(m.Task,s.task_id), db.get(m.Job,s.job_id)
        task.data = {'instance_id':task.data['instance_id'], 'instance_release_id':s.release_id,
            'business_id':'wait_business', 'entity_ids':task.data['entity_ids'], 'workflow_runtime':{'receipts':[]},
            'graph_runtime':state,'graph_jobs':{activation:{'job_id':job.id,'invocation_key':s.key,
                'input_hash':digest(inputs),'phase':'running','cancel_epoch':0}}}
        job.data = {'task_id':task.id,'actor_id':s.p.user_id,'activation_id':activation,
            'activation_version':0,'graph_hash':state['graph_hash'],'cancel_epoch':0,
            'inputs':inputs,'input_hash':digest(inputs),'invocation_key':s.key}
        db.commit()
    s.inputs, s.activation_id, s.qa_id, s.de_id = inputs, activation, qa['id'], de['id']
    # Explicit authority seams: do not claim that a fake package/175 grant is
    # a real production source. Real Actor/Job token/deadline and registry checks
    # inside graph_scope + the Gateway remain unchanged.
    monkeypatch.setattr(workflow_graph,'live',lambda *_args,**_kwargs:None)
    monkeypatch.setattr(workflow_graph,'business_binding',lambda *_args:{'schema':'synthetic-source-authority-only'})
    monkeypatch.setattr(approval,'guard',lambda *_args,**_kwargs:None)
    monkeypatch.setattr(goal_guard,'before_dispatch',lambda *_args,**_kwargs:None)
    def files(db,p,task):
        assert task.id == s.task_id and p.workspace_id == s.p.workspace_id
        selected = [{'row':db.get(m.Evidence,identity),'name':'public-original.txt',
            'refs':[{'kind':'synthetic_task_membership','id':identity,'selection_hash':digest(identity)}]}
            for identity in s.evidence_ids]
        return selected,[],['synthetic_task_membership'],digest(s.evidence_ids),digest(task.data['entity_ids'])
    monkeypatch.setattr(q,'inventory',files)
    return s


def safe_diagnostics(output):
    configured=Path(settings().storage_dir);root=configured.absolute()
    return {'quality_status':output.get('quality_status'), 'attributes':output.get('attributes'),
        'issues':[{key:row.get(key) for key in ('code','severity','evidence_id','field')}
            for row in output.get('issues',[])],
        'files':[{key:row.get(key) for key in ('evidence_id','hash_verified','format_verified','format_method')}
            for row in output.get('files',[])],
        'configured_root_is_absolute':configured.is_absolute(),
        'configured_root_is_physical':root==root.resolve(strict=True)}


def run_quality(s):
    with Session(s.bound) as db:
        leases.attach(db,s.job_id,s.token); db.info['worker_actor'] = s.p
        result = invoke(db,s.p,s.qa_id,'data',copy.deepcopy(s.inputs),s.key)
        assert result.status == 'succeeded',result.data.get('error')
        output, call_id = copy.deepcopy(result.data['output']),result.id
        # Every downstream tamper test must start from a genuine readable,
        # successful technical check, not an already blocked fixture.
        assert output['quality_status']=='pass' and output['attributes']['file_count']==2,safe_diagnostics(output)
    # Real kernel completion + real frontier enqueue, like graph process. Only
    # worker-final Job status is intentionally held running to expose the race.
    with Session(s.bound) as db:
        leases.attach(db,s.job_id,s.token); db.info['worker_actor'] = s.p
        task = db.get(m.Task,s.task_id); state = task.data['graph_runtime']
        activation = state['activations'][s.activation_id]
        state = kernel.complete(s.graph,state,s.activation_id,output,call_id,s.p.user_id,
            activation['version'],activation['epoch'],state['clock']+1)
        audit(db,s.p,'workflow.graph.node_completed',task.id,{'activation_id':s.activation_id,'invocation_id':call_id})
        release = db.get(m.InstanceRelease,s.release_id)
        workflow_graph.settle(db,s.p,task,release,release.data['snapshot']['businesses'][0],state)
        next_id = state['eligible_ability_ids'][0]
        next_job = task.data['graph_jobs'][next_id]['job_id']
        db.commit()
    s.call_id,s.output,s.next_job,s.next_activation = call_id,output,next_job,next_id
    return output


def start_wait(s):
    claim = worker.claim(s.next_job,[q.graph_scope.KIND],workspace_id=s.p.workspace_id)
    assert claim is not None and claim[0] == s.next_job
    s.next_token = claim[1]
    with Session(s.bound) as db:
        leases.attach(db,s.next_job,s.next_token); db.info['worker_actor'] = s.p
        task,job,cap = db.get(m.Task,s.task_id),db.get(m.Job,s.next_job),db.get(m.Capability,s.de_id)
        node = next(node for node in s.graph['nodes'] if node['id']=='group_originals')
        assert waits.preflight(db,s.p,task,job,node,cap,copy.deepcopy(job.data['inputs'])) is True
        db.commit()


def run_dedup(s):
    claim=worker.claim(s.next_job,[q.graph_scope.KIND],workspace_id=s.p.workspace_id)
    assert claim is not None and claim[0]==s.next_job
    with Session(s.bound) as db:
        leases.attach(db,s.next_job,claim[1]);db.info['worker_actor']=s.p
        task,job=db.get(m.Task,s.task_id),db.get(m.Job,s.next_job)
        data=copy.deepcopy(task.data)
        data['graph_jobs'][s.next_activation]['phase']='running';task.data=data;db.commit()
        result=invoke(db,s.p,s.de_id,'data',copy.deepcopy(job.data['inputs']),job.data['invocation_key'])
        assert result.status=='succeeded',result.data.get('error')
        return copy.deepcopy(result.data['output'])


def test_real_local_quality_receipt_then_producer_running_wait_and_same_job_wake(quality_scene):
    s=quality_scene
    with Session(s.bound) as db:
        before_calls=db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id==s.p.workspace_id))
    output=run_quality(s)
    assert output['quality_status']=='pass' and output['attributes']['file_count']==2,safe_diagnostics(output)
    assert len(output['duplicate_groups'])==1 and set(output['duplicate_groups'][0]['evidence_ids'])==set(s.evidence_ids)
    with Session(s.bound) as db:
        assert db.get(m.Job,s.job_id).status=='running'
        assert db.scalar(select(func.count()).select_from(m.Audit).where(m.Audit.action==q.PRODUCED,m.Audit.object_id==s.call_id))==1
    start_wait(s)
    with Session(s.bound) as db:
        job=db.get(m.Job,s.next_job); waiting=(job.id,job.idempotency_key,job.attempts,copy.deepcopy(job.data['inputs']))
        assert job.status==waits.PHASE
        assert db.scalar(select(func.count()).select_from(m.Invocation).where(m.Invocation.idempotency_key==job.data['invocation_key']))==0
        assert waits.schedule_once(db,workspace_id=s.p.workspace_id)==0
        db.commit()
    # Finalize the ACTUALLY EXECUTED local producer under its original lease.
    # No fabricated answer, new nonce, changed source, or artificial clock.
    with Session(s.bound) as db:
        leases.attach(db,s.job_id,s.token); db.info['worker_actor']=s.p
        db.get(m.Job,s.job_id).status='succeeded';db.commit()
    with Session(s.bound) as db:
        assert waits.schedule_once(db,workspace_id=s.p.workspace_id)==1
        db.commit()
    with Session(s.bound) as db:
        job=db.get(m.Job,s.next_job)
        assert job.status=='queued' and (job.id,job.idempotency_key,job.attempts,job.data['inputs'])==waiting
        assert db.scalar(select(func.count()).select_from(m.CloudCall).where(m.CloudCall.workspace_id==s.p.workspace_id))==before_calls
        assert all(db.get(m.Evidence,identity) is not None for identity in s.evidence_ids)
    result=run_dedup(s)
    assert result['report']['operation']=='deduplicate' and result['quality_status']=='pass'
    assert result['duplicate_groups']==output['duplicate_groups']


def test_dedup_reopens_actual_bytes_after_the_real_prior_quality_receipt(quality_scene):
    s=quality_scene;run_quality(s)
    with Session(s.bound) as db:
        leases.attach(db,s.job_id,s.token);db.info['worker_actor']=s.p
        evidence=db.get(m.Evidence,s.evidence_ids[0])
        path=settings().storage_dir/evidence.storage_key
        original=path.read_bytes()
        db.get(m.Job,s.job_id).status='succeeded';db.commit()
    try:
        path.write_bytes(b'corrupt bytes after quality')
        result=run_dedup(s)
        assert result['quality_status']=='blocked' and result['duplicate_groups']==[]
        assert all(not row['hash_verified'] for row in result['files'])
    finally:
        path.write_bytes(original)


@pytest.mark.parametrize('fault',['missing_produced_audit','modified_original_output','missing_completion_audit'])
def test_wait_never_treats_an_incomplete_original_quality_chain_as_success(quality_scene,fault):
    s=quality_scene;run_quality(s)
    with Session(s.bound) as db:
        if fault=='modified_original_output':
            call=db.get(m.Invocation,s.call_id);value=copy.deepcopy(call.data)
            value['output']['report']['quality_status']='needs_review';call.data=value
        else:
            action=q.PRODUCED if fault=='missing_produced_audit' else 'workflow.graph.node_completed'
            rows=list(db.scalars(select(m.Audit).where(m.Audit.action==action,
                m.Audit.object_id==(s.call_id if action==q.PRODUCED else s.task_id))))
            assert len(rows)==1;db.delete(rows[0])
        db.commit()
    with pytest.raises(HTTPException):start_wait(s)
    with Session(s.bound) as db:
        assert db.scalar(select(func.count()).select_from(m.Audit).where(m.Audit.action==waits.WAIT,m.Audit.object_id==s.next_job))==0


def test_real_registry_capability_alone_never_grants_a_graph_execution(wait_scene,client):
    s=wait_scene;cap=publish(client,q.manifest().model_dump(mode='json'))
    with Session(s.bound) as db:
        call=invoke(db,s.p,cap['id'],'data',{'scope_ref':q.scope_ref()},'unowned-originals:'+str(uuid4()))
        assert call.status=='blocked_evidence'
        assert call.data.get('output') is None
        assert db.scalar(select(func.count()).select_from(m.Audit).where(m.Audit.action==q.PRODUCED,m.Audit.object_id==call.id))==0
