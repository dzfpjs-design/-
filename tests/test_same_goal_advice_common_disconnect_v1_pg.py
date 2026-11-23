"""A loopback disconnect after POST preserves the original nonzero reservation."""
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import cloud_business_reasoning_v197 as transport
from generic_agents import same_goal_advice_common_inventory_v2 as common
from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_execution_v308 as execution
from generic_agents import same_goal_advice_runtime_v308 as runtime
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, AuthSession, CloudCall, Invocation, Job, Plan, Task
from generic_agents.security import Principal
from generic_agents.worker import process_once
from tests.test_same_goal_advice_common_real_v1_pg import test_real_v1_prepare_and_start_count_in_isolated_pg as prepare_and_start
from tests.test_same_goal_advice_manual_append_pg import (
    matured_manual_case, managed, scene, reject_non_loopback_network,
)
from tests.same_goal_advice_aligned_publication_fixture import deepseek_3600_publication


def test_v1_disconnect_is_not_retried_and_remains_in_counter(matured_manual_case, monkeypatch, reject_non_loopback_network):
    case = matured_manual_case; prepare_and_start(case, monkeypatch)
    with Session(engine()) as db:
        plan = db.scalar(select(Plan).where(Plan.workspace_id == case.workspace_id,
            Plan.task_id == case.task_id, Plan.data['schema'].as_string() == contracts.PLAN))
        send_id = plan.data[execution.KEY]['job_ref']['id']
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == case.principal.user_id,
            AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
            .order_by(AuthSession.created_at.desc()))
        actor = Principal(case.principal.user_id, case.workspace_id, 'admin', session.id)
    received = []
    class DisconnectProvider(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.connection.shutdown(socket.SHUT_RDWR); self.connection.close()
        def log_message(self, *_args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), DisconnectProvider)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-loopback-key-not-a-credential')
        monkeypatch.setattr(settings(), 'cloud_allowed_providers', 'deepseek')
        monkeypatch.setitem(transport.ENDPOINTS, 'deepseek',
            'http://127.0.0.1:%d/chat/completions' % server.server_port)
        assert process_once(send_id, kinds=[contracts.SEND_KIND], deliver=False, workspace_id=case.workspace_id)
        with Session(engine()) as db:
            task, job = db.get(Task, case.task_id), db.get(Job, send_id)
            assert job.status == 'failed'
            calls = list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == case.workspace_id)))
            assert len(calls) == 1 and calls[0].status == 'failed'
            invocations = list(db.scalars(select(Invocation).where(Invocation.workspace_id == case.workspace_id,
                Invocation.data['job_id'].as_string() == send_id)))
            assert len(invocations) == 1 and invocations[0].status != 'succeeded'
            assert invocations[0].data[runtime.STATE_KEY]['capture'] is None
            assert execution.budget_calls(db, actor, task) == 1
            assert common.read(db, actor, task) == {'v1_calls': 1, 'queued_v2': 0}
            start = db.scalar(select(Audit).where(Audit.workspace_id == case.workspace_id,
                Audit.object_id == plan.id, Audit.action == execution.STARTED))
            replay = execution.start(db, actor, case.task_id, plan.id, start.data['request'])
            assert replay['send_job_id'] == send_id
            db.commit()
        assert len(received) == 1
        assert not process_once(send_id, kinds=[contracts.SEND_KIND], deliver=False, workspace_id=case.workspace_id)
        assert len(received) == 1
        print('V1_DISCONNECT_KEPT_ORIGINAL_COUNT_WITHOUT_RESEND', flush=True)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
