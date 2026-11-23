"""One synthetic-only request; external POST is blocked after the first attempt."""
import copy, json, os, tempfile
import pytest
from pydantic import SecretStr
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.contracts import Manifest, digest
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Capability, CloudCall, Job
from generic_agents import sensor_native_analysis as service, cloud_business_reasoning_v197 as transport
from tests.test_sensor_native_packet_pg import setup_native
from tests.test_sensor_native_analysis_pg import router, fake_provider


def write_private(path, values):
    target=Path(path);fd,name=tempfile.mkstemp(prefix='.synthetic-evidence-',dir=target.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w') as out:
            json.dump(values,out,ensure_ascii=False);out.flush();os.fsync(out.fileno())
        os.replace(name,target)
    finally:
        if os.path.exists(name):os.unlink(name)


@pytest.fixture
def device():
    # Documentation-only address: the read-only register transport is replaced below.
    return '10.255.255.253', 15020, []


def run_journey(client, account, device, monkeypatch, mode, evidence_dir):
    journal_path=evidence_dir/'journal.json'; evidence_path=evidence_dir/'evidence.json'
    def journal(**values):write_private(journal_path,values)
    monkeypatch.setattr(settings(),'cloud_allowed_providers','deepseek')
    if mode!='real':
        monkeypatch.setenv('DEEPSEEK_API_KEY','synthetic-key-never-sent')
        monkeypatch.setattr(settings(),'deepseek_api_key',SecretStr('synthetic-key-never-sent'))
        fake_provider(monkeypatch,invalid=(mode=='invalid'))
    original_client=transport.httpx.Client;attempts=[]
    class OnceClient:
        def __init__(self,*args,**kwargs):self._inner=original_client(*args,**kwargs)
        def __enter__(self):self._inner.__enter__();return self
        def __exit__(self,*args):return self._inner.__exit__(*args)
        def post(self,url,*args,**kwargs):
            if attempts:raise RuntimeError('second outbound attempt forbidden')
            if str(url)!='https://api.deepseek.com/chat/completions':raise RuntimeError('unexpected outbound endpoint')
            attempts.append(1);journal(phase='outbound_attempt_started',request_id=rid,outbound_attempts=1)
            return self._inner.post(url,*args,**kwargs)
    monkeypatch.setattr(transport.httpx,'Client',OnceClient)
    rid=str(uuid4());journal(phase='setup',request_id=rid,outbound_attempts=0,provider_mode=mode)
    reads=[]
    def synthetic_register_read(*args):
        reads.append(1);return [37]
    monkeypatch.setattr('generic_agents.modbus_readonly_transport.read_registers',synthetic_register_read)
    task,_=setup_native(client,account,device,monkeypatch)
    assert reads==[1], 'synthetic observation must be read exactly once'
    base='/api/sensor-native/tasks/'+task['id'];ctx=client.get(base+'/context').json();row=ctx['records'][0]
    pkt=client.post(base+'/packets',json={'request_id':str(uuid4()),'expected_task_version':ctx['task_version'],'selection':[{'kind':row['kind'],'id':row['id'],'record_hash':row['record_hash']}]})
    assert pkt.status_code==200,'synthetic packet rejected'
    spec=Manifest(kind='foundation_model',namespace='synthetic_real_e2e',name='deepseek',version='1.0.0',owner='test',description='合成模型',allowed_agent_roles=['business'],execution={'provider':'deepseek','model':'deepseek-v4-pro','max_tokens':512,'thinking_mode':'disabled','secret_ref':'DEEPSEEK_API_KEY'},data_policy={'cloud_allowed':True}).model_dump(mode='json');h=digest(spec)
    with Session(engine(),expire_on_commit=False) as db:
        cap=Capability(workspace_id=account[2],kind='foundation_model',namespace='synthetic_real_e2e',name='deepseek',release_version='1.0.0',status='published',content_hash=h,validated_hash=h,data=spec);db.add(cap);db.commit();model=cap.id
    preview=client.post(base+'/analyses/preview',json={'request_id':str(uuid4()),'packet_id':pkt.json()['packet']['id'],'model_id':model,'max_output_tokens':256,'max_cost_fen':None})
    assert preview.status_code==200,'synthetic preview refused'
    view=preview.json();assert view['quote']['user_limit_fen'] is None and view['model']['provider']=='deepseek'
    url=base+'/analyses/'+view['preview_id']+'/send';body={'request_id':rid,'preview_hash':view['preview_hash'],'consent':True}
    journal(phase='before_submit',request_id=rid,outbound_attempts=0,preview_id=view['preview_id'])
    def snapshot(phase,**extra):
        with Session(engine()) as db:
            calls=list(db.scalars(select(CloudCall).where(CloudCall.workspace_id==account[2])))
            jobs=list(db.scalars(select(Job).where(Job.workspace_id==account[2],Job.idempotency_key==service.send_key(rid))))
            evidence={'phase':phase,'request_id':rid,'provider_mode':mode,'outbound_attempts':len(attempts),
                'cloud_calls':[{'id':c.id,'status':c.status,'reserved_tokens':c.reserved_tokens,'charged_tokens':c.charged_tokens,'receipt':copy.deepcopy(c.data.get('receipt'))} for c in calls],
                'jobs':[{'id':j.id,'kind':j.kind,'state':j.data.get('state'),'error':j.data.get('error'),'failure_reason':service.failure_reason(j.data) if j.data.get('state')=='response_invalid' else None,'capture':copy.deepcopy(j.data.get('capture')),'output_schema':copy.deepcopy(j.data.get('snapshot',{}).get('output_schema'))} for j in jobs],**extra}
            write_private(evidence_path,evidence)
            return evidence
    try:
        first=client.post(url,json=body);assert first.status_code==200,'initial submit refused'
        journal(phase='queued_before_worker',request_id=rid,outbound_attempts=0)
        service.tick(account[2])
        evidence=snapshot('before_replay');assert len(evidence['jobs'])==1,'original request not durably recorded; replay forbidden'
        original_state=evidence['jobs'][0]['state'];first_count=len(evidence['cloud_calls'])
        replay=client.post(url,json=body);assert replay.status_code==200,'same request replay refused'
        replay_value=replay.json();assert replay_value.get('state')==original_state
        if original_state=='response_invalid':assert replay_value['analysis']['failure_reason']==evidence['jobs'][0]['failure_reason']
        # This checks the worker cannot claim the original a second time.
        assert service.tick(account[2])==0,'same request created another runnable dispatch'
        after=snapshot('replay_checked',initial_state=original_state,replay_status=replay.status_code)
        assert len(after['jobs'])==1 and len(after['cloud_calls'])==first_count and len(attempts)<=1
        journal(phase='replay_verified',request_id=rid,state=original_state,outbound_attempts=len(attempts),cloud_call_count=first_count)
        if mode=='invalid':assert original_state=='response_invalid' and len(attempts)==1 and first_count==1
        elif mode=='valid':assert original_state=='completed' and len(attempts)==1 and first_count==1
        else:assert original_state=='completed','real model result did not pass validation; original evidence saved'
    except BaseException:
        try:snapshot('stopped_no_retry')
        finally:raise


@pytest.mark.parametrize('mode',['invalid','valid'])
def test_synthetic_model_replay_with_fake_provider(client,account,device,monkeypatch,tmp_path,mode):
    run_journey(client,account,device,monkeypatch,mode,tmp_path)
    evidence=json.loads((tmp_path/'evidence.json').read_text())
    assert evidence['jobs'][0]['capture']['raw_base64'] and evidence['jobs'][0]['output_schema']
    if mode=='invalid':assert evidence['jobs'][0]['failure_reason']['code']=='content_not_json'
    assert (tmp_path/'evidence.json').stat().st_mode & 0o777 == 0o600
    assert (tmp_path/'journal.json').stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.getenv('GA_RUN_SYNTHETIC_REAL_DEEPSEEK')!='1',reason='explicit synthetic-only paid run required')
def test_synthetic_model_replay_with_real_deepseek(client,account,device,monkeypatch):
    # Explicit env, never implicitly read live project credentials from .env.
    if not os.getenv('DEEPSEEK_API_KEY'):pytest.fail('explicit environment credential required')
    raw=os.getenv('GA_SYNTHETIC_EVIDENCE_DIR','')
    if not raw:pytest.fail('explicit private evidence directory required')
    parent=Path(raw)
    if not parent.is_absolute() or parent.resolve()!=parent or not parent.is_dir():pytest.fail('evidence directory must be an existing absolute non-symlink path')
    st=parent.stat()
    if st.st_uid!=os.getuid() or st.st_mode & 0o077:pytest.fail('evidence directory must be private and owned by current user')
    evidence_dir=parent/('synthetic-'+str(uuid4()))
    evidence_dir.mkdir(mode=0o700)
    run_journey(client,account,device,monkeypatch,'real',evidence_dir)
