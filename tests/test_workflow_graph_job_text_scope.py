"""One real process() lifetime, real inert DOCX/FS, explicit runtime IO seams.

Database/auth/source/goal/Gateway are deliberate mechanical test boundaries,
not PostgreSQL, a valid lease, an actual Invocation or business acceptance.
The actual process body reaches its durable-intent commit, then stops at a
modeled Gateway source boundary without sending or fabricating a tool result.
Only the pure DOCX parser is reused; source bytes and current gates still run.
"""
import copy
import hashlib
from types import SimpleNamespace as NS
from uuid import uuid4

from fastapi import HTTPException
import pytest

from generic_agents import gateway, leases, layered_approval_store_v175 as approvals
from generic_agents import native_report_reading as reading, workspace_api
from generic_agents import workflow_document_packet_wait_v207 as documents
from generic_agents import workflow_graph as workflow
from generic_agents import workflow_graph_goal_guard_v133 as goals
from tests.test_full_semantic_graph_v1 import ability, base, control, edge
from tests.test_report_text_parse_scope import doc


class StopAtGateway(BaseException):
    """A bounded test stop, never a successful or dispatched tool result."""


@pytest.fixture
def case(tmp_path, monkeypatch):
    raw = doc('公开合成正文：纯解析复用不能替代来源与权限。')
    path = tmp_path / 'inert-original.docx'
    path.write_bytes(raw)
    c = NS(path=path, raw=raw, expected_sha=hashlib.sha256(raw).hexdigest(),
        parse_calls=[], file_reads=[], live_gates=[], gateway_entries=[], audits=[],
        allowed=True, revoke_at_commit=False, corrupt_at_commit=False, current=None,
        workspace_id=str(uuid4()))
    original_parser = reading._read_docx_uncached

    def parse(content):
        c.parse_calls.append(content)
        return original_parser(content)

    def read_source(label):
        content = c.path.read_bytes()
        c.file_reads.append((label, hashlib.sha256(content).hexdigest()))
        if hashlib.sha256(content).hexdigest() != c.expected_sha:
            raise HTTPException(409, 'actual original bytes changed')
        return reading.read_docx(content)

    class Database:
        commits = 0
        prior = None

        def scalar(self, _query):
            return self.prior

        def commit(self):
            self.commits += 1
            if c.revoke_at_commit: c.allowed = False
            if c.corrupt_at_commit: c.path.write_bytes(b'changed after durable intent')

    c.db = Database()

    def prepare_job(identity):
        node = ability('read', {})
        node['execution']['arguments'] = {'approved': True}
        graph = base([node, control('end', 'end')], [edge('read', 'end')], 'read', ['read'])
        state = workflow.graph_api.new_run(graph, {}, 10)
        activation = state['eligible_ability_ids'][0]
        inputs = workflow.graph_api.ability_inputs(graph, state, activation)
        key = 'explicit-test-boundary:' + identity
        task = NS(id='task-' + identity, workspace_id=c.workspace_id, version=1, data={'graph_runtime': state,
            'graph_jobs': {activation: {'job_id': identity, 'invocation_key': key,
                'input_hash': workflow.digest(inputs), 'phase': 'queued', 'cancel_epoch': state['cancel_epoch']}}})
        job = NS(id=identity, workspace_id=c.workspace_id, attempts=1, data={'task_id': task.id, 'activation_id': activation,
            'graph_hash': state['graph_hash'], 'input_hash': workflow.digest(inputs),
            'inputs': inputs, 'invocation_key': key, 'cancel_epoch': state['cancel_epoch']})
        cap = NS(id=node['execution']['capability_id'], workspace_id=c.workspace_id, data={'execution': {'adapter': 'builtin'},
            'budget': {'max_attempts': 2}, 'input_schema': node['execution']['input_schema']})
        release = NS(package_id='synthetic-package', workspace_id=c.workspace_id)
        package = NS(id=release.package_id, workspace_id=c.workspace_id, data={'dependencies': [cap.id]})
        c.current = NS(task=task, job=job, cap=cap, release=release, package=package,
            business={'id': 'synthetic-business'}, graph=graph,
            p=NS(user_id=node['actor_id'], workspace_id=c.workspace_id, role='operator'))
        return c.current

    def saved(db, p, identity, lock=False):
        current = c.current
        assert db is c.db and p is current.p and identity == current.task.id and lock is True
        read_source('saved_execution')
        return current.task, current.release, current.business

    def live(db, p, task, release, business):
        assert db is c.db and task is c.current.task
        c.live_gates.append(c.allowed)
        if not c.allowed: raise HTTPException(403, 'current authorization was revoked')
        read_source('live')

    def stop_gateway(db, p, capability_id, role, inputs, key, **options):
        assert db is c.db and c.db.commits >= 1
        c.gateway_entries.append(key)
        # Model only the separately repeated live/source gate after the real
        # process intent commit. No adapter or actual Gateway result is made.
        live(db, p, c.current.task, c.current.release, c.current.business)
        raise StopAtGateway()

    def published(db, p, identity, *_args):
        return c.current.package if identity == c.current.release.package_id else c.current.cap

    monkeypatch.setattr(reading, '_read_docx_uncached', parse)
    monkeypatch.setattr(workspace_api, 'saved_execution', saved)
    monkeypatch.setattr(approvals, 'guard', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow, 'graph_for', lambda *_args: copy.deepcopy(c.current.graph))
    monkeypatch.setattr(workflow, 'live', live)
    monkeypatch.setattr(workflow, 'published', published)
    monkeypatch.setattr(documents, 'preflight', lambda *_args: False)
    monkeypatch.setattr(goals, 'reserve', lambda *_args: None)
    monkeypatch.setattr(workflow, 'modality_inputs', lambda *_args: None)
    monkeypatch.setattr(workflow, 'audit', lambda *args, **kwargs: c.audits.append((args[2], args[3])))
    monkeypatch.setattr(leases, 'ownership', lambda _db: {'job_id': c.current.job.id, 'lease_token': 'test-only'})
    monkeypatch.setattr(gateway, 'invoke', stop_gateway)
    c.prepare_job = prepare_job
    return c


def test_same_job_reuses_only_parser_across_intent_commit_and_next_job_rechecks(case):
    c = case
    for identity in ('first-job', 'another-job'):
        current = c.prepare_job(identity)
        with pytest.raises(StopAtGateway): workflow.process(c.db, current.p, current.job)
    assert len(c.parse_calls) == 2
    assert len(c.file_reads) == 6 and c.live_gates == [True] * 4
    assert c.db.commits == 2 and len(c.gateway_entries) == 2
    assert all(hashed == c.expected_sha for _, hashed in c.file_reads)
    # An exception has left no reusable lifetime, even for these exact bytes.
    reading.read_docx(c.path.read_bytes())
    assert len(c.parse_calls) == 3


@pytest.mark.parametrize('fault,status', [('revoked', 403), ('changed_original', 409)])
def test_commit_does_not_cache_current_permission_or_original_file_validation(case, fault, status):
    c = case
    current = c.prepare_job('blocked-at-independent-boundary')
    c.revoke_at_commit = fault == 'revoked'
    c.corrupt_at_commit = fault == 'changed_original'
    with pytest.raises(HTTPException) as error: workflow.process(c.db, current.p, current.job)
    assert error.value.status_code == status
    assert c.db.commits == 1 and len(c.gateway_entries) == 1
    assert len(c.live_gates) == 2 and len(c.parse_calls) == 1
    assert len(c.file_reads) == (2 if fault == 'revoked' else 3)
    reading.read_docx(c.raw)
    assert len(c.parse_calls) == 2


def test_original_failed_invocation_keeps_normal_no_dispatch_return_and_clears_scope(case, monkeypatch):
    c = case
    current = c.prepare_job('original-failed-job')
    original = NS(id='original-call', status='failed', data={'error': 'saved original failure'})
    c.db.prior = original
    failures = []
    monkeypatch.setattr(workflow, 'record_failure', lambda *args: failures.append(args[-1]) or 'blocked')
    assert workflow.process(c.db, current.p, current.job) == 'blocked'
    assert failures == [original] and original.status == 'failed'
    assert c.gateway_entries == [] and c.db.commits == 0 and len(c.parse_calls) == 1
    reading.read_docx(c.raw)
    assert len(c.parse_calls) == 2
