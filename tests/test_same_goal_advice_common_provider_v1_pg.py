"""Isolated v1 loopback provider journey; never contacts a real provider."""
import base64
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import cloud_business_reasoning_v197 as transport
from generic_agents import same_goal_advice_common_inventory_v2 as common
from generic_agents import same_goal_advice_contracts_v302 as contracts
from generic_agents import same_goal_advice_execution_v308 as execution
from generic_agents import same_goal_advice_preview_v307 as preview
from generic_agents import same_goal_advice_runtime_v308 as runtime
from generic_agents import same_goal_advice_store_v302 as store
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
from tests.test_same_goal_advice_runtime_v308 import _opinion


def test_v1_loopback_response_is_persisted_and_counted_once(matured_manual_case, monkeypatch, reject_non_loopback_network):
    case = matured_manual_case
    prepare_and_start(case, monkeypatch)
    with Session(engine()) as db:
        plan = db.scalar(select(Plan).where(Plan.workspace_id == case.workspace_id,
            Plan.task_id == case.task_id, Plan.data['schema'].as_string() == contracts.PLAN))
        assert plan is not None
        event = db.scalar(select(Audit).where(Audit.workspace_id == case.workspace_id,
            Audit.object_id == plan.id, Audit.action == preview.ACTION))
        assert event is not None
        decision = event.data['preview_record']['cloud_request']['decision_input']
        reply = _opinion(decision)
        send_id = plan.data[execution.KEY]['job_ref']['id']
        session = db.scalar(select(AuthSession).where(AuthSession.user_id == case.principal.user_id,
            AuthSession.revoked.is_(False), AuthSession.expires_at > func.clock_timestamp())
            .order_by(AuthSession.created_at.desc()))
        actor = Principal(case.principal.user_id, case.workspace_id, 'admin', session.id)
    received = []
    class SyntheticProvider(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append({'path': self.path, 'authorization': self.headers.get('Authorization'),
                'body': self.rfile.read(int(self.headers['Content-Length']))})
            envelope = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(reply)}}],
                'usage': {'prompt_tokens': 40, 'completion_tokens': 60, 'total_tokens': 100}}
            raw = json.dumps(envelope).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw))); self.send_header('x-request-id', 'synthetic-loopback')
            self.end_headers(); self.wfile.write(raw)
        def log_message(self, *_args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), SyntheticProvider)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic-loopback-key-not-a-credential')
        monkeypatch.setattr(settings(), 'cloud_allowed_providers', 'deepseek')
        monkeypatch.setitem(transport.ENDPOINTS, 'deepseek',
            'http://127.0.0.1:%d/chat/completions' % server.server_port)
        assert process_once(send_id, kinds=[contracts.SEND_KIND], deliver=False, workspace_id=case.workspace_id)
        with Session(engine()) as db:
            task, job = db.get(Task, case.task_id), db.get(Job, send_id)
            if job.status != 'succeeded':
                from generic_agents.worker_failure_diagnostics_v289 import KEY
                diagnostic = job.data.get(KEY) or {}
                calls = list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == case.workspace_id)))
                invocations = list(db.scalars(select(Invocation).where(Invocation.workspace_id == case.workspace_id,
                    Invocation.data['job_id'].as_string() == send_id)))
                print('V1_WORKER_DIAGNOSTIC', json.dumps({'diagnostic': diagnostic.get('diagnostic'),
                    'loopback_requests': len(received), 'cloud_statuses': [row.status for row in calls],
                    'invocation_statuses': [row.status for row in invocations],
                    'invocation_error': [str(row.data.get('error', ''))[:240]
                        .replace('synthetic-loopback-key-not-a-credential', '[synthetic key]') for row in invocations],
                    'transport_error_type': [row.data.get('receipt', {}).get('transport_error_type')
                        for row in calls]}, ensure_ascii=False), flush=True)
            assert job.status == 'succeeded', 'synthetic provider worker did not complete'
            assert execution.budget_calls(db, actor, task) == 1
            assert common.read(db, actor, task) == {'v1_calls': 1, 'queued_v2': 0}
            calls = list(db.scalars(select(CloudCall).where(CloudCall.workspace_id == case.workspace_id)))
            assert len(calls) == 1 and calls[0].status == 'succeeded' and calls[0].charged_tokens == 100
            invocations = list(db.scalars(select(Invocation).where(Invocation.workspace_id == case.workspace_id,
                Invocation.data['job_id'].as_string() == send_id)))
            assert len(invocations) == 1 and invocations[0].status == 'succeeded'
            capture = invocations[0].data[runtime.STATE_KEY]['capture']
            raw = base64.b64decode(capture['raw_base64'], validate=True)
            assert hashlib.sha256(raw).hexdigest() == capture['raw_sha256']
            assert json.loads(raw)['usage']['total_tokens'] == 100
            start = db.scalar(select(Audit).where(Audit.workspace_id == case.workspace_id,
                Audit.object_id == plan.id, Audit.action == execution.STARTED))
            original_request = start.data['request']
        # A new session cold-reads the actual user-facing saved result and
        # replays the original request. Neither path runs the provider.
        engine().dispose()
        with Session(engine()) as db:
            saved = store.get(db, actor, case.task_id, plan.id)
            assert saved['phase'] == 'ready_for_review' and saved['result'] is not None
            replay = execution.start(db, actor, case.task_id, plan.id, original_request)
            assert replay['send_job_id'] == send_id
            db.commit()
        assert len(received) == 1
        assert received[0]['path'] == '/chat/completions'
        assert received[0]['authorization'] == 'Bearer synthetic-loopback-key-not-a-credential'
        payload = json.loads(received[0]['body']); assert payload['model'] == 'deepseek-v4-pro'
        user_message = next(message for message in payload['messages'] if message['role'] == 'user')
        assert json.loads(user_message['content'][0]['text'])['decision_input'] == decision
        assert not process_once(send_id, kinds=[contracts.SEND_KIND], deliver=False, workspace_id=case.workspace_id)
        assert len(received) == 1
        print('V1_LOOPBACK_RESPONSE_AND_COUNTER_PERSISTED', flush=True)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
