"""Actual local n3/n4 -> Artifact pair -> original Gateway cold replay.

Source/175 authority is the explicit synthetic seam inherited from V219 PG;
this is not a normal V184/175 user acceptance. Registry, leases, real original
bytes, both upstream Gateway outputs, Kernel completions, independent Audits,
archive preflight/wake, FS and result transactions execute for real on ga_test.
No CloudCall success is fabricated and no provider/network operation is used.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import gateway,leases,models as m,worker,workflow_graph
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents import workflow_quality_archive_v220 as archive
from generic_agents import workflow_provenance_wait_v197 as waits
from generic_agents import evidence as evidence_store
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.services import audit
from tests.test_full_semantic_graph_v1 import ability,edge
from tests.test_workflow_data_quality_pg_v219 import quality_scene,run_quality,run_dedup
from tests.test_workflow_provenance_wait_pg_v197 import scene as wait_scene
from tests.test_workflow_service_registration_v195 import publish


@pytest.fixture
def archive_scene(quality_scene,client):
    s=quality_scene;published=publish(client,archive.manifest().model_dump(mode='json'))
    node=ability('technical_summary',{'quality_report':'previous_quality','dedup_report':'previous_dedup'})
    with Session(s.bound) as db:
        cap=db.get(m.Capability,published['id']);s.archive_cap=cap.id
        node['actor_id']=s.p.user_id
        node['execution'].update(capability_id=cap.id,capability_hash=cap.content_hash,
            input_schema=archive.input_schema(),output_schema=archive.output_schema(),release_version=cap.release_version,max_attempts=1)
        s.graph['nodes'].insert(-1,node)
        s.graph['edges']=[row for row in s.graph['edges'] if row['from']!='group_originals']
        s.graph['edges'] += [edge('group_originals','technical_summary'),edge('technical_summary','end')]
        s.graph['goals'][0]['required_nodes'].append('technical_summary')
        s.graph['references']['previous_dedup']={'kind':'object','nullable':False,'source':'output','field':'report','node_id':'group_originals'}
        state=kernel.new_run(s.graph,{},10);activation=state['eligible_ability_ids'][0]
        task,job=db.get(m.Task,s.task_id),db.get(m.Job,s.job_id)
        task.data={**task.data,'graph_runtime':state}
        job.data={**job.data,'graph_hash':state['graph_hash']}
        db.commit()
    s.qa_output=run_quality(s)
    finish_job(s,s.job_id)
    s.dedup_output=run_dedup(s)
    with Session(s.bound) as db:
        job=db.get(m.Job,s.next_job);token=job.lease_token
    with Session(s.bound) as db:
        leases.attach(db,s.next_job,token);db.info['worker_actor']=s.p
        task=db.get(m.Task,s.task_id);state=task.data['graph_runtime'];activation=state['activations'][s.next_activation]
        original_job=db.get(m.Job,s.next_job)
        call=db.scalar(select(m.Invocation).where(m.Invocation.idempotency_key==original_job.data['invocation_key']))
        state=kernel.complete(s.graph,state,s.next_activation,s.dedup_output,call.id,s.p.user_id,
            activation['version'],activation['epoch'],state['clock']+1)
        audit(db,s.p,'workflow.graph.node_completed',task.id,{'activation_id':s.next_activation,'invocation_id':call.id})
        release=db.get(m.InstanceRelease,s.release_id)
        workflow_graph.settle(db,s.p,task,release,release.data['snapshot']['businesses'][0],state)
        s.archive_activation=state['eligible_ability_ids'][0]
        s.archive_job=task.data['graph_jobs'][s.archive_activation]['job_id']
        db.commit()
    return s


def finish_job(s,identity):
    with Session(s.bound) as db:token=db.get(m.Job,identity).lease_token
    with Session(s.bound) as db:
        leases.attach(db,identity,token);db.info['worker_actor']=s.p
        db.get(m.Job,identity).status='succeeded';db.commit()


def artifact_count(db,s):
    return db.scalar(select(func.count()).select_from(m.Evidence).where(m.Evidence.workspace_id==s.p.workspace_id,
        m.Evidence.data['purpose'].as_string()==archive.KEY))


def invoke_archive(s):
    claim=worker.claim(s.archive_job,[archive.scope.KIND],workspace_id=s.p.workspace_id)
    assert claim is not None
    with Session(s.bound) as db:
        leases.attach(db,s.archive_job,claim[1]);db.info['worker_actor']=s.p
        task,job=db.get(m.Task,s.task_id),db.get(m.Job,s.archive_job)
        data=copy.deepcopy(task.data);data['graph_jobs'][s.archive_activation]['phase']='running';task.data=data;db.commit()
        call=gateway.invoke(db,s.p,s.archive_cap,'data',copy.deepcopy(job.data['inputs']),job.data['invocation_key'])
        s.archive_call=call.id;s.archive_key=job.data['invocation_key']
        return call.status,copy.deepcopy(call.data)


def test_actual_report_pair_wait_then_two_real_artifacts_and_cold_original_replay(archive_scene):
    s=archive_scene
    claim=worker.claim(s.archive_job,[archive.scope.KIND],workspace_id=s.p.workspace_id);assert claim
    with Session(s.bound) as db:
        leases.attach(db,s.archive_job,claim[1]);db.info['worker_actor']=s.p
        task,job,cap=db.get(m.Task,s.task_id),db.get(m.Job,s.archive_job),db.get(m.Capability,s.archive_cap)
        node=next(row for row in s.graph['nodes'] if row['id']=='technical_summary')
        assert artifact_count(db,s)==0
        assert waits.preflight(db,s.p,task,job,node,cap,copy.deepcopy(job.data['inputs'])) is True
        # The original lease fence must still observe DB status=running when
        # this transaction commits. No ORM query/autoflush after preflight.
        db.commit()
    with Session(s.bound) as db:assert artifact_count(db,s)==0
    finish_job(s,s.next_job)
    with Session(s.bound) as db:
        assert waits.schedule_once(db,workspace_id=s.p.workspace_id)==1;db.commit()
    status,data=invoke_archive(s);assert status=='succeeded',data.get('error')
    output=data['output'];assert output['technical_status']=='pass'
    with Session(s.bound) as db:
        call=db.get(m.Invocation,s.archive_call)
        assert archive.checked_saved_result(db,s.p,call)==output
        assert artifact_count(db,s)==2
        for action in (archive.SAVED,archive.PRODUCED):
            assert db.scalar(select(func.count()).select_from(m.Audit).where(m.Audit.action==action,m.Audit.object_id==call.id))==1
        before=digest([(row.id,row.sha256,copy.deepcopy(row.data)) for row in db.scalars(select(m.Evidence).where(m.Evidence.workspace_id==s.p.workspace_id).order_by(m.Evidence.id))])
        for kind,ref in output['artifacts'].items():
            row=db.get(m.Evidence,ref['evidence_id'])
            raw=archive.quality.read_original(settings().storage_dir,s.p.workspace_id,row)
            assert hashlib.sha256(raw).hexdigest()==ref['sha256']
            if kind=='markdown':assert raw.decode().startswith('# 任务内部技术汇总')
            else:
                payload=json.loads(raw)
                assert payload['source_reports']['quality_report']['report_hash']==s.qa_output['report']['report_hash']
                assert payload['source_reports']['dedup_report']['report_hash']==s.dedup_output['report']['report_hash']
                assert payload['formal_deliverable'] is False
            assert b'/private/' not in raw and b'public-original.txt' not in raw
        original_inputs=copy.deepcopy(db.get(m.Job,s.archive_job).data['inputs'])
    # New DB session, no worker lease: only the SAME successful original is
    # read. No new input/source/Artifact writes or re-execution are permitted.
    with Session(s.bound) as db:
        replay=gateway.invoke(db,s.p,s.archive_cap,'data',original_inputs,s.archive_key)
        assert replay.id==s.archive_call and replay.data['output']==output
        after=digest([(row.id,row.sha256,copy.deepcopy(row.data)) for row in db.scalars(select(m.Evidence).where(m.Evidence.workspace_id==s.p.workspace_id).order_by(m.Evidence.id))])
        assert before==after


@pytest.mark.parametrize('action',[archive.SAVED,archive.PRODUCED,'evidence.upload'])
def test_original_recovery_never_recreates_a_missing_independent_audit(archive_scene,action):
    s=archive_scene;finish_job(s,s.next_job);status,data=invoke_archive(s);assert status=='succeeded',data.get('error')
    with Session(s.bound) as db:
        identity=s.archive_call if action!='evidence.upload' else data['output']['artifacts']['markdown']['evidence_id']
        rows=list(db.scalars(select(m.Audit).where(m.Audit.action==action,m.Audit.object_id==identity)))
        assert len(rows)==1;db.delete(rows[0]);db.commit()
    with Session(s.bound) as db:
        call=db.get(m.Invocation,s.archive_call);before=artifact_count(db,s)
        with pytest.raises(HTTPException):archive.checked_saved_result(db,s.p,call)
        assert artifact_count(db,s)==before


def test_gateway_output_failure_rolls_back_both_artifact_rows_and_saved_marker(archive_scene,monkeypatch):
    s=archive_scene;finish_job(s,s.next_job);original=gateway.validate;injected=[]
    def rejected(schema,value):
        if schema==archive.output_schema():
            injected.append(value.get('archive_hash'))
            raise HTTPException(422,'Synthetic failure after real file save')
        return original(schema,value)
    monkeypatch.setattr(gateway,'validate',rejected)
    status,data=invoke_archive(s)
    assert len(injected)==1 and injected[0], 'The real handler must reach outer result validation before this failure test counts'
    assert status!='succeeded'
    with Session(s.bound) as db:
        assert artifact_count(db,s)==0
        assert archive.KEY not in db.get(m.Invocation,s.archive_call).data
        for action in (archive.SAVED,archive.PRODUCED):
            assert db.scalar(select(func.count()).select_from(m.Audit).where(m.Audit.action==action,m.Audit.object_id==s.archive_call))==0
        with pytest.raises(HTTPException):archive.checked_saved_result(db,s.p,db.get(m.Invocation,s.archive_call))


@pytest.mark.parametrize('fault',['target_symlink','ancestor_swap'])
def test_real_archive_does_not_write_through_external_symlinks(archive_scene,monkeypatch,tmp_path,fault):
    s=archive_scene;finish_job(s,s.next_job)
    outside=tmp_path.resolve()/'outside';outside.mkdir();sentinel=outside/'sentinel';sentinel.write_bytes(b'untouched external')
    root=Path(settings().storage_dir);original=archive._put;swapped=[]
    def tamper(fd,content):
        if not swapped:
            if fault=='target_symlink':
                target=root/s.p.workspace_id/(hashlib.sha256(content).hexdigest()+'.bin');target.symlink_to(sentinel)
                swapped.append(target)
            else:
                moved=root.with_name(root.name+'-held');root.rename(moved);root.symlink_to(outside,target_is_directory=True)
                swapped.append(moved)
        return original(fd,content)
    monkeypatch.setattr(archive,'_put',tamper)
    try:
        status,data=invoke_archive(s);assert status!='succeeded'
        assert swapped, 'The real archive must reach the injected filesystem fault; an earlier failure is not FS evidence'
        assert sentinel.read_bytes()==b'untouched external' and list(outside.iterdir())==[sentinel]
        with Session(s.bound) as db:assert artifact_count(db,s)==0
    finally:
        if swapped:
            if fault=='target_symlink':swapped[0].unlink(missing_ok=True)
            else:root.unlink();swapped[0].rename(root)
