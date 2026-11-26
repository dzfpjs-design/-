"""Real worker process with synthetic-only source substitutes and loopback provider.

The fixture has no real evaluation or approved source originals. Its explicitly
substituted source checks are inherited by the child via a private test file;
this does not establish actual source provenance or production readiness.
"""
import inspect
import json
import os
import pickle
import subprocess
import sys
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall
from generic_agents import same_goal_advice_cloud_claim_test_v2 as claims
from generic_agents import same_goal_advice_dispatch_control_candidate_v2 as control
from generic_agents import same_goal_advice_dispatch_delegation_candidate_v2 as delegation
from generic_agents import same_goal_advice_first_response_storage_candidate_v2 as storage
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as document
from generic_agents import same_goal_advice_response_reconcile_candidate_v2 as reconcile
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
from tests.synthetic_deepseek_loopback_v2 import provider
from tests.test_advice_dispatch_delegation_candidate_v2_pg import pending
from tests.test_advice_v2_deepseek_adapter_candidate_pg import private_response_spool
from tests.test_same_goal_advice_loopback_dispatch_candidate_v2_pg import synthetic_source_expiry
from tests.test_same_goal_advice_runtime_v308 import _opinion
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def test_real_worker_process_loopback_once_and_saved_result(pending,prepared_preview,private_response_spool,synthetic_source_expiry,tmp_path):
    s,created,body=pending;p=s['principal'];frame=prepared_preview[3]
    resolved=inspect.getclosurevars(document.sources.resolve_documents).nonlocals['resolved']
    with Session(engine()) as db:
        claims.install(db)
        if db.scalar(text("SELECT to_regclass('public.ga_test_advice_v2_first_response')")) is None:storage.install(db)
        db.commit()
    with Session(engine()) as db:
        grant=delegation.begin_with_delegation(db,p,s['task_id'],created['plan_id'],body)
        control.enqueue(db,p,s['task_id'],created['plan_id'],grant['delegation_id']);db.commit()
    with Session(engine()) as db:
        event=db.scalar(select(Audit).where(Audit.action==preview.ACTION))
        opinion=_opinion(event.data['preview_record']['cloud_request']['decision_input'])
    fixture_path=tmp_path/'synthetic-fixture.pkl'
    fixture_path.write_bytes(pickle.dumps((frame,resolved)))
    os.chmod(fixture_path,0o600)
    env=os.environ.copy()
    env.update(GA_V2_USER_SEND_TEST_ENABLED='true',GA_V2_USER_SEND_WORKER_ENABLED='true',
        GA_V2_RESPONSE_SPOOL_ENABLED='true',GA_V2_RESPONSE_SPOOL_ROOT=str(settings().v2_response_spool_root),
        GA_STORAGE_DIR=str(settings().storage_dir),SYNTHETIC_FIXTURE_PATH=str(fixture_path),
        DEEPSEEK_API_KEY='',DASHSCOPE_API_KEY='')
    program='''
import copy,json,os,pickle
from datetime import timedelta
from sqlalchemy import func,select
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
from generic_agents import same_goal_advice_prepare_document_candidate_v2 as document
from generic_agents import same_goal_advice_start_safety_candidate_v2 as safety
from generic_agents import same_goal_advice_dispatch_permit_candidate_v2 as permit
from generic_agents import same_goal_advice_user_send_worker_candidate_v2 as worker
from generic_agents.db import engine
with open(os.environ['SYNTHETIC_FIXTURE_PATH'],'rb') as f:frame,resolved=pickle.load(f)
preview.frame_builder.build_current=lambda *_,**__:copy.deepcopy(frame)
preview._verify_evaluation=lambda *_:None
document.sources.resolve_documents=lambda *_:copy.deepcopy(resolved)
safety._lock_approved_sources=lambda *_:None
safety.check_freshness=lambda *_:None
permit._source_expiry=lambda db,*_:db.scalar(select(func.clock_timestamp()))+timedelta(minutes=5)
first=worker.tick()
engine().dispose()
second=worker.tick()
print('WORKER_TEST_RESULT='+json.dumps([first,second]),flush=True)
'''
    with provider(int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT']),content=json.dumps(opinion),usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        result=subprocess.run([sys.executable,'-c',program],env=env,capture_output=True,text=True,timeout=90)
        assert result.returncode==0, result.stderr[-3000:]
        output=[line for line in result.stdout.splitlines() if line.startswith('WORKER_TEST_RESULT=')]
        assert len(output)==1
        first,second=json.loads(output[0].split('=',1)[1])
        assert first=={'claimed':1,'failed':0} and second=={'claimed':0,'failed':0}
        assert len(server.requests)==1
    engine().dispose()
    with Session(engine()) as db:
        call=db.scalar(select(CloudCall))
        assert call is not None and db.scalar(select(func.count()).select_from(CloudCall))==1
        intent=db.scalar(select(Audit).where(Audit.action=='same_goal_advice.unlimited_transport_intent_test_v2'))
        assert intent is not None
        result=reconcile.read_result(db,p,call.id,intent.id)
        assert result['model_value']==opinion
