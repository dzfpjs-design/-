"""Recovery callback must not turn rejected foreign targets into loop exits."""
import copy
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import runtime_documents as d, runtime_document_receipts as receipts, models as m, worker
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.runtime_document_models import RuntimeDocumentVersion as Version, RuntimeDocumentParseAttempt as Attempt
from generic_agents.runtime_document_models import RuntimeDocumentSearch as Search
from tests.test_runtime_documents_pg_v205 import scene, instance, uploaded, response, version, search, run, BASE


@pytest.mark.parametrize('fault', ['missing_subject', 'malformed_subject', 'unknown_subject', 'foreign_subject',
    'missing_origin', 'wrong_origin', 'missing_attempt'])
def test_rejected_recovery_leaves_target_unmodified_and_original_job_finishes_failed(scene, fault):
    s = scene; release = instance(s)
    original = response(uploaded(s, release, b'Original queued document')[0], 201)
    target_id = original['latest_version_id']
    if fault == 'foreign_subject':
        with Session(engine()) as db:
            foreign = m.Workspace(name='不能恢复或修改的外域'); db.add(foreign); db.flush()
            db.add(m.Membership(user_id=s.p.user_id, workspace_id=foreign.id, role='admin')); foreign_id = foreign.id; db.commit()
        s.client.headers['X-Workspace-ID'] = foreign_id
        try:
            foreign_release = instance(s)
            target_id = response(uploaded(s, foreign_release, b'Foreign queued document')[0], 201)['latest_version_id']
        finally: s.client.headers['X-Workspace-ID'] = s.workspace
    with Session(engine()) as db:
        own = db.get(Version, original['latest_version_id']); job_id = own.data['origin']['job_id']; job = db.get(m.Job, job_id)
        if fault == 'missing_subject': job.data = {'actor_id': s.p.user_id}
        elif fault == 'malformed_subject': job.data = {**job.data, 'runtime_document_id': {'private': 'not-an-id'}}
        elif fault == 'unknown_subject': job.data = {**job.data, 'runtime_document_id': str(uuid4())}
        elif fault == 'foreign_subject': job.data = {**job.data, 'runtime_document_id': target_id}
        elif fault == 'missing_origin': own.data = {key: value for key, value in own.data.items() if key != 'origin'}
        elif fault == 'wrong_origin': own.data = {**own.data, 'origin': {**own.data['origin'], 'job_id': str(uuid4())}}
        elif fault == 'missing_attempt': own.data = {**own.data, 'latest_attempt_id': str(uuid4())}
        db.commit()
        own_pin = (own.status, own.version, copy.deepcopy(own.data))
        target = db.get(Version, target_id); target_pin = (target.status, target.version, copy.deepcopy(target.data))
    assert worker.process_once(job_id, kinds=['parse_runtime_document'], deliver=False, workspace_id=s.workspace)
    with Session(engine()) as db:
        assert db.get(m.Job, job_id).status == 'failed'
        own = db.get(Version, original['latest_version_id']); target = db.get(Version, target_id)
        assert (own.status, own.version, own.data) == own_pin and (target.status, target.version, target.data) == target_pin
        audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == s.workspace,
            m.Audit.object_id == job_id, m.Audit.action == 'runtime_document.recovery_refused_v205')))
        assert len(audits) == 1 and set(audits[0].data) == {'reason'}
        assert '目标记录未变更' in audits[0].data['reason'] and 'not-an-id' not in str(audits[0].data)
        assert db.scalar(select(func.count()).select_from(Attempt).where(Attempt.document_version_id == target_id)) == 0


def test_search_job_rejection_cannot_fail_a_different_saved_search_or_escape_worker(scene):
    s = scene; release = instance(s)
    one, two = search(s, release, 'first'), search(s, release, 'second')
    with Session(engine()) as db:
        own = db.get(Search, one['id']); job = db.get(m.Job, own.data['job_id']); job_id = job.id
        job.data = {**job.data, 'runtime_document_id': two['id']}; db.commit()
        other = db.get(Search, two['id']); pin = (other.status, other.version, copy.deepcopy(other.data))
    assert worker.process_once(job_id, kinds=['search_runtime_documents'], deliver=False, workspace_id=s.workspace)
    with Session(engine()) as db:
        other = db.get(Search, two['id'])
        assert db.get(m.Job, job_id).status == 'failed' and (other.status, other.version, other.data) == pin
        assert db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == s.workspace,
            m.Audit.object_id == job_id, m.Audit.action == 'runtime_document.recovery_refused_v205'))


def test_legitimate_missing_raw_before_attempt_stays_failed_and_retries_same_original_after_repair(scene):
    s = scene; release = instance(s); raw = b'Original file temporarily unavailable'
    document = response(uploaded(s, release, raw)[0], 201); identity = document['latest_version_id']
    with Session(engine()) as db:
        saved = db.get(Version, identity); evidence = db.get(m.Evidence, saved.evidence_id)
        original_job = saved.data['origin']['job_id']; original_origin = copy.deepcopy(saved.data['origin'])
        path = settings().storage_dir / evidence.storage_key
    held = path.with_name(path.name + '.' + uuid4().hex + '.held')
    path.rename(held)
    try:
        assert run(s, identity) == 'failed'
        failed = version(s, identity)
        assert failed['status'] == 'failed' and failed['can_retry'] and failed['latest_attempt_id'] is None
        with Session(engine()) as db:
            assert db.scalar(select(m.Audit.id).where(m.Audit.object_id == identity,
                m.Audit.action == 'runtime_document.worker_interrupted'))
            assert db.get(Version, identity).data['origin'] == original_origin
    finally: held.replace(path)
    request = {'request_id': str(uuid4()), 'expected_version': failed['version'], 'reason': '原字节恢复后沿原版本明确重试'}
    response(s.client.post(BASE + '/versions/' + identity + '/retry', json=request))
    assert run(s, identity) == 'succeeded'; receipts.consume_once(s.workspace)
    received = version(s, identity)
    assert received['status'] == 'parsed' and received['delivery']['verified']
    with Session(engine()) as db:
        saved = db.get(Version, identity)
        assert saved.data['origin']['job_id'] == original_job and saved.data['origin'] == original_origin
        assert db.scalar(select(func.count()).select_from(m.Audit).where(m.Audit.object_id == identity,
            m.Audit.action == 'runtime_document.worker_interrupted')) == 1
    assert s.client.get(BASE + '/versions/' + identity + '/original').content == raw
