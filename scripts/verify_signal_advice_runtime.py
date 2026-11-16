"""Own public QA evidence; real cloud call is authorized and sent through the UI."""
import json,sys
from pathlib import Path
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.contracts import digest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.verify_signal_runtime import snapshot,originals
OUT=ROOT/'docs/evidence/signal-advice-v06';OUT.mkdir(parents=True,exist_ok=True)
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def read(n):return json.loads((OUT/(n+'.json')).read_text())
def state(w):return {'own':snapshot(w),'other':snapshot(w,True),'own_originals':originals(w),'other_originals':originals(w,True)}
def prepare(a,c):
    if (OUT/'prepared.json').exists():raise RuntimeError('已准备，继续现有检查点，不重复登记')
    before=state(a['workspace_id']);save('before',before)
    # Find the exact verified original task from the existing M23 record, never add users/objects/devices.
    with Session(engine()) as db:
        instance=db.scalar(select(m.DomainInstance).where(m.DomainInstance.workspace_id==a['workspace_id'],m.DomainInstance.data['name'].as_string()=='地图影像区域核查'))
        signals=list(db.scalars(select(m.PerceptionSignal).where(m.PerceptionSignal.workspace_id==a['workspace_id'],m.PerceptionSignal.instance_id==instance.id).order_by(m.PerceptionSignal.created_at)))
        signal=next(s for s in signals if s.data.get('source_task_link') and s.data.get('business_review',{}).get('decision')=='not_abnormal')
        task=db.get(m.Task,signal.data['source_task_link']['task_id']);task_hash=digest(task.data)
        assert task.status=='workflow_completed' and '公开' in task.title
    manifest={'kind':'foundation_model','namespace':'public_engineering_qa','name':'public_signal_advice_qwen','version':'1.0.0',
        'owner':'公开工程核查','description':'只对公开合成图像的候选与公开QA说明生成处置建议；原始媒体不外发',
        'allowed_agent_roles':['business'],'specification':{},'execution':{'provider':'qwen','model':'qwen-plus','secret_ref':'DASHSCOPE_API_KEY','max_tokens':2048},
        'data_policy':{'cloud_allowed':True},'budget':{'max_attempts':1,'max_concurrency':1}}
    response=c.post('/capabilities',json=manifest);response.raise_for_status();cap=response.json()
    for step in ['validate','publish']:
        response=c.post('/capabilities/'+cap['id']+'/'+step,json={'expected_version':cap['version'],'reason':'现有管理员在公开QA范围登记云端基础业务推理模型；每次外发仍需准确预览与授权，不授予设备权限'})
        response.raise_for_status();cap=response.json()
    save('prepared',{'instance_id':instance.id,'signal_id':signal.id,'task_id':task.id,'task_hash':task_hash,'entity_id':signal.entity_id,'model_profile_id':cap['id'],'model':'qwen-plus','media_bytes_sent':0,'new_accounts':0,'new_permissions':0})
    print('Existing public QA signal and Qwen profile ready; no cloud call made')
def verify(a,c,phase):
    prepared=read('prepared');response=c.get('/signal-advice',params={'instance_id':prepared['instance_id'],'signal_id':prepared['signal_id']});response.raise_for_status();rows=response.json()
    assert len(rows)==1, 'Only the intended once-authorized real request is expected'
    advice=rows[0];assert advice['status']=='applied' and advice['dispatch']['route']=='evidence_check'
    assert advice['draft']['criteria']['purpose']=='人工核对公开合成地图关联的档案、登记数据与影像候选；不判断现场状态。'
    response=c.get('/evidence-assessments/'+advice['dispatch']['assessment_id']);response.raise_for_status();assessment=response.json();assert assessment['status']=='completed' and assessment['result']['cloud_calls']==0
    response=c.get('/signal-advice/'+advice['id']+'/export');response.raise_for_status();assert response.json()==advice
    save(phase+'-advice',advice);save(phase+'-assessment',assessment)
    with Session(engine()) as db:
        assert digest(db.get(m.Task,prepared['task_id']).data)==prepared['task_hash']
        calls=list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id==a['workspace_id'])))
    before=read('before');after=state(a['workspace_id'])
    for key in ['own','other','own_originals','other_originals']:
        assert all(after[key].get(k)==v for k,v in before[key].items()),key+' old record changed'
    if phase=='restart':assert after==read('before-restart'),'Normal restart changed durable records'
    receipt=advice['model_receipt'];usage=receipt.get('usage',{})
    check={'passed':True,'original_task_unchanged':True,'old_own_records_unchanged':len(before['own']),
        'own_records':len(after['own']),'other_records_unchanged':len(after['other']),'old_originals_unchanged':True,
        'cloud_calls':len(calls),'provider':receipt.get('provider'),'model':receipt.get('model'),'usage':usage,'duration_ms':receipt.get('duration_ms'),
        'raw_media_sent':False,'entity_state_updated':False,'device_action_executed':False,'ordinary_device_worker_started':False,
        'query_records':assessment['result']['objects'][0]['saved_records'],'query_processing_records':assessment['result']['objects'][0]['processing_records'],'original_files':len(after['own_originals']), 'new_inference':0,'model_quality_validated':False,'production_ready':False}
    save(phase+'-verification',check)
    if phase=='before-restart':save('before-restart',after)
    print(json.dumps(check,ensure_ascii=False))
def main():
    mode=sys.argv[1];a=json.loads((ROOT/'.runtime/wms-access.json').read_text())
    with httpx.Client(base_url='http://127.0.0.1:8765/api',trust_env=False,timeout=40) as c:
        result=c.post('/login',json={k:a[k] for k in ['username','password']});result.raise_for_status();c.headers.update({'X-Workspace-ID':a['workspace_id'],'X-CSRF-Token':result.json()['csrf_token']})
        if mode=='prepare':prepare(a,c)
        else:verify(a,c,mode)
if __name__=='__main__':main()
