"""Opt-in real v2 provider/UI comparison with two independent registered schemas.

Only H01 is instantiated for cloud selection. The six-field capacity task is a
separate product refusal check. Source acceptance is API-seeded, not a human
browser acceptance claim. B is ready but remains unaccepted for current checks.
"""
import json
from hashlib import sha256
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

from dotenv import dotenv_values
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
from tests.test_vertical_slice import manifest, publish

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/evidence/native-suggestion-quality-v12/browser'
ACCESS = ROOT / '.runtime/native-suggestion-v2-browser-access.json'
CONTROL = ROOT / '.runtime/native-suggestion-v2-browser-control.json'


def write(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')


def seed(client):
    c = configuration()
    c['instanceProfile']['goal'] = 'NMS_PRIVATE_V2_BUSINESS_NOTE'
    for flow in c['flows'].values():
        flow['confirmedSignature'] = json.dumps(signature_value(c, flow), ensure_ascii=False)
    revision = save(client, c, review_automation=True)
    member = client.get('/api/instances/members').json()[0]
    def fields(mixed_kind):
        return [{'label':'NMS_PRIVATE_V2_NAME','kind':'文本','required':True},
                {'label':'NMS_PRIVATE_V2_COUNT','kind':'整数','required':True},
                {'label':'NMS_PRIVATE_V2_FLAG','kind':'是否','required':True},
                {'label':'NMS_PRIVATE_V2_MIXED','kind':mixed_kind,'required':True}]
    body = {'revision':revision,'role_bindings':{'核验负责人':member['id']},'objects':[
        {'name':'NMS_PRIVATE_V2_TYPE_TEXT','fields':fields('文本')},
        {'name':'NMS_PRIVATE_V2_TYPE_BOOL','fields':fields('是否')},
        {'name':'NMS_PRIVATE_V2_TYPE_CAPACITY','fields':[
            {'label':'NMS_PRIVATE_V2_CAP_'+str(i),'kind':'文本','required':True} for i in range(6)]}]}
    check = client.post('/api/instances/check', json=body)
    assert check.status_code == 200, check.text
    response = client.post('/api/instances', json={**body,'confirmed_configuration_hash':check.json()['configuration_hash'],'confirm_configuration':True})
    assert response.status_code == 201, response.text
    release = response.json(); enable(client, release)
    kinds = release['snapshot']['object_types']; tasks = {}; objects = {}
    for letter in ('A','B','CAP'):
        selected = kinds[:2] if letter != 'CAP' else kinds[2:]
        rows = []
        for index, kind in enumerate(selected):
            values = {}
            for field in kind['fields']:
                if field['kind'] == '整数': value = (17 if letter == 'A' else 51) + index * 17
                elif field['kind'] == '是否': value = True if field['label'] == 'NMS_PRIVATE_V2_FLAG' else False
                else: value = 'NMS_PRIVATE_V2_ATTRIBUTE_' + letter + '_' + str(index) + '_' + field['label']
                values[field['key']] = value
            result = client.post('/api/instances/'+release['instance_id']+'/objects',json={
                'release_id':release['id'],'object_type':kind['key'],'attributes':values})
            assert result.status_code == 201, result.text
            rows.append(result.json())
        objects[letter] = rows
        keys = ([field['key'] for field in kinds[0]['fields'][1:]] if letter != 'CAP'
                else [field['key'] for field in kinds[2]['fields']])
        result = client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json={
            'release_id':release['id'],'business_id':release['available_business_ids'][0],
            'entity_ids':[row['id'] for row in rows],'title':'NMS_PRIVATE_V2_TASK_'+letter,'request_id':str(uuid4())})
        assert result.status_code == 201, result.text
        task = result.json()
        criteria = [{'id':'enough','kind':'evidence_sufficient','description':'NMS_PRIVATE_V2_CRITERION_ENOUGH'},
                    {'id':'human','kind':'human_confirmation','description':'NMS_PRIVATE_V2_CRITERION_HUMAN'}]
        if letter != 'CAP':
            criteria.append({'id':'flag','kind':'field_value','field':keys[1],'operator':'eq',
                             'expected':True,'description':'NMS_PRIVATE_V2_BOOLEAN_EXPECTED'})
        spec = {'purpose':'NMS_PRIVATE_V2_GOAL_TEXT','entity_ids':[row['id'] for row in rows],
                'required_fields':keys,'criteria':criteria,
                'subgoals':[{'id':'check','description':'NMS_PRIVATE_V2_SUBGOAL','criterion_ids':[x['id'] for x in criteria]}],
                'evidence':{'source':'archive','minimum_verification':'recorded','time':{'mode':'archive','timezone':'Asia/Shanghai'}},
                'observation_mode':'existing_only'}
        define(client,'/api/workspace/tasks/'+task['id']+'/goal',{
            'request_id':str(uuid4()),'expected_version':task['version'],'reason':'独立合成档案核验，不认定现场', 'goal_spec':spec})
        ready(client, task)
        if letter in ('A','CAP'):
            accepted = accept(client, task); assert accepted.status_code == 200, accepted.text
        tasks[letter] = {'id':task['id'],'entity_ids':[row['id'] for row in rows],'fields':keys}
    models = {}
    for provider, model, secret in [('qwen','qwen-plus','DASHSCOPE_API_KEY'),('deepseek','deepseek-flash','DEEPSEEK_API_KEY')]:
        execution = {'provider':provider,'model':model,'secret_ref':secret,'max_tokens':2048}
        if provider == 'deepseek': execution['thinking_mode'] = 'disabled'
        models[provider] = publish(client, manifest('foundation_model',name='native_method_v2_'+provider+'_qa',
            allowed_agent_roles=['business'],execution=execution,data_policy={'cloud_allowed':True,'redact_fields':[]}))
    return release, tasks, objects, models


def test_browser_native_suggestion_v2(client, account):
    if os.getenv('GA_NATIVE_SUGGESTION_V2_BROWSER') != '1':
        pytest.skip('Explicit disposable real-provider browser comparison only')
    assert engine().url.database.startswith('ga_test_') and not ACCESS.exists()
    assert not list((OUT/'cloud-http').glob('request-*.json')), 'Preserve previous paid attempts'
    keys = dotenv_values('.env')
    assert keys.get('DASHSCOPE_API_KEY') and keys.get('DEEPSEEK_API_KEY'), 'Real keys required; no fallback'
    release, tasks, objects, models = seed(client)
    with Session(engine()) as db:
        assert not db.scalar(select(CloudCall.id))
        viewer_name, viewer_password = 'viewer_'+uuid4().hex[:10], uuid4().hex
        viewer = User(username=viewer_name,password_hash=password_hash(viewer_password)); db.add(viewer); db.flush()
        db.add(Membership(user_id=viewer.id,workspace_id=account[2],role='viewer')); db.commit()
        archives = {letter:[{'id':obj['id'],'hash':digest(db.get(Entity,obj['id']).data)} for obj in rows] for letter,rows in objects.items()}
    protocol_path = ROOT/'docs/evidence/native-suggestion-quality-v12/protocol.json'
    protocol = json.loads(protocol_path.read_text())
    assert protocol['status'] == 'frozen_before_calls', 'Root must freeze labels and two-call authorization first'
    authorization = protocol['run_authorization']
    source_freeze = json.loads((ROOT/'.runtime/native-suggestion-v2-source-freeze.json').read_text())
    assert digest(source_freeze['backend_files']) == authorization['source_bundle_hash']
    assert {item['path'] for item in source_freeze['backend_files']} == {
        str(path.relative_to(ROOT)) for path in (ROOT/'src/generic_agents').rglob('*.py')}
    assert all(sha256((ROOT/item['path']).read_bytes()).hexdigest() == item['sha256']
               for item in source_freeze['backend_files']), 'Backend changed after frozen authorization'
    ui_manifest = json.loads((ROOT/'.runtime/native-suggestion-v2-frontend-source.json').read_text())
    assert ui_manifest['asset_sha256'] == authorization['ui_asset_sha256']
    assert sha256((ROOT/'.runtime/builds/native-suggestion-v2/assets'/ui_manifest['asset']).read_bytes()).hexdigest() == authorization['ui_asset_sha256']
    assert all(sha256((ROOT/item['path']).read_bytes()).hexdigest() == item['sha256']
               for item in ui_manifest['files']), 'UI source changed after build'
    frozen_previews = {}
    for provider, model in models.items():
        response = client.post('/api/workspace/tasks/'+tasks['A']['id']+'/experience/suggestions/preview',
                               json={'model_profile_id':model['id']})
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview['generation_contract']['generation_contract_hash'] == authorization['generation_contract_hash']
        assert preview['dispatch_messages_hash'] == authorization['dispatch_messages_hashes'][provider]
        assert preview['dispatch_options'] == authorization['dispatch_options'][provider]
        assert preview['model_hash'] == authorization['model_profile_hashes'][provider]
        assert digest(preview['cloud_input']) == authorization['actual_H01_projection_hash']
        frozen_previews[provider] = {key:preview[key] for key in [
            'cloud_input','generation_contract','dispatch_messages_hash','dispatch_options','capacity','model_hash']}
    with Session(engine()) as db:
        assert not db.scalar(select(CloudCall.id)), 'Preflight must not send or reserve cloud calls'
    write('input-freeze',{'classification':'real_synthetic_registered_schemas_not_domain_quality',
        'protocol_hash':digest(protocol),'archives':archives,'tasks':tasks,'authorized_previews':frozen_previews,
        'source_A_CAP_formal_acceptance_API_preseeded':True,'B_workflow_ready_not_accepted':True,
        'cloud_count_before':0,'actual_HTTP_limit':2})
    forbidden = [account[2],release['id'],release['instance_id'],*[x['id'] for x in models.values()]]
    for task in tasks.values(): forbidden += [task['id'],*task['entity_ids']]
    forbidden += [x['hash'] for rows in archives.values() for x in rows]
    with socket.socket() as sock: sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
    url = 'http://localhost:'+str(port)
    env = {'PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8','GA_ALLOWED_ORIGINS':url,
        'GA_COOKIE_SECURE':'false','GA_DATABASE_URL':settings().database_url,'GA_STORAGE_DIR':str(settings().storage_dir),
        'GA_SETUP_BUILD_DIR':str(ROOT/'.runtime/builds/native-suggestion-v2'),
        'DASHSCOPE_API_KEY':keys['DASHSCOPE_API_KEY'],'DEEPSEEK_API_KEY':keys['DEEPSEEK_API_KEY'],
        'GA_CLOUD_DAILY_CALL_LIMIT':'2','GA_NATIVE_SUGGESTION_BROWSER':'1',
        'GA_NATIVE_SUGGESTION_HTTP_LIMIT':'2',
        'GA_NATIVE_SUGGESTION_CAPTURE_RESPONSE':'1',
        'GA_NATIVE_SUGGESTION_PROBE_DIR':str(OUT/'cloud-http'),
        'GA_NATIVE_SUGGESTION_FORBIDDEN_IDS':json.dumps(forbidden)}
    children, logs = [], []
    def start(command):
        log = (ROOT/('.runtime/native-suggestion-v2-browser-'+command+'.log')).open('ab'); logs.append(log)
        args = [sys.executable,'-m','tests.native_suggestion_runtime_probe',command]
        if command == 'serve': args += ['--host','127.0.0.1','--port',str(port)]
        child = subprocess.Popen(args,cwd=ROOT,env=env,stdout=log,stderr=log); children.append(child)
        if command == 'serve':
            def alive():
                try: return httpx.get(url+'/health',timeout=1,trust_env=False).is_success
                except httpx.HTTPError: return False
            wait_for(alive,[child],timeout=15,description='v2 isolated API')
        return child
    try:
        api, worker = start('serve'), start('setup-worker')
        with os.fdopen(os.open(ACCESS,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:
            json.dump({'url':url,'username':account[0],'password':account[1],'viewer_username':viewer_name,
                'viewer_password':viewer_password,'workspace_id':account[2],'instance_id':release['instance_id'],
                'tasks':tasks,'models':{key:value['id'] for key,value in models.items()}},f)
        write('prepared',{'actual_API_worker_ready':True,'tasks':tasks,'instance_id':release['instance_id'],
            'models':{key:{'id':x['id'],'provider':key,'model':x['data']['execution']['model']} for key,x in models.items()},
            'actual_HTTP_limit':2,'source_A_CAP_formal_acceptance_API_preseeded':True})
        sequence, offline = 0, False
        deadline = time.monotonic()+5400
        while time.monotonic()<deadline:
            assert offline or api.poll() is None, 'API stopped unexpectedly'
            assert offline or worker.poll() is None, 'Worker stopped unexpectedly'
            try: action = json.loads(CONTROL.read_text()) if CONTROL.exists() else {}
            except ValueError: time.sleep(.2); continue
            if action.get('sequence',0)<=sequence: time.sleep(.2); continue
            sequence, command = action['sequence'], action['command']
            if command=='offline':
                stop_child(worker); stop_child(api); offline=True
                write('offline',{'sequence':sequence,'actual_processes_stopped':True})
            elif command=='restart':
                if not offline: stop_child(worker); stop_child(api)
                before=durable(); api,worker=start('serve'),start('setup-worker'); offline=False
                after=durable(); assert before==after
                write('restarted',{'sequence':sequence,'all_table_digests_identical':True,'before':before,'after':after})
            elif command=='finish':
                proofs=[json.loads(p.read_text()) for p in sorted((OUT/'cloud-http').glob('request-*.json'))]
                assert len(proofs)==2 and all(p.get('sent_to_real_provider') and p.get('http_status')==200 for p in proofs)
                with Session(engine()) as db:
                    calls=list(db.scalars(select(CloudCall))); assert len(calls)==2
                    suggestions=list(db.scalars(select(Memory).where(Memory.data['type'].as_string()=='native_procedure_suggestion')))
                    write('server-final',{'calls':[{'id':r.id,'status':r.status,'charged_tokens':r.charged_tokens,'data':r.data} for r in calls],
                        'suggestions':[{'id':r.id,'status':r.status,'version':r.version,'data':r.data} for r in suggestions],
                        'frozen_acceptance_hashes':{key:db.get(Task,t['id']).data.get('native_goal_acceptance_hash') for key,t in tasks.items()}})
                write('verification',{'classification':'evidence_collected_not_quality_or_product_acceptance',
                    'actual_provider_attempts':2,'requires_independent_raw_scoring':True,'business_benefit':False,'production_ready':False,
                    'before_shutdown_tables':durable()})
                return
            else: raise AssertionError('Unknown fixture action')
        raise AssertionError('v2 browser verification timed out')
    finally:
        for child in reversed(children): stop_child(child)
        for log in logs: log.close()
        ACCESS.unlink(missing_ok=True); CONTROL.unlink(missing_ok=True)
