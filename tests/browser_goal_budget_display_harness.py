"""Opt-in isolated browser evidence; synthetic local data only."""
import hashlib, json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import AuthSession, CloudCall, Membership, User, Workspace
from generic_agents.security import password_hash
from tests.test_native_query_readonly_pg import ready_query, persisted_state
from tests.test_same_goal_advice_manual_append_pg import reject_non_loopback_network

USERNAME = 'synthetic-budget-review'
PASSWORD = 'synthetic-budget-test-only'

@pytest.fixture
def account():
    with Session(engine()) as db:
        user = User(username=USERNAME, password_hash=password_hash(PASSWORD))
        workspace = Workspace(name='隔离预算核查工作区')
        db.add_all([user,workspace]);db.flush()
        db.add(Membership(user_id=user.id,workspace_id=workspace.id,role='admin'));db.commit()
        return USERNAME,PASSWORD,workspace.id


def test_browser_harness(client, reject_non_loopback_network):
    assert engine().url.host == '127.0.0.1' and engine().url.database.startswith('ga_test_')
    directory = Path(os.environ['GA_BROWSER_REVIEW_DIR'])
    assert directory.is_absolute() and not directory.is_symlink()
    directory = directory.resolve(strict=True)
    assert directory.parent == Path(tempfile.gettempdir()).resolve()
    assert directory.name.startswith('ga-goal-budget-browser-') and directory.stat().st_uid == os.getuid()
    assert directory.stat().st_mode & 0o077 == 0
    info_file, control = directory/'access.json', directory/'control.json'
    build = Path(os.environ['GA_BROWSER_UI_BUILD']).resolve(strict=True)
    assert build.is_dir() and (build/'index.html').is_file()
    proof = json.loads(Path(os.environ['GA_BROWSER_BUILD_PROOF']).read_text())
    source_root = Path(__file__).resolve().parents[1]
    expected_sources = {name:hashlib.sha256((source_root/name).read_bytes()).hexdigest()
        for name in ('design/resource-studio/src/NativeGoalStudio.jsx',
                     'design/resource-studio/src/GoalBudgetDisplayCandidate.jsx')}
    assert proof['sources'] == expected_sources
    artifacts = {str(path.relative_to(build)):hashlib.sha256(path.read_bytes()).hexdigest()
        for path in build.rglob('*') if path.is_file()}
    assert proof['artifacts'] == artifacts
    case = ready_query(client); task_id = case['task']['id']; before = persisted_state(task_id)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env = os.environ.copy()
    env.pop('GA_GOAL_BUDGET_DISPLAY_PREVIEW_ENABLED', None)
    env.update(GA_STORAGE_DIR=str(settings().storage_dir), GA_SETUP_BUILD_DIR=str(build),
        GA_ALLOWED_ORIGINS='http://127.0.0.1:'+str(port),
        GA_COOKIE_SECURE='false', GA_CLOUD_ALLOWED_PROVIDERS='', DEEPSEEK_API_KEY='', DASHSCOPE_API_KEY='')
    log=open(directory/'service.log','w');process=None;control.unlink(missing_ok=True)
    try:
        process=subprocess.Popen([sys.executable,'-m','uvicorn','generic_agents.api:app','--host','127.0.0.1',
            '--port',str(port)],env=env,stdout=log,stderr=log)
        for _ in range(80):
            assert process.poll() is None
            try:
                if httpx.get('http://127.0.0.1:'+str(port)+'/health',trust_env=False,timeout=1).is_success:break
            except httpx.HTTPError:pass
            time.sleep(.25)
        else:raise AssertionError('isolated API not ready')
        info={'url':'http://127.0.0.1:'+str(port)+'/setup/','username':USERNAME,'password':PASSWORD,
            'task_id':task_id,'instance_id':case['release']['instance_id'],'state':'ready'}
        info_file.write_text(json.dumps(info));os.chmod(info_file,0o600)
        print('ISOLATED_GOAL_BROWSER_READY',flush=True)
        deadline=time.monotonic()+360
        while time.monotonic()<deadline:
            if control.exists():
                command=json.loads(control.read_text());control.unlink()
                if command['action']=='revoke':
                    with Session(engine()) as db:
                        user=db.scalar(select(User).where(User.username==USERNAME))
                        for session in db.scalars(select(AuthSession).where(AuthSession.user_id==user.id)):
                            session.revoked=True
                        db.commit()
                    info['state']='revoked';info_file.write_text(json.dumps(info));os.chmod(info_file,0o600)
                elif command['action']=='finish':
                    assert persisted_state(task_id)==before
                    with Session(engine()) as db:assert not list(db.scalars(select(CloudCall)))
                    print('ISOLATED_GOAL_BROWSER_DURABILITY_VERIFIED',flush=True)
                    return
                else:raise AssertionError('unknown browser control')
            time.sleep(.25)
        raise AssertionError('browser evidence session timed out')
    finally:
        if process and process.poll() is None:process.terminate();process.wait(timeout=10)
        log.close();info_file.unlink(missing_ok=True);control.unlink(missing_ok=True)
