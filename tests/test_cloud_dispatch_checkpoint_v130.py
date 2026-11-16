"""Root-only real API/Worker/PG recovery capture, synthetic provider responses."""
import json
import os
from pathlib import Path
import re
import shutil
import stat
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from generic_agents.api import app
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Workspace, Membership, Memory
from sqlalchemy import select
from tests.test_native_suggestion_not_sent_v130 import test_actual_reservation_then_public_policy_withdrawal_is_confirmed_not_sent as _confirmed_journey
from tests.test_native_suggestion_not_sent_v130 import run_not_sent_retry_journey, run_timeout_unknown_journey
from tests import test_native_method_suggestions as old
from tests.test_observation_submission_checkpoint_v128 import (
    _owned_database, _dump_database, _tree_snapshot, _private_dir, _json_private, _file_sha, _json_response,
)


def test_capture_actual_dispatch_failure_recovery(client, account, monkeypatch, tmp_path_factory):
    capture=Path(os.environ['GA_V130_CAPTURE_DIR'])
    assert capture.is_absolute() and capture.name=='full-semantic-graph-v130' and capture.parent.name=='.runtime'
    _private_dir(capture);assert capture.resolve(strict=True)==capture
    temporary=tmp_path_factory.getbasetemp().resolve(strict=True)
    assert re.fullmatch(r'pytest-\d+',temporary.name) and temporary.parent==capture/'source-runtime'
    marker=temporary.parent/'ROOT_OWNED.json'
    assert stat.S_IMODE(marker.stat().st_mode)==0o600
    assert json.loads(marker.read_text())=={'schema':'v130-owned-source-runtime','synthetic_only':True,'purpose':'v130-durable-checkpoint'}
    url,comment=_owned_database();me=_json_response(client.get('/api/me'))
    quota=run_not_sent_retry_journey(client,monkeypatch)
    quota['workspace_id']=me['workspace_id']
    with Session(engine()) as db:
        workspace=Workspace(name='独立超时未知恢复核对');db.add(workspace);db.flush()
        db.add(Membership(user_id=me['user_id'],workspace_id=workspace.id,role='admin'));db.commit();workspace_id=workspace.id
    with TestClient(app) as other:
        login=_json_response(other.post('/api/login',json={'username':account[0],'password':account[1]}))
        other.headers.update({'X-CSRF-Token':login['csrf_token'],'X-Workspace-ID':workspace_id})
        with monkeypatch.context() as transport:
            unknown=run_timeout_unknown_journey(other,transport)
        unknown['workspace_id']=workspace_id
    # Snapshot final idempotent readback rather than the initial queued reply.
    # The original command body/nonce remain exact; replay is not a new request.
    with Session(engine()) as db:
        workspace=Workspace(name='独立预留后未发送恢复核对');db.add(workspace);db.flush()
        db.add(Membership(user_id=me['user_id'],workspace_id=workspace.id,role='admin'));db.commit();confirmed_workspace=workspace.id
    with TestClient(app) as other:
        login=_json_response(other.post('/api/login',json={'username':account[0],'password':account[1]}))
        other.headers.update({'X-CSRF-Token':login['csrf_token'],'X-Workspace-ID':confirmed_workspace})
        with monkeypatch.context() as transport:
            _confirmed_journey(other,transport)
        with Session(engine()) as db:
            rows=list(db.scalars(select(Memory).where(Memory.workspace_id==confirmed_workspace,
                Memory.data['dispatch_not_sent']['reservation_state'].as_string()=='confirmed')))
            assert len(rows)==1;record=rows[0];task_id=record.task_id;suggestion_id=record.id
        task_path='/api/workspace/tasks/'+task_id
        paths=[task_path,task_path+'/goal',task_path+'/experience/suggestions/'+suggestion_id]
        confirmed={'schema':'native-suggestion-confirmed-not-sent-proof-v130','workspace_id':confirmed_workspace,
            'task_id':task_id,'suggestion_id':suggestion_id,'commands':[],'readonly_requests':[],
            'readbacks':[{'path':path,'response':_json_response(other.get(path))} for path in paths],
            'send_count':0,'real_paid_calls':0}
    with Session(engine()) as db:
        workspace=Workspace(name='独立停止未知调用恢复核对');db.add(workspace);db.flush()
        db.add(Membership(user_id=me['user_id'],workspace_id=workspace.id,role='admin'));db.commit();stopped_workspace=workspace.id
    with TestClient(app) as other:
        login=_json_response(other.post('/api/login',json={'username':account[0],'password':account[1]}))
        other.headers.update({'X-CSRF-Token':login['csrf_token'],'X-Workspace-ID':stopped_workspace})
        with monkeypatch.context() as transport:
            stopped=run_timeout_unknown_journey(other,transport)
        path=stopped['task_path']+'/experience/suggestions/'+stopped['suggestion_id']
        command=old.action(_json_response(other.get(path)))
        result=_json_response(other.post(path+'/cancel',json=command))
        assert result['suggestion']['status']=='cancelled' and result['suggestion']['call']['status']=='unknown'
        stopped['commands'].append({'path':path+'/cancel','body':command,'response':result})
        for item in stopped['readonly_requests']:
            item['body']['expected_version']=result['suggestion']['task_version']
        stopped['workspace_id']=stopped_workspace
        stopped['schema']='native-suggestion-stopped-unknown-proof-v130'
        for item in stopped['readbacks']:
            item['response']=_json_response(other.get(item['path']))
    journeys=[quota,unknown,confirmed,stopped]
    for journey in journeys:
        client.headers['X-Workspace-ID']=journey['workspace_id']
        cold=[]
        for item in [*journey['commands'],*journey['readonly_requests']]:
            cold.append({'path':item['path'],'body':item['body'],
                'response':_json_response(client.post(item['path'],json=item['body']))})
        journey['cold_requests']=cold
        for item in journey['readbacks']:
            current=_json_response(client.get(item['path']))
            if not item['path'].endswith('/goal'):assert current==item['response']
            item['response']=current
    run=capture/('run-'+str(uuid4()));_private_dir(run,create=True)
    storage=settings().storage_dir.resolve(strict=True)
    assert not capture.is_relative_to(storage) and not storage.is_relative_to(capture) and not capture.is_relative_to(temporary)
    storage_snapshot=_tree_snapshot(storage);external_snapshot=_tree_snapshot(temporary)
    storage_backup=run/'evidence';external_backup=run/'external-synthetic-runtime'
    shutil.copytree(storage,storage_backup,symlinks=True,copy_function=shutil.copy2)
    shutil.copytree(temporary,external_backup,symlinks=True,copy_function=shutil.copy2)
    assert _tree_snapshot(storage_backup)==storage_snapshot and _tree_snapshot(external_backup)==external_snapshot
    database,dump=_dump_database(run,url)
    assert _tree_snapshot(storage)==storage_snapshot and _tree_snapshot(temporary)==external_snapshot
    value={'schema':'cloud-dispatch-durable-checkpoint-v130','synthetic_only':True,'actual_browser':False,
        'account':{'username':account[0],'password':account[1],'workspace_id':account[2]},'me':me,
        'source_db':{'database':url.database,'host':url.host,'port':url.port,'role':url.username,'storage':str(storage),'comment':comment},
        'journeys':journeys,'database_snapshot':database,'storage_snapshot':storage_snapshot,
        'external':{'original':str(temporary),'backup':str(external_backup),'snapshot':external_snapshot},
        'dump_path':str(dump),'dump_sha256':_file_sha(dump),'storage_backup':str(storage_backup)}
    checkpoint=run/'checkpoint.json';_json_private(checkpoint,value)
    _json_private(capture/'latest-v4.json',{'schema':'cloud-dispatch-durable-pointer-v130','checkpoint':str(checkpoint),'checkpoint_sha256':_file_sha(checkpoint)})
