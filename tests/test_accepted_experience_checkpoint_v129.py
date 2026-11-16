"""Private capture of actual graph acceptance -> reviewed experience.

Root only. Synthetic document/provider transport is not semantic model proof.
"""
import httpx
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from uuid import uuid4

from tests.test_graph_wait_journey_v118 import (
    _RELEASES, waiting_release, graph_seed, v7_scene, reading_scene,
    local_runtime, full_source_scene,
)
from tests.test_workflow_graph_goal_journey_v126 import run_goal_journey
from tests.test_native_tool_experience_v129 import run_native_tool_journey
from tests.test_business_tools import service
from tests.test_tool_accepted_experience_v129 import run_experience_for_accepted
from tests.test_observation_submission_checkpoint_v128 import (
    _owned_database, _dump_database, _tree_snapshot, _private_dir, _json_private,
    _file_sha, _json_response,
)
from generic_agents.config import settings
from generic_agents import gateway
from generic_agents.api import app
from generic_agents.db import engine
from generic_agents.models import Workspace, Membership
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

# Preserve the guarded real HTTP client before the document transport fixture
# temporarily replaces it for its exact synthetic provider contract.
_HTTP_CLIENT = httpx.Client


def test_capture_actual_accepted_tool_experience(waiting_release, tmp_path_factory, monkeypatch, service):
    capture=Path(os.environ['GA_V129_CAPTURE_DIR'])
    assert capture.is_absolute() and capture.name=='full-semantic-graph-v129' and capture.parent.name=='.runtime'
    _private_dir(capture);assert capture.resolve(strict=True)==capture
    temporary=tmp_path_factory.getbasetemp().resolve(strict=True)
    assert re.fullmatch(r'pytest-\d+',temporary.name) and temporary.parent==capture/'source-runtime'
    assert stat.S_IMODE(temporary.parent.stat().st_mode)==0o700
    marker=temporary.parent/'ROOT_OWNED.json'
    assert not marker.is_symlink() and stat.S_IMODE(marker.stat().st_mode)==0o600
    assert json.loads(marker.read_text())=={'schema':'v129-owned-source-runtime','synthetic_only':True,'purpose':'v129-durable-checkpoint'}
    url,comment=_owned_database();client=waiting_release['client']
    proof=run_goal_journey(waiting_release,tmp_path_factory.mktemp('graph'))
    experience=run_experience_for_accepted(client,proof)
    me=_json_response(client.get('/api/me'))
    accounts=[value['account'] for value in _RELEASES.values() if value['account'][0]==me['username'] and value['account'][2]==me['workspace_id']]
    assert len(accounts)==1 and len(accounts[0])==3
    username,password,workspace=accounts[0]
    # The inherited document fixture has a narrow fake settings/client object.
    # Native HTTP registration/execution uses the real connector guards and
    # guarded HTTP client; only each native model response stays synthetic.
    with monkeypatch.context() as native_transport:
        native_transport.setattr(gateway, 'settings', settings)
        native_transport.setattr(httpx, 'Client', _HTTP_CLIENT)
        native={}
        for state in ('accepted','rejected','cancelled'):
            # Test identity provisioning only, matching the owned account fixture.
            # Every business/plan/job/result still comes from its public API.
            # A separate workspace preserves the existing adopted configuration.
            with Session(engine()) as db:
                ws=Workspace(name='独立原计划经验核对 '+state);db.add(ws);db.flush()
                db.add(Membership(user_id=me['user_id'],workspace_id=ws.id,role='admin'))
                db.commit();native_workspace=ws.id
            with TestClient(app) as native_client:
                login=_json_response(native_client.post('/api/login',json={'username':username,'password':password}))
                native_client.headers.update({'X-CSRF-Token':login['csrf_token'],'X-Workspace-ID':native_workspace})
                identity=_json_response(native_client.get('/api/me'))
                assert identity['user_id']==me['user_id'] and identity['workspace_id']==native_workspace
                value=run_native_tool_journey(native_client,native_transport,service,settlement=state)
                value['workspace_id']=native_workspace
                value['experience']['workspace_id']=native_workspace
                native[state]=value
    assert service['calls']==2 and all(value['real_paid_calls']==0 for value in native.values())
    experience['workspace_id']=workspace
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
    value={'schema':'accepted-experience-durable-checkpoint-v129','synthetic_only':True,'actual_browser':False,
        'account':{'username':username,'password':password,'workspace_id':workspace},'me':me,
        'source_db':{'database':url.database,'host':url.host,'port':url.port,'role':url.username,'storage':str(storage),'comment':comment},
        'proof':proof,'experience':experience,'native_experience':native,'database_snapshot':database,'storage_snapshot':storage_snapshot,
        'external':{'original':str(temporary),'backup':str(external_backup),'snapshot':external_snapshot},
        'dump_path':str(dump),'dump_sha256':_file_sha(dump),'storage_backup':str(storage_backup)}
    checkpoint=run/'checkpoint.json';_json_private(checkpoint,value)
    _json_private(capture/'latest.json',{'schema':'accepted-experience-durable-pointer-v129','checkpoint':str(checkpoint),'checkpoint_sha256':_file_sha(checkpoint)})
