"""Every test run uses a new, disposable PostgreSQL database; never app data."""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from dotenv import dotenv_values

_test_url = os.environ.get("GA_TEST_DATABASE_URL")
_expected_data_dir = os.environ.get("GA_TEST_PG_DATA_DIR")
_expected_system_id = os.environ.get("GA_TEST_PG_SYSTEM_IDENTIFIER")
if not _test_url or not _expected_data_dir or not _expected_system_id:
    raise RuntimeError("测试需要显式指定独立 PostgreSQL 地址、数据目录和系统标识；不会使用应用数据库配置")
_original = make_url(_test_url)
if _original.host != "127.0.0.1" or not _original.port:
    raise RuntimeError("测试 PostgreSQL 必须使用独立的本机 IPv4 地址和明确端口")
for _app_url in (dotenv_values(".env").get("GA_DATABASE_URL"), os.environ.get("GA_DATABASE_URL")):
    if _app_url and (make_url(_app_url).port or 5432) == _original.port:
        raise RuntimeError("测试 PostgreSQL 不能与应用数据库配置使用同一端口")
if not Path(_expected_data_dir).is_absolute() or not _expected_system_id.isdecimal():
    raise RuntimeError("测试 PostgreSQL 身份格式无效")
_test_name = "ga_test_" + uuid4().hex[:12]
_admin = create_engine(_original.set(database="postgres"), isolation_level="AUTOCOMMIT")
with _admin.connect() as conn:
    data_dir, system_id = conn.execute(text(
        "SELECT current_setting('data_directory'), (SELECT system_identifier::text FROM pg_control_system())"
    )).one()
    if Path(data_dir).resolve() != Path(_expected_data_dir).resolve() or system_id != _expected_system_id:
        raise RuntimeError("测试 PostgreSQL 身份核验失败；未创建测试库")
    conn.execute(text('CREATE DATABASE "' + _test_name + '"'))
os.environ["GA_DATABASE_URL"] = _original.set(database=_test_name).render_as_string(hide_password=False)
from generic_agents.config import settings
from generic_agents.db import engine, migrate
settings.cache_clear(); engine.cache_clear(); migrate()

# Use the same router-registration order as the real service, before test
# collection imports individual shared-router modules in arbitrary order.
# The disposable database is already selected; no application data is used.
from generic_agents.api import app as application


@pytest.fixture(scope="session", autouse=True)
def disposable_database():
    from tempfile import TemporaryDirectory
    from pathlib import Path
    evidence_dir = TemporaryDirectory(prefix="ga-test-evidence-")
    settings().storage_dir = Path(evidence_dir.name)
    yield
    engine().dispose()
    assert _test_name.startswith("ga_test_")
    with _admin.connect() as conn:
        conn.execute(text('DROP DATABASE "' + _test_name + '" WITH (FORCE)'))
    _admin.dispose()
    evidence_dir.cleanup()


@pytest.fixture()
def account():
    from sqlalchemy.orm import Session
    from generic_agents.models import User, Workspace, Membership
    from generic_agents.security import password_hash
    username = "test_" + uuid4().hex[:12]
    password = uuid4().hex
    with Session(engine()) as db:
        user = User(username=username, password_hash=password_hash(password))
        workspace = Workspace(name="集成验证工作区")
        db.add_all([user, workspace]); db.flush()
        db.add(Membership(user_id=user.id, workspace_id=workspace.id, role="admin")); db.commit()
        workspace_id = workspace.id
    return username, password, workspace_id


@pytest.fixture(autouse=True)
def isolate_worker_queue():
    """The worker claims across workspaces; a previous test's durable jobs must not run in another test."""
    from sqlalchemy import inspect
    assert _test_name.startswith("ga_test_")
    with engine().begin() as conn:
        # Some modules register optional tables after migrate() has run. Reset
        # the tables actually present in this disposable database instead of
        # assuming every subsequently imported model was migrated.
        names = inspect(conn).get_table_names()
        tables = ', '.join(conn.dialect.identifier_preparer.quote_identifier(name) for name in names)
        if tables:
            conn.execute(text('TRUNCATE '+tables+' RESTART IDENTITY CASCADE'))


@pytest.fixture()
def client(account):
    from fastapi.testclient import TestClient
    from generic_agents.api import app
    with TestClient(app) as client:
        username, password, workspace_id = account
        response = client.post("/api/login", json={"username": username, "password": password})
        assert response.status_code == 200, response.text
        client.headers.update({"X-CSRF-Token": response.json()["csrf_token"], "X-Workspace-ID": workspace_id})
        yield client
