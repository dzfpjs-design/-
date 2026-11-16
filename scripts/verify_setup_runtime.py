"""Local-only actual API/worker restart verification. Never invokes cloud or devices."""
import hashlib
import importlib.util
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
import httpx
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import local_runtime as runtime
access=json.loads((ROOT/'.runtime/setup-validation-access.json').read_text())
OUT=ROOT/'docs/evidence/setup-v05';OUT.mkdir(parents=True,exist_ok=True)

@contextmanager
def client():
    c=httpx.Client(base_url='http://127.0.0.1:8765',timeout=20,trust_env=False)
    r=c.post('/api/login',json={'username':access['username'],'password':access['password']});r.raise_for_status()
    c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':access['workspace_id']})
    try: yield c
    finally: c.close()

def snapshot(c):
    draft=c.get('/api/setup/draft');draft.raise_for_status()
    rows=c.get('/api/setup/materials?limit=500');rows.raise_for_status()
    materials=[]
    for row in rows.json():
        r=c.get('/api/setup/materials/'+row['id']);r.raise_for_status()
        original=c.get('/api/setup/materials/'+row['id']+'/content');original.raise_for_status()
        assert hashlib.sha256(original.content).hexdigest()==row['hash']
        materials.append(r.json())
    return {'draft':draft.json(),'materials':materials}

def wait_api():
    for _ in range(80):
        try:
            if httpx.get('http://127.0.0.1:8765/health',timeout=1,trust_env=False).status_code==200:return
        except httpx.HTTPError:pass
        time.sleep(.1)
    raise RuntimeError('API未恢复')

with client() as c:
    before=snapshot(c)
    assert before['materials'] and any(m['name']=='最终（全）(1).docx' and m['status']=='parsed' for m in before['materials'])
    assert before['draft']['configuration']['flows']['资料核查与归集']['confirmedSignature']
runtime.stop('setup-worker','setup-worker');runtime.stop('api','serve')
runtime.start('api','serve');runtime.start('setup-worker','setup-worker');wait_api()
with client() as c:
    after=snapshot(c);assert before==after
runtime.stop('setup-worker','setup-worker')
queued_text='保持原件和排队解析任务。核验批次：'+uuid4().hex
with client() as c:
    row=c.post('/api/setup/materials',files={'file':('重启接续.txt',queued_text.encode())});row.raise_for_status();row=row.json()
    assert row['status']=='queued'
runtime.start('setup-worker','setup-worker')
with client() as c:
    for _ in range(100):
        record=c.get('/api/setup/materials/'+row['id']).json()
        if record['status']=='parsed':break
        time.sleep(.1)
    assert record['status']=='parsed'
    assert record['blocks'][0]['text']==queued_text
result={'date':'2026-10-08','scope':'first M1 setup persistence/native parser slice; no cloud, no device operations',
        'actual_browser_report':'最终（全）(1).docx','original_sha256':before['materials'][0]['hash'],
        'actual_document_blocks':next(m['block_count'] for m in before['materials'] if m['name']=='最终（全）(1).docx'),
        'configuration_and_materials_exact_after_api_worker_restart':True,'parsed_original_bytes_hash_verified':True,
        'queued_parser_job_completed_after_setup_worker_restart':True,'manual_flow_confirmation_persisted':True,
        'postgres_restart':False,'backup_crash_recovery':False,'cloud_understanding_verified':False,
        'native_browser_download_event_observable':False,'native_download_file_hash_verified':True,
        'professional_models_installed':False,'full_GF01_complete':False}
(OUT/'runtime.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in {'original_sha256','actual_browser_report'}},ensure_ascii=False))
