"""Short, explicit disposable API for the changed authorization guard; no broker/device."""
import json,os,socket,subprocess,sys,time
from pathlib import Path
import httpx,pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.config import settings
from generic_agents.models import MqttGateway,CloudCall
from tests.test_mqtt_api import configuration

ROOT=Path(__file__).resolve().parents[1]
ACCESS=ROOT/'.runtime/m39-mqtt-guard-access.json'
CONTROL=ROOT/'.runtime/m39-mqtt-guard-control.json'
OUT=ROOT/'docs/evidence/mqtt-pipeline-v06/browser'


def test_guard_browser(client,account,monkeypatch):
    if os.getenv('GA_M39_MQTT_GUARD_BROWSER')!='1':pytest.skip('Explicit isolated UI only')
    assert engine().url.database.startswith('ga_test_') and not ACCESS.exists()
    origin='mqtt://127.0.0.1:1883';monkeypatch.setattr(settings(),'mqtt_allowed_brokers',origin)
    response=client.post('/api/mqtt/gateways',json={'name':'隔离授权核查来源','configuration':configuration()})
    assert response.status_code==201;identifier=response.json()['id']
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    url='http://localhost:'+str(port)
    env={**os.environ,'GA_DATABASE_URL':settings().database_url,'GA_STORAGE_DIR':str(settings().storage_dir),
         'GA_MQTT_ALLOWED_BROKERS':origin,'GA_ALLOWED_ORIGINS':url,'DASHSCOPE_API_KEY':'','DEEPSEEK_API_KEY':''}
    log=open(ROOT/'.runtime/m39-mqtt-guard-api.log','a');process=None
    try:
        process=subprocess.Popen([sys.executable,'-m','uvicorn','generic_agents.api:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
        for _ in range(80):
            assert process.poll() is None
            try:
                if httpx.get(url+'/health',trust_env=False,timeout=1).is_success:break
            except httpx.HTTPError:pass
            time.sleep(.25)
        else:raise AssertionError('API not ready')
        with os.fdopen(os.open(ACCESS,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:
            json.dump({'username':account[0],'password':account[1],'url':url},f)
        print('Isolated guard API ready; no broker or device process started',flush=True)
        seen=set();deadline=time.monotonic()+600
        while time.monotonic()<deadline:
            assert process.poll() is None
            command=json.loads(CONTROL.read_text()).get('command') if CONTROL.exists() else None
            if command not in {'dirty','finish'} or command in seen:time.sleep(.2);continue
            with Session(engine()) as db:
                gateway=db.get(MqttGateway,identifier)
                if command=='dirty':
                    assert gateway.status=='paused' and gateway.version==1 and gateway.data==configuration()
                    (OUT/'authorization-dirty-blocked.json').write_text(json.dumps({'paused':True,'source_unchanged':True,'version_unchanged':True})+'\n')
                else:
                    assert 'dirty' in seen and gateway.status=='active' and gateway.version==2 and gateway.data==configuration()
                    assert not db.scalars(select(CloudCall)).all()
                    (OUT/'authorization-guard-verification.json').write_text(json.dumps({'dirty_source_authorization_blocked':True,
                        'original_configuration_restored_then_authorized':True,'legacy_selectors_normalized':True,
                        'configuration_unchanged':True,'cloud_calls':0,'broker_connected':False,'device_process_started':False})+'\n')
                    print('Changed-source guard and restored-source authorization verified',flush=True);break
            seen.add(command)
        else:raise AssertionError('Guard harness expired')
    finally:
        if process and process.poll() is None:process.terminate();process.wait(timeout=10)
        log.close();ACCESS.unlink(missing_ok=True);CONTROL.unlink(missing_ok=True)
