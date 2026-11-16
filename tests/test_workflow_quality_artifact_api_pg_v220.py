"""Real V219/220 local producer/FS proofs through authenticated READ ONLY GET.

The inherited fixtures retain an explicit synthetic original workflow/source
permission seam; this is not H's normal V184/175 acceptance. Real accounts,
workspace auth, Task scope, Gateway results, both artifacts/Audits, bytes and
the actual snapshot GET dependency are used. No provider or paid result exists.
"""
import copy
import hashlib
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func,select
from sqlalchemy.orm import Session

from generic_agents import models as m,workspace_api
from generic_agents import workflow_quality_archive_v220 as archive
from generic_agents import workflow_quality_artifact_api_v220 as api
from generic_agents import full_semantic_graph_v1 as kernel
from generic_agents.services import scoped
from tests.test_workflow_quality_archive_pg_v220 import archive_scene,finish_job,invoke_archive
from tests.test_workflow_data_quality_pg_v219 import quality_scene
from tests.test_workflow_provenance_wait_pg_v197 import scene as wait_scene


@pytest.fixture
def artifact_case(archive_scene,client,monkeypatch):
    s=archive_scene;finish_job(s,s.next_job);status,data=invoke_archive(s)
    assert status=='succeeded',data.get('error')
    s.artifact_output=data['output'];s.client=client
    # Same explicit source permission seam as the original PG fixture, extended
    # to history readers. This does not authorize a new dispatch or a lease.
    def saved(db,p,identity,lock=False):
        assert lock is False
        task=scoped(db,m.Task,identity,p)
        release=scoped(db,m.InstanceRelease,task.data['instance_release_id'],p)
        return task,release,release.data['snapshot']['businesses'][0]
    def frozen(db,p,identity,activation_id):
        task,release,business=saved(db,p,identity)
        graph=copy.deepcopy(s.graph);state=kernel.check_run(graph,task.data['graph_runtime'])
        activation=state['activations'][activation_id]
        node=next(row for row in graph['nodes'] if row['id']==activation['node_id'])
        return task,release,business,graph,state,activation,node
    monkeypatch.setattr(workspace_api,'saved_execution',saved)
    monkeypatch.setattr(archive.scope,'_frozen',frozen)
    return s


def url(s,kind='markdown'):
    return '/api/workspace/tasks/'+s.task_id+'/technical-artifacts/'+s.artifact_output['artifacts'][kind]['evidence_id']


def inventory(s):
    with Session(s.bound) as db:
        return {model.__tablename__:db.scalar(select(func.count()).select_from(model).where(model.workspace_id==s.p.workspace_id))
            for model in (m.Task,m.Job,m.Invocation,m.Evidence,m.Audit,m.Memory,m.CloudCall)}


@pytest.mark.parametrize('kind',['markdown','machine_json'])
def test_normal_authenticated_get_reads_exact_pair_bytes_and_no_business_writes(artifact_case,kind):
    s=artifact_case;before=inventory(s);descriptor=s.artifact_output['artifacts'][kind]
    for query,disposition in (('?download=false','inline;'),('?download=true','attachment;')):
        response=s.client.get(url(s,kind)+query)
        assert response.status_code==200,response.text[:200]
        assert len(response.content)==descriptor['size_bytes']==int(response.headers['Content-Length'])
        assert hashlib.sha256(response.content).hexdigest()==descriptor['sha256']==response.headers['X-Content-SHA256']
        assert response.headers['Content-Type'].split(';')[0]==descriptor['media_type']
        assert response.headers['X-Technical-Artifact-Kind']==kind
        assert response.headers['Content-Disposition'].startswith(disposition)
        assert response.headers['Cache-Control']=='private, no-store'
    assert inventory(s)==before


def test_readonly_api_does_not_require_current_inventory_device_or_new_lease(artifact_case,monkeypatch):
    s=artifact_case
    monkeypatch.setattr(archive.quality,'inventory',lambda *_:pytest.fail('Saved Artifact GET must not ask for current inventory'))
    response=s.client.get(url(s))
    assert response.status_code==200,response.text[:200]
    with TestClient(s.client.app) as anonymous:assert anonymous.get(url(s)).status_code==401


def test_current_workspace_viewer_can_read_the_same_saved_task_artifact(artifact_case):
    s=artifact_case;name='artifact_viewer_'+uuid4().hex[:10];password=uuid4().hex
    account=s.client.post('/api/users',json={'username':name,'password':password,'role':'viewer'})
    assert account.status_code==201,account.text[:200]
    with TestClient(s.client.app) as viewer:
        login=viewer.post('/api/login',json={'username':name,'password':password})
        assert login.status_code==200,login.text[:200]
        viewer.headers.update({'X-Workspace-ID':s.p.workspace_id})
        result=viewer.get(url(s));assert result.status_code==200,result.text[:200]


@pytest.mark.parametrize('fault',['foreign_task','foreign_workspace','ordinary_evidence','orphan','deleted_produced','corrupt_bytes'])
def test_task_scope_or_broken_proof_never_downloads_arbitrary_or_incomplete_evidence(artifact_case,fault):
    s=artifact_case;target=url(s);restore=None
    with Session(s.bound) as db:
        if fault=='foreign_task':
            original=db.get(m.Task,s.task_id)
            other=m.Task(workspace_id=s.p.workspace_id,package_id=original.package_id,owner_id=s.p.user_id,
                title='Negative unrelated Task, no accepted or successful workflow',status='workflow_waiting',
                data=copy.deepcopy(original.data))
            db.add(other);db.flush();target=target.replace(s.task_id,other.id);db.commit()
        elif fault=='foreign_workspace':pass
        elif fault=='ordinary_evidence':target='/api/workspace/tasks/'+s.task_id+'/technical-artifacts/'+s.evidence_ids[0]
        elif fault=='orphan':db.get(m.Invocation,s.archive_call).status='failed';db.commit()
        elif fault=='deleted_produced':
            rows=list(db.scalars(select(m.Audit).where(m.Audit.action==archive.PRODUCED,m.Audit.object_id==s.archive_call)))
            assert len(rows)==1;db.delete(rows[0]);db.commit()
        else:
            row=db.get(m.Evidence,s.artifact_output['artifacts']['markdown']['evidence_id'])
            path=archive.settings().storage_dir/row.storage_key;restore=(path,path.read_bytes());path.write_bytes(b'corrupt Artifact')
    before=inventory(s)
    try:
        result=s.client.get(target,headers={'X-Workspace-ID':str(uuid4())}) if fault=='foreign_workspace' else s.client.get(target)
        allowed={403,404} if fault=='foreign_workspace' else {404} if fault in {'foreign_task','ordinary_evidence'} else {409}
        assert result.status_code in allowed,result.text[:200]
        assert result.headers.get('X-Content-SHA256') is None
        assert inventory(s)==before
    finally:
        if restore is not None:restore[0].write_bytes(restore[1])
