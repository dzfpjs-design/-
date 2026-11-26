"""Seed an isolated disposable browser test with synthetic text only."""
import os
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from generic_agents.db import engine, migrate
from generic_agents.models import User,Workspace,Membership
from generic_agents.security import password_hash

assert os.environ['DEEPSEEK_API_KEY']=='' and os.environ['DASHSCOPE_API_KEY']==''
assert os.environ['GA_CLOUD_ALLOWED_PROVIDERS']==''
assert int(os.environ['TEST_PG_PORT']) not in (8766,55486)
assert '/candidate-browser-isolated-' in os.environ['TEST_ROOT']
assert engine().url.database.startswith('ga_test_') and engine().url.host=='127.0.0.1'
with engine().connect() as conn:
    assert Path(conn.execute(text("SELECT current_setting('data_directory')")).scalar_one()).resolve()==Path(os.environ['TEST_ROOT'],'data').resolve()
migrate()
with Session(engine()) as db:
    user=User(username=os.environ['TEST_USERNAME'],password_hash=password_hash(os.environ['TEST_PASSWORD']))
    workspace=Workspace(name='合成工作区');db.add_all([user,workspace]);db.flush()
    db.add(Membership(user_id=user.id,workspace_id=workspace.id,role='admin'));db.commit();workspace_id=workspace.id

from generic_agents.api import app
from generic_agents.setup_api import initial_configuration
from generic_agents.worker import process_once
from tests.test_setup_business_candidates import material,bundle
with TestClient(app) as client:
    login=client.post('/api/login',json={'username':os.environ['TEST_USERNAME'],'password':os.environ['TEST_PASSWORD']})
    assert login.status_code==200
    client.headers.update({'X-CSRF-Token':login.json()['csrf_token'],'X-Workspace-ID':workspace_id})
    row=material(client)
    proposal=bundle(row)
    proposal['candidates'][0]['name']='合成检查'
    proposal['candidates'][0]['steps'][0]['sources']=[{'source_text':'仅用于测试的另一处原文。','quote':'另一处原文'}]
    draft=client.get('/api/setup/draft').json();config=draft['configuration'] or initial_configuration();config['materials']=[row]
    config['instanceProfile']={'name':'合成配置','domain':'合成领域','goal':'测试'}
    saved=client.put('/api/setup/draft',json={'revision':draft['revision'],'configuration':config});assert saved.status_code==200, (saved.status_code,saved.text[:300])
    added=client.post('/api/setup/candidates',json={'revision':saved.json()['revision'],'bundle':proposal});assert added.status_code==200
engine().dispose()
