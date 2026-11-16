"""Opt-in disposable PostgreSQL/browser proof for deterministic local methods.
No provider registration, key loading, CloudCall or specialist execution claim.
A/B unknown numeric units test refusal; A2/B2 prove nonnumeric consumption.
Source A/A2/CAP acceptance is API seeded, not human browser acceptance.
"""
import json
from hashlib import sha256
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.instance_compiler import signature_value
from generic_agents.models import CloudCall, Entity, Job, Membership, Memory, Task, User
from generic_agents.security import password_hash
from tests.browser_mqtt_daily_harness import durable
from tests.test_instance_workspace import configuration, save, enable
from tests.test_mqtt_supervisor_protocol import stop_child, wait_for
from tests.test_native_cases import ready, accept
from tests.test_native_goals import define
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/evidence/local-method-v13/browser'
ACCESS=ROOT/'.runtime/local-method-v13-browser-access.json'
CONTROL=ROOT/'.runtime/local-method-v13-browser-control.json'
def write(name,value):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n')

def seed(client):
    c = configuration()
    c['instanceProfile']['goal'] = '独立合成档案核验，不认定现场'
    for flow in c['flows'].values():
        flow['confirmedSignature'] = json.dumps(signature_value(c, flow), ensure_ascii=False)
    revision = save(client, c, review_automation=True)
    member = client.get('/api/instances/members').json()[0]
    def fields(mixed_kind):
        return [{'label':'工程对象名称','kind':'文本','required':True},
                {'label':'登记数量','kind':'整数','required':True},
                {'label':'档案标志','kind':'是否','required':True},
                {'label':'混合登记字段','kind':mixed_kind,'required':True}]
    body = {'revision':revision,'role_bindings':{'核验负责人':member['id']},'objects':[
        {'name':'文本登记类型','fields':fields('文本')},
        {'name':'布尔登记类型','fields':fields('是否')},
        {'name':'六字段容量类型','fields':[
            {'label':'必要字段'+str(i),'kind':'文本','required':True} for i in range(6)]}]}
    check = client.post('/api/instances/check', json=body)
    assert check.status_code == 200, check.text
    response = client.post('/api/instances', json={**body,'confirmed_configuration_hash':check.json()['configuration_hash'],'confirm_configuration':True})
    assert response.status_code == 201, response.text
    release = response.json(); enable(client, release)
    kinds = release['snapshot']['object_types']; tasks = {}; objects = {}
    for letter in ('A','B','CAP','A2','B2'):
        selected = kinds[:2] if letter != 'CAP' else kinds[2:]
        rows = []
        for index, kind in enumerate(selected):
            values = {}
            for field in kind['fields']:
                if field['kind'] == '整数': value = (17 if letter.startswith('A') else 51) + index * 17
                elif field['kind'] == '是否': value = True if field['label'] == '档案标志' else False
                else: value = '独立当前档案_' + letter + '_' + str(index) + '_' + field['label']
                values[field['key']] = value
            result = client.post('/api/instances/'+release['instance_id']+'/objects',json={
                'release_id':release['id'],'object_type':kind['key'],'attributes':values})
            assert result.status_code == 201, result.text
            rows.append(result.json())
        objects[letter] = rows
        keys = ([field['key'] for field in kinds[0]['fields'][1:]] if letter != 'CAP'
                else [field['key'] for field in kinds[2]['fields']])
        if letter in ('A2','B2'): keys = keys[1:]
        result = client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json={
            'release_id':release['id'],'business_id':release['available_business_ids'][0],
            'entity_ids':[row['id'] for row in rows],'title':'本地清单工程任务_'+letter,'request_id':str(uuid4())})
        assert result.status_code == 201, result.text
        task = result.json()
        criteria = [{'id':'enough','kind':'evidence_sufficient','description':'当前原档案充分且无冲突'},
                    {'id':'human','kind':'human_confirmation','description':'原流程结束后明确人工验收'}]
        if letter != 'CAP':
            criteria.append({'id':'flag','kind':'field_value','field':keys[0] if letter in ('A2','B2') else keys[1],'operator':'eq',
                             'expected':True,'description':'档案标志需要满足登记标准'})
        spec = {'purpose':'核查各对象独立当前档案，保留未知与原流程','entity_ids':[row['id'] for row in rows],
                'required_fields':keys,'criteria':criteria,
                'subgoals':[{'id':'check','description':'完成已确认的原任务资料核验','criterion_ids':[x['id'] for x in criteria]}],
                'evidence':{'source':'archive','minimum_verification':'recorded','time':{'mode':'archive','timezone':'Asia/Shanghai'}},
                'observation_mode':'existing_only'}
        define(client,'/api/workspace/tasks/'+task['id']+'/goal',{
            'request_id':str(uuid4()),'expected_version':task['version'],'reason':'独立合成档案核验，不认定现场', 'goal_spec':spec})
        ready(client, task)
        if letter in ('A','CAP','A2'):
            accepted = accept(client, task); assert accepted.status_code == 200, accepted.text
        tasks[letter] = {'id':task['id'],'entity_ids':[row['id'] for row in rows],'fields':keys}
    return release, tasks, objects

def test_browser_local_method(client, account):
    if os.getenv('GA_LOCAL_METHOD_BROWSER')!='1': pytest.skip('Explicit disposable browser only')
    assert engine().url.database.startswith('ga_test_') and not ACCESS.exists()
    frozen=json.loads((OUT.parent/'source-freeze.json').read_text())
    assert digest(frozen['backend_files']) == frozen['backend_bundle_hash']
    assert {item['path'] for item in frozen['backend_files']} == {
        str(path.relative_to(ROOT)) for path in (ROOT/'src/generic_agents').rglob('*.py')}
    assert all(sha256((ROOT/item['path']).read_bytes()).hexdigest()==item['sha256']
               for item in frozen['backend_files']), 'Backend changed after runtime source freeze'
    ui=json.loads((ROOT/'.runtime/local-method-v13-frontend-source.json').read_text())
    assert ui['asset_sha256']==frozen['ui_asset_sha256']
    assert sha256((ROOT/'.runtime/builds/local-method-v13/assets'/ui['asset']).read_bytes()).hexdigest()==ui['asset_sha256']
    assert all(sha256((ROOT/item['path']).read_bytes()).hexdigest()==item['sha256'] for item in ui['files'])
    release,tasks,objects=seed(client)
    with Session(engine()) as db:
        assert not db.scalar(select(CloudCall.id))
        viewer_name,viewer_password='viewer_'+uuid4().hex[:10],uuid4().hex
        viewer=User(username=viewer_name,password_hash=password_hash(viewer_password));db.add(viewer);db.flush()
        db.add(Membership(user_id=viewer.id,workspace_id=account[2],role='viewer'));db.commit()
        archives={letter:[{'id':obj['id'],'hash':digest(db.get(Entity,obj['id']).data)} for obj in rows] for letter,rows in objects.items()}
        original_goals={letter:{'goal_spec_hash':digest(db.get(Task,t['id']).data['goal_spec']),
                               'acceptance_hash':db.get(Task,t['id']).data.get('native_goal_acceptance_hash')}
                        for letter,t in tasks.items()}
    write('input-freeze',{'classification':'real_registered_synthetic_archives_not_domain_quality',
        'tasks':tasks,'archives':archives,'original_goals':original_goals,
        'source_A_A2_CAP_acceptance_API_seeded':True,'B_B2_not_accepted':True,
        'cloud_count_before':0,'foundation_model_profiles':0})
    with socket.socket() as sock: sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    url='http://localhost:'+str(port)
    env={'PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8','GA_ALLOWED_ORIGINS':url,
        'GA_COOKIE_SECURE':'false','GA_DATABASE_URL':settings().database_url,'GA_STORAGE_DIR':str(settings().storage_dir),
        'GA_SETUP_BUILD_DIR':str(ROOT/'.runtime/builds/local-method-v13'),
        'GA_REDIS_URL':'redis://127.0.0.1:6379/0', 'GA_CLOUD_DAILY_CALL_LIMIT':'0',
        'DASHSCOPE_API_KEY':'', 'DEEPSEEK_API_KEY':'',
        'PYTHONPATH':str(ROOT)+os.pathsep+str(ROOT/'src'),
        'GA_LOCAL_METHOD_PROOF_DIR':str(OUT)}
    isolated_cwd=TemporaryDirectory(prefix='ga-local-method-no-dotenv-')
    children,logs=[],[]
    def start(command):
        log=(ROOT/('.runtime/local-method-v13-browser-'+command+'.log')).open('ab');logs.append(log)
        args=[sys.executable,'-m','tests.local_method_runtime',command]
        if command=='serve':args+=['--host','127.0.0.1','--port',str(port)]
        child=subprocess.Popen(args,cwd=isolated_cwd.name,env=env,stdout=log,stderr=log);children.append(child)
        wait_for(lambda:(OUT/('runtime-config-'+command+'-'+str(child.pid)+'.json')).exists(),
                 [child],timeout=10,description='effective no-cloud child configuration')
        if command=='serve':
            def alive():
                try:return httpx.get(url+'/health',timeout=1,trust_env=False).is_success
                except httpx.HTTPError:return False
            wait_for(alive,[child],timeout=15,description='local-method isolated API')
        return child
    try:
        api,worker=start('serve'),start('setup-worker')
        with os.fdopen(os.open(ACCESS,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:
            json.dump({'url':url,'username':account[0],'password':account[1],'viewer_username':viewer_name,
                'viewer_password':viewer_password,'workspace_id':account[2],'instance_id':release['instance_id'],'tasks':tasks},f)
        write('prepared',{'API_worker_ready':True,'tasks':tasks,'instance_id':release['instance_id'],
                         'source_A_A2_CAP_acceptance_API_seeded':True,'provider_keys_absent':True})
        sequence,offline=0,False
        deadline=time.monotonic()+5400
        while time.monotonic()<deadline:
            assert offline or api.poll() is None,'API stopped unexpectedly'
            assert offline or worker.poll() is None,'Worker stopped unexpectedly'
            try:action=json.loads(CONTROL.read_text()) if CONTROL.exists() else {}
            except ValueError:time.sleep(.2);continue
            if action.get('sequence',0)<=sequence:time.sleep(.2);continue
            sequence,command=action['sequence'],action['command']
            if command=='offline':
                stop_child(worker);stop_child(api);offline=True
                write('offline-'+str(sequence),{'sequence':sequence,'actual_processes_stopped':True})
            elif command=='restart':
                if not offline:stop_child(worker);stop_child(api)
                before=durable();api,worker=start('serve'),start('setup-worker');offline=False
                after=durable();assert before==after
                write('restarted-'+str(sequence),{'sequence':sequence,'all_table_digests_identical':True,'before':before,'after':after})
            elif command=='finish':
                with Session(engine()) as db:
                    assert not db.scalar(select(CloudCall.id)), 'Local workflow must not reserve any cloud call'
                    assert not db.scalar(select(Job.id).where(Job.kind=='generate_native_method_suggestion'))
                    suggestions=list(db.scalars(select(Memory).where(Memory.data['type'].as_string()=='native_procedure_suggestion')))
                    assert suggestions and all(row.data['request'].get('source_kind')=='local_rules' for row in suggestions)
                    assert not any(row.task_id==tasks['CAP']['id'] for row in suggestions)
                    for letter,t in tasks.items():
                        stored=db.get(Task,t['id'])
                        assert digest(stored.data['goal_spec'])==original_goals[letter]['goal_spec_hash']
                        if letter in ('A','A2','CAP'):
                            assert stored.data['native_goal_acceptance_hash']==original_goals[letter]['acceptance_hash']
                    assert not db.get(Task,tasks['B']['id']).data.get('native_goal_acceptance_hash')
                    accepted=db.get(Task,tasks['B2']['id']).data['native_goal_acceptance']
                    proof=accepted['experience_proof']
                    assert len(proof['items'])==2
                    assert {ref['entity_id'] for item in proof['items'] for ref in item['current_refs']}==set(tasks['B2']['entity_ids'])
                    assert all(item['confirmation']['conclusion']=='confirmed' for item in proof['items'])
                    for rows in objects.values():
                        for obj in rows:
                            assert any(obj['id']==item['id'] and digest(db.get(Entity,obj['id']).data)==item['hash']
                                       for entries in archives.values() for item in entries)
                    write('server-final',{'cloud_calls':0,'cloud_suggestion_jobs':0,
                        'suggestions':[{'id':r.id,'status':r.status,'version':r.version,'data':r.data} for r in suggestions],
                        'tasks':[{'id':db.get(Task,t['id']).id,'data':db.get(Task,t['id']).data} for t in tasks.values()]})
                write('verification',{'classification':'runtime_evidence_not_whole_product_acceptance','cloud_calls':0,
                    'domain_quality':False,'production_ready':False,'before_shutdown_tables':durable()})
                return
            else:raise AssertionError('Unknown fixture action')
        raise AssertionError('Browser proof timed out')
    finally:
        for child in reversed(children):stop_child(child)
        for log in logs:log.close()
        ACCESS.unlink(missing_ok=True);CONTROL.unlink(missing_ok=True)
        isolated_cwd.cleanup()
