import os
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from generic_agents.db import engine,migrate
from generic_agents.models import User,Workspace,Membership
from generic_agents.security import password_hash
from generic_agents.api import app
from generic_agents.setup_api import initial_configuration
assert os.environ['DEEPSEEK_API_KEY']=='' and os.environ['DASHSCOPE_API_KEY']==''
assert int(os.environ['TEST_PG_PORT']) not in (8766,55486)
with engine().connect() as c:assert Path(c.execute(text("select current_setting('data_directory')")).scalar_one()).resolve()==Path(os.environ['TEST_ROOT'],'data').resolve()
migrate()
with Session(engine()) as db:
 u=User(username=os.environ['TEST_USERNAME'],password_hash=password_hash(os.environ['TEST_PASSWORD']));a=Workspace(name='合成林场甲');b=Workspace(name='合成林场乙');db.add_all([u,a,b]);db.flush();db.add_all([Membership(user_id=u.id,workspace_id=a.id,role='admin'),Membership(user_id=u.id,workspace_id=b.id,role='admin')]);db.commit();ids=[a.id,b.id]
with TestClient(app) as client:
 r=client.post('/api/login',json={'username':os.environ['TEST_USERNAME'],'password':os.environ['TEST_PASSWORD']});assert r.status_code==200
 client.headers['X-CSRF-Token']=r.json()['csrf_token']
 for id,name in zip(ids,['甲配置','乙配置']):
  client.headers['X-Workspace-ID']=id;draft=client.get('/api/setup/draft').json();c=initial_configuration();c['instanceProfile']={'name':name,'domain':'合成领域','goal':'合成目标'}
  assert client.put('/api/setup/draft',json={'revision':draft['revision'],'configuration':c}).status_code==200
engine().dispose()
