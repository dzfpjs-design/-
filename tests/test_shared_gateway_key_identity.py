"""One shared encrypted vault must never accept a different key across protocols."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os,subprocess,sys
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Job
from generic_agents.managed_file_grants import KEY_MARKER
from tests.test_managed_mqtt_grants import vault

KINDS={'file':'managed_file_grant_v1','ftps':'managed_ftps_grant_v1','mqtt':'managed_mqtt_grant_v1'}

def request_for(protocol):
    common={'request_id':str(uuid4()),'name':'隔离测试','host':'192.168.0.10','port':8883,'username':'synthetic-user','password':'synthetic-password'}
    if protocol=='mqtt':return '/api/mqtt/authorizations',common
    if protocol=='ftps':return '/api/ftps-authorizations/grants',{**common,'tls_name':'192.168.0.10','root':'/','data_port_min':49000,'data_port_max':49010,'ca_pem':'','server_chroot_confirmed':True}
    return '/api/file-gateway-authorizations/grants',{**common,'protocol':'ftp','root':'/','data_port_min':49000,'data_port_max':49010,'server_chroot_confirmed':True,'plaintext_risk_confirmed':True}

@pytest.mark.parametrize(('existing','target'),[(a,b) for a in KINDS for b in KINDS if a!=b])
def test_missing_marker_with_ciphertext_blocks_other_protocol(client,account,vault,existing,target):
    with Session(engine()) as db:
        db.add(Job(workspace_id=account[2],kind=KINDS[existing],status='internal',idempotency_key='old:'+str(uuid4()),data={'cipher':'preserved-existing-ciphertext'}));db.commit()
    path,body=request_for(target);response=client.post(path,json=body)
    assert response.status_code==503,response.text
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind==KEY_MARKER))==0
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind==KINDS[existing]))==1
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind==KINDS[target]))==0

@pytest.mark.parametrize('existing',list(KINDS))
def test_key_initializer_refuses_any_existing_ciphertext(account,vault,tmp_path,existing):
    with Session(engine()) as db:
        db.add(Job(workspace_id=account[2],kind=KINDS[existing],status='internal',idempotency_key='old:'+str(uuid4()),data={'cipher':'preserved-existing-ciphertext'}));db.commit()
    output=tmp_path/'must-not-create.key'
    env={**os.environ,'GA_DATABASE_URL':settings().database_url,'GA_STORAGE_DIR':str(settings().storage_dir),'GA_FILE_GATEWAY_KEY_FILE':str(output)}
    result=subprocess.run([sys.executable,'scripts/initialize_file_gateway_key.py'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    assert result.returncode!=0 and not output.exists()

@pytest.mark.parametrize(('left','right'),[('file','mqtt'),('ftps','mqtt'),('file','ftps')])
def test_concurrent_first_creation_uses_single_identity(client,vault,left,right):
    one=request_for(left);two=request_for(right)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(client.post,one[0],json=one[1]);b=pool.submit(client.post,two[0],json=two[1]);responses=[a.result(timeout=10),b.result(timeout=10)]
    assert [r.status_code for r in responses]==[200,200],[r.status_code for r in responses]
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(Job).where(Job.kind==KEY_MARKER))==1
