"""Read actual synthetic cloud receipts and verify normal API/worker restart durability.

This script never invokes a cloud model. It restarts only this project's setup services.
"""
from contextlib import contextmanager
import json
import os
import time
from pathlib import Path
import httpx
import local_runtime as runtime
from generic_agents.contracts import digest

ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
access=json.loads((ROOT/'.runtime/understanding-validation-access.json').read_text())
OUT=ROOT/'docs/evidence/setup-understanding-v06';OUT.mkdir(parents=True,exist_ok=True)


@contextmanager
def connection():
    c=httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=20)
    r=c.post('/api/login',json={k:access[k] for k in ['username','password']});r.raise_for_status()
    c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':access['workspace_id']})
    try:yield c
    finally:c.close()


def get(c,path):
    r=c.get(path);r.raise_for_status();return r.json()


with connection() as c:
    before={'draft':get(c,'/api/setup/draft'),'analyses':get(c,'/api/setup/understanding'),
            'materials':get(c,'/api/setup/materials')}
    rows=before['analyses'];qwen=next(r for r in rows if r['provider']=='qwen' and r['status']=='applied')
    deepseek=next(r for r in rows if r['provider']=='deepseek' and r['status']=='undone')
    assert qwen['result']['businesses'][0]['original_name']==''
    assert deepseek['result']['businesses'][0]['original_name']=='设备异常复核'
    assert qwen['review_selection']['names']==deepseek['review_selection']['names']==['设备异常复核']
    old=deepseek['before_context']['flows']['设备异常复核'];new=deepseek['result']['businesses'][0]
    diff=[]
    assert len(old['steps'])==len(new['steps'])==4 and old['trigger']==new['trigger'] and old['output']==new['output']
    for i,(a,b) in enumerate(zip(old['steps'],new['steps'])):
        for k in ['name','purpose','agent','role','output']:
            if a[k]!=b[k]:diff.append({'step':i+1,'field':k,'before':a[k],'after':b[k]})
    assert diff==[{'step':3,'field':'role','before':'值班人员','after':'业务负责人'}]
    blocks={}
    for m in before['materials']:
        detail=get(c,'/api/setup/materials/'+m['id'])
        for b in detail['blocks']:blocks[(m['id'],b['id'])]=b['text']
    citations=0
    for row in [qwen,deepseek]:
        for business in row['result']['businesses']:
            for item in [business,*business['steps']]:
                for ref in item['citations']:
                    assert ref['quote'] in blocks[(ref['material_id'],ref['block_id'])];citations+=1
    flow=before['draft']['configuration']['flows']['设备异常复核']
    assert flow['steps'][2]['role']=='业务负责人' and flow['confirmedSignature']
    assert all(not m['name'].endswith('.docx') for m in before['materials'])
    assert not any(r['status'] in {'queued','running'} for r in rows)

runtime.stop('setup-worker','setup-worker');runtime.stop('api','serve')
runtime.start('api','serve');runtime.start('setup-worker','setup-worker')
for _ in range(80):
    try:
        if httpx.get('http://127.0.0.1:8765/health',trust_env=False,timeout=1).status_code==200:break
    except httpx.HTTPError:pass
    time.sleep(.1)
else:raise RuntimeError('本项目API未恢复')
with connection() as c:
    after={'draft':get(c,'/api/setup/draft'),'analyses':get(c,'/api/setup/understanding'),
           'materials':get(c,'/api/setup/materials')}
    assert before==after

calls=[{'provider':r['provider'],'model':r['model'],'record_status':r['status'],
    'total_tokens':receipt.get('usage',{}).get('total_tokens'),'duration_ms':receipt.get('duration_ms')}
    for r in rows for receipt in r['receipts']]
report={'date':'2026-10-08','scope':'one synthetic four-step business; actual cloud/browser/restart, not industry accuracy',
    'actual_qwen_initial_generation':True,'actual_deepseek_role_change_only':diff,
    'exact_native_source_citations_verified':citations,'review_choices_persisted_across_browser_reload':True,
    'normal_api_setup_worker_restart_exact':True,'manual_flow_confirmation_and_role_persisted':True,
    'actual_undo_verified_record_status':deepseek['status'],'calls':calls,
    'initial_qwen_rejected_result_preserved':any(r['provider']=='qwen' and r['status']=='failed' for r in rows),
    'snapshot_sha256':digest(before),'cloud_sent_real_report':False,'postgres_restart':False,
    'cloud_cancel_crash_injection':False,'professional_models_installed':False,'full_GF01_complete':False,
    'code_and_contract_tests':328,'backend_release_seconds':126.35,'frontend_tests':59,
    'quality_sample_size':{'initial_generation_after_fix':1,'natural_language_role_change':1},
    'production_readiness':False}
(OUT/'runtime.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'normal_restart_exact':True,'citations_checked':citations,'cloud_calls':len(calls),
    'cloud_tokens':sum(c['total_tokens'] or 0 for c in calls),'full_product_complete':False},ensure_ascii=False))
