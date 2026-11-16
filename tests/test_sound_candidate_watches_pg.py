"""Normal CPU/registered Task/source/ACK chain; synthetic WAV is not field quality.

No fake inference, receipt or Signal success. Root runs this in the prepared
isolated sound runtime; exact Job IDs and explicitly scoped delivery are used.
"""
import copy
import hashlib
import json
from uuid import uuid4
import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from generic_agents import models as m
from generic_agents.db import engine
from generic_agents.contracts import digest
from generic_agents.worker import process_once, deliver_events
from generic_agents import sound_candidate_watches as sw
from tests.test_sound_inputs_pg import original_scene, good
from tests.test_workflow_sound_native_pg import sound_scene, ack


def base(s):
    return '/api/workspace/instances/' + s.current_release['instance_id'] + '/sound-candidate-watches'


def options(s):
    return good(s.client.get(base(s) + '/options'))


def target(s, opts=None):
    opts = opts or options(s); assert opts['can_create'], opts['reason']
    business = next(b for b in opts['businesses'] if b['id'] == s.current_release['available_business_ids'][0])
    cap = next(c for c in opts['capabilities'] if c['id'] in business['capability_ids'])
    entity = next(e for e in opts['entities'] if e['id'] == s.entity['id'])
    return {'instance_version': opts['instance_version'], 'release_id': opts['release_id'],
        'configuration_hash': opts['configuration_hash'], 'business_id': business['id'],
        'entity_id': entity['id'], 'entity_version': entity['version'], 'entity_definition_hash': entity['definition_hash'],
        'capability_id': cap['id'], 'capability_version': cap['version'], 'capability_hash': cap['content_hash'],
        'labels_sha256': cap['labels_sha256']}


def create_body(s):
    scores = s.run['result']['classification']['windows'][0]['scores']
    index = max(range(521), key=lambda i: scores[i]); assert scores[index] > 0
    return {'schema': 'sound-candidate-watch-create-v1', 'request_id': '声音/关注?保留-' + uuid4().hex,
        'name': '工程声音类别关注，不认定现场异常', 'target_pins': target(s), 'class_index': index,
        'min_score_text': '1e-9999', 'min_windows': 1, 'confirm_candidate_only': True}


def raw_post(s, path, body):
    raw = json.dumps(body, ensure_ascii=False, indent=2) + '\n'
    return s.client.post(path, content=raw.encode(), headers={'Content-Type': 'application/json'}), raw


def create(s):
    body = create_body(s); response, raw = raw_post(s, base(s), body)
    return good(response), body, raw


def existing(s, rule, *, nonce=None):
    body = {'schema': 'sound-candidate-watch-check-v1', 'request_id': nonce or 'explicit-existing-' + uuid4().hex,
        'version': rule['version'], 'target_pins': target(s), 'before_run_id': None,
        'confirm_existing_received_only': True}
    response, raw = raw_post(s, base(s) + '/' + rule['id'] + '/check-existing', body)
    return good(response), body, raw


def count(db, cls):
    return db.scalar(select(func.count()).select_from(cls))


def finish_checks(s, receipt):
    for identity in receipt['job_ids']: assert process_once(job_id=identity, deliver=False)
    checks = good(s.client.get(base(s) + '/' + receipt['watch_id'] + '/checks'))
    for record in checks['records']:
        assert record['status'] == 'succeeded', {k: record[k] for k in ('id', 'status', 'error', 'integrity_issue')}
        assert record['integrity_issue'] is None and record['result'] is not None
    return checks


def test_real_ack_old_registration_requires_explicit_check_then_only_one_inbox_signal(sound_scene):
    s = sound_scene
    paths = s.client.app.openapi()['paths']
    assert base(s).replace(s.current_release['instance_id'], '{identity}') + '/options' in paths
    with Session(engine()) as db:
        before_jobs = count(db, m.Job); before_tasks = count(db, m.Task)
        assert not db.scalars(select(m.PerceptionSignal)).all()
    made, body, raw = create(s); rule = made['rule']
    before, _, _ = existing(s, rule)
    assert before['receipt']['job_ids'] == [] and before['receipt']['not_received_run_ids'] == [s.run['id']]
    assert before['receipt']['blocked_run_ids'] == []
    received = ack(s); assert received['reception']['state'] == 'received'
    # Registration preceded enablement. Late ACK is not silently a new registration.
    with Session(engine()) as db:
        assert not db.scalars(select(m.Job).where(m.Job.idempotency_key.like('sound-candidate-check:%'))).all()
    checked, check_body, check_raw = existing(s, rule)
    assert len(checked['receipt']['job_ids']) == 1
    finished_checks = finish_checks(s, checked['receipt'])
    assert finished_checks['records'][0]['delivery_state'] == 'pending_delivery'
    result = finished_checks['records'][0]['result']
    assert result['matched'] and result['matched_window_count'] == 1
    deliver_events(workspace_id=received['run']['sound_snapshot']['workspace_id'],
        event_kind='perception.candidate')
    with Session(engine()) as db:
        signals = db.scalars(select(m.PerceptionSignal)).all(); assert len(signals) == 1
        signal_id = signals[0].id; event_id = signals[0].event_id
        reference = signals[0].data['basis']['original_model_result']
        assert reference['schema'] == 'sound-candidate-signal-reference-v1' and reference['family'] == 'environment_sound'
        assert reference['task_id'] == s.task['id'] and reference['physical_state_verified'] is False
        assert count(db, m.Task) == before_tasks and not db.scalars(select(m.CloudCall)).all()
    delivered_checks = good(s.client.get(base(s) + '/' + rule['id'] + '/checks'))
    assert delivered_checks['records'][0]['delivery_state'] == 'received' and delivered_checks['records'][0]['delivery_error'] is None
    evidence = good(s.client.get(reference['notification_evidence_path']))
    assert evidence['reception']['state'] == 'received' and evidence['run']['id'] == s.run['id']
    assert evidence['sound_input']['id'] == s.attached['id']
    assert evidence['matched_windows'][0]['score'] == s.run['result']['classification']['windows'][0]['scores'][body['class_index']]
    assert evidence['physical_state_verified'] is False and evidence['recording_time_verified'] is False
    assert evidence['window_semantics'] == 'overlapping_analysis_windows_not_duration_or_sources'
    assert good(s.client.post(base(s) + '/' + rule['id'] + '/check-existing', content=check_raw,
        headers={'Content-Type': 'application/json'}))['receipt'] == checked['receipt']
    reviewed = good(s.client.post('/api/sounds/runs/' + s.run['id'] + '/reviews', json={
        'schema': 'sound-review-v1', 'request_id': 'review-after-notice-' + uuid4().hex, 'version': s.run['version'],
        'verdict': 'uncertain', 'note': '人工记录不改机器来源，不再次提醒。'}), 201)
    assert reviewed['note']
    again, _, _ = existing(s, rule); finish_checks(s, again['receipt'])
    deliver_events(workspace_id=received['run']['sound_snapshot']['workspace_id'],
        event_kind='perception.candidate')
    with Session(engine()) as db:
        assert count(db, m.PerceptionSignal) == 1 and count(db, m.Task) == before_tasks
        assert len(db.scalars(select(m.Event).where(m.Event.idempotency_key.like('candidate-sound:%'))).all()) == 1


@pytest.mark.parametrize('stop_before_check', [False, True])
def test_enabled_rule_checks_new_registered_result_only_after_real_ack_and_pause_blocks_queued_work(sound_scene, stop_before_check):
    s = sound_scene; made, _, _ = create(s); rule = made['rule']
    body = {'release_id': s.current_release['id'], 'business_id': s.current_release['available_business_ids'][0],
        'entity_ids': [s.entity['id']], 'sound_ids': [s.attached['id']], 'title': '启用后新登记声音成果', 'request_id': str(uuid4())}
    task = good(s.client.post('/api/workspace/instances/' + s.current_release['instance_id'] + '/tasks', json=body), 201)
    assert process_once(job_id=task['workflow_runtime']['automatic']['job_id'], deliver=False)
    entry = good(s.client.get('/api/workspace/tasks/' + task['id'] + '/sounds'))['records'][0]
    with Session(engine()) as db:
        assert not db.scalars(select(m.Job).where(m.Job.idempotency_key.like('sound-candidate-check:%'))).all()
    deliver_events(workspace_id=entry['run']['sound_snapshot']['workspace_id'],
        observation_id=entry['reception']['observation_id'], event_kind='observation.data')
    with Session(engine()) as db:
        jobs = db.scalars(select(m.Job).where(m.Job.idempotency_key.like('sound-candidate-check:%'))).all()
        assert len(jobs) == 1; job_id = jobs[0].id
    pause = {'schema': 'sound-candidate-watch-status-v1', 'request_id': 'pause-queued-' + uuid4().hex,
        'version': rule['version'], 'status': 'paused', 'target_pins': None}
    if stop_before_check:
        stopped, _ = raw_post(s, base(s) + '/' + rule['id'] + '/status', pause)
        assert good(stopped)['rule']['status'] == 'paused'
    assert process_once(job_id=job_id, deliver=False)
    deliver_events(workspace_id=entry['run']['sound_snapshot']['workspace_id'], event_kind='perception.candidate')
    with Session(engine()) as db:
        job = db.get(m.Job, job_id)
        assert job.status == ('failed' if stop_before_check else 'succeeded'), {'status': job.status, 'error': job.data.get('error')}
        signals = db.scalars(select(m.PerceptionSignal)).all()
        events = db.scalars(select(m.Event).where(m.Event.idempotency_key.like('candidate-sound:%'))).all()
        assert len(signals) == len(events) == (0 if stop_before_check else 1)
        assert count(db, m.Task) == 2
        if signals:
            assert signals[0].data['basis']['original_model_result']['task_id'] == task['id']


def test_raw_nonce_recovery_and_monotonic_cas_keep_paused_originals_and_reject_cross_operation(sound_scene):
    s = sound_scene; made, body, raw = create(s); rule = made['rule']
    params = {'request_id': body['request_id'], 'raw_sha256': hashlib.sha256(raw.encode()).hexdigest(),
        'expected_target': json.dumps(body['target_pins'])}
    original = good(s.client.get(base(s) + '/requests', params=params))
    assert original['found'] and original['original_raw'] == raw and original['receipt'] == made['receipt']
    with Session(engine()) as db:
        quantities = {cls.__name__: count(db, cls) for cls in (m.Job, m.Audit, m.CandidateWatch, m.Event)}
    for _ in range(2): assert good(s.client.get(base(s) + '/requests', params=params)) == original
    pause = {'schema': 'sound-candidate-watch-status-v1', 'request_id': 'pause-original-' + uuid4().hex,
        'version': rule['version'], 'status': 'paused', 'target_pins': None}
    stopped, pause_raw = raw_post(s, base(s) + '/' + rule['id'] + '/status', pause); saved = good(stopped)
    recovery = good(s.client.get(base(s) + '/' + rule['id'] + '/requests', params={
        'request_id': pause['request_id'], 'raw_sha256': hashlib.sha256(pause_raw.encode()).hexdigest(),
        'operation': 'status', 'expected_version': pause['version']}))
    assert recovery['found'] and recovery['receipt'] == saved['receipt'] and recovery['original_raw'] == pause_raw
    assert good(s.client.get(base(s) + '/requests', params=params))['receipt'] == made['receipt']
    assert s.client.post(base(s), content=raw + ' ', headers={'Content-Type': 'application/json'}).status_code == 409
    stale = {**pause, 'request_id': 'unknown-paused-' + uuid4().hex}
    stale_raw = json.dumps(stale)
    unknown = good(s.client.get(base(s) + '/' + rule['id'] + '/requests', params={
        'request_id': stale['request_id'], 'raw_sha256': hashlib.sha256(stale_raw.encode()).hexdigest(),
        'operation': 'status', 'expected_version': stale['version']}))
    assert not unknown['found'] and unknown['superseded'] and unknown['current_version'] > unknown['expected_version']
    assert s.client.post(base(s) + '/' + rule['id'] + '/status', content=stale_raw,
        headers={'Content-Type': 'application/json'}).status_code == 409
    switched = {**pause, 'request_id': body['request_id']}
    response, _ = raw_post(s, base(s) + '/' + rule['id'] + '/status', switched)
    assert response.status_code == 409
    current_instance = next(v for v in good(s.client.get('/api/instances')) if v['id'] == s.current_release['instance_id'])
    paused = good(s.client.post('/api/instances/' + current_instance['id'] + '/pause', json={'version': current_instance['version']}))
    assert paused['status'] == 'paused'
    assert not options(s)['can_create']
    assert good(s.client.get(base(s) + '/requests', params=params))['receipt'] == made['receipt']
    assert good(s.client.get(base(s) + '/' + rule['id']))['status'] == 'paused'


@pytest.mark.parametrize('damage', ['memory', 'command_audit', 'definition', 'issued_audit'])
def test_bad_ack_or_rule_or_notification_proof_cannot_become_an_inbox_candidate(sound_scene, damage):
    s = sound_scene; made, _, _ = create(s); rule = made['rule']; received = ack(s)
    if damage in {'memory', 'command_audit', 'definition'}:
        with Session(engine()) as db:
            if damage == 'memory': db.delete(db.get(m.Memory, received['reception']['memory_id']))
            elif damage == 'command_audit':
                proof = db.scalar(select(m.Audit).where(m.Audit.action == sw.COMMAND, m.Audit.object_id == rule['id']))
                db.delete(proof)
            else:
                watch = db.get(m.CandidateWatch, rule['id']); data = copy.deepcopy(watch.data)
                data[sw.KEY]['definition']['min_windows'] = 2; data[sw.KEY]['definition_hash'] = digest(data[sw.KEY]['definition'])
                watch.data = data
            db.commit()
        response, _ = raw_post(s, base(s) + '/' + rule['id'] + '/check-existing', {
            'schema': 'sound-candidate-watch-check-v1', 'request_id': 'damaged-check-' + uuid4().hex,
            'version': rule['version'], 'target_pins': made['rule']['access']['current_target_pins'],
            'before_run_id': None, 'confirm_existing_received_only': True})
        if damage == 'memory':
            value = good(response); assert value['receipt']['job_ids'] == [] and value['receipt']['blocked_run_ids'] == [s.run['id']]
        else: assert response.status_code == 409
    else:
        checked, _, _ = existing(s, rule); finish_checks(s, checked['receipt'])
        with Session(engine()) as db:
            event = db.scalar(select(m.Event).where(m.Event.idempotency_key.like('candidate-sound:%')))
            proof = db.scalar(select(m.Audit).where(m.Audit.action == sw.ISSUED, m.Audit.object_id == event.id)); db.delete(proof); db.commit()
        deliver_events(workspace_id=received['run']['sound_snapshot']['workspace_id'],
            event_kind='perception.candidate')
    with Session(engine()) as db: assert not db.scalars(select(m.PerceptionSignal)).all()



def test_without_any_confirmed_rule_real_sound_ack_creates_no_check_job_or_notification(sound_scene):
    s = sound_scene; received = ack(s)
    assert received['reception']['state'] == 'received'
    deliver_events(workspace_id=received['run']['sound_snapshot']['workspace_id'], event_kind='perception.candidate')
    with Session(engine()) as db:
        assert not db.scalars(select(m.Job).where(m.Job.idempotency_key.like('sound-candidate-check:%'))).all()
        assert not db.scalars(select(m.PerceptionSignal)).all()
        assert count(db, m.Task) == 1



def test_pause_after_successful_filter_keeps_job_result_but_exposes_blocked_delivery(sound_scene):
    s = sound_scene; made, _, _ = create(s); rule = made['rule']; received = ack(s)
    result, _, _ = existing(s, rule); finished = finish_checks(s, result['receipt'])
    assert finished['records'][0]['status'] == 'succeeded' and finished['records'][0]['delivery_state'] == 'pending_delivery'
    pause = {'schema': 'sound-candidate-watch-status-v1', 'request_id': 'pause-before-delivery-' + uuid4().hex,
        'version': rule['version'], 'status': 'paused', 'target_pins': None}
    response, _ = raw_post(s, base(s) + '/' + rule['id'] + '/status', pause); assert good(response)['rule']['status'] == 'paused'
    deliver_events(workspace_id=received['run']['sound_snapshot']['workspace_id'], event_kind='perception.candidate')
    checks = good(s.client.get(base(s) + '/' + rule['id'] + '/checks'))
    record = checks['records'][0]
    assert record['status'] == 'succeeded' and record['result'] == finished['records'][0]['result']
    assert record['delivery_state'] == 'blocked_delivery' and record['delivery_error']
    with Session(engine()) as db:
        assert not db.scalars(select(m.PerceptionSignal)).all()
        assert count(db, m.Task) == 1



def test_explicit_same_epoch_check_recovers_original_blocked_notification_without_model_or_task_rerun(sound_scene, monkeypatch):
    from fastapi import HTTPException
    from generic_agents import perception_signals
    s = sound_scene; made, _, _ = create(s); rule = made['rule']; received = ack(s)
    checked, _, _ = existing(s, rule); first = finish_checks(s, checked['receipt'])['records'][0]
    event_id = first['result']['event_id']; real_receive = perception_signals.receive
    def temporarily_unavailable(db, event):
        raise HTTPException(409, 'injected bounded recipient failure before commit')
    with monkeypatch.context() as patch:
        patch.setattr(perception_signals, 'receive', temporarily_unavailable)
        deliver_events(workspace_id=received['run']['sound_snapshot']['workspace_id'], event_kind='perception.candidate')
    with Session(engine()) as db:
        before = {cls.__name__: count(db, cls) for cls in (m.Task, m.Invocation, m.Event)}
        event = db.get(m.Event, event_id)
        assert event.status == 'blocked_delivery' and event.attempts == 1 and event.data['delivery_error']
        assert not db.scalars(select(m.PerceptionSignal)).all()
    original = good(s.client.get(base(s) + '/' + rule['id'] + '/checks'))['records'][0]
    assert original['delivery_state'] == 'blocked_delivery' and original['status'] == 'succeeded'
    retry, _, _ = existing(s, rule); finished = finish_checks(s, retry['receipt'])
    assert finished['records'][0]['result']['event_id'] == event_id
    assert finished['records'][0]['delivery_state'] == 'pending_delivery'
    with Session(engine()) as db:
        event = db.get(m.Event, event_id)
        assert event.attempts == 1 and event.data['previous_delivery_error']
        proofs = db.scalars(select(m.Audit).where(m.Audit.action == sw.REQUEUED, m.Audit.object_id == event_id)).all()
        assert len(proofs) == 1 and proofs[0].data['request_id'] == retry['receipt']['request_id']
    deliver_events(workspace_id=received['run']['sound_snapshot']['workspace_id'], event_kind='perception.candidate')
    with Session(engine()) as db:
        event = db.get(m.Event, event_id)
        assert event.status == 'received' and event.attempts == 2
        assert {cls.__name__: count(db, cls) for cls in (m.Task, m.Invocation, m.Event)} == before
        assert count(db, m.PerceptionSignal) == 1
    assert all(row['delivery_state'] == 'received' for row in good(s.client.get(base(s) + '/' + rule['id'] + '/checks'))['records'])



@pytest.mark.parametrize('pause_while_finishing', [False, True])
def test_requeue_handler_commit_waits_for_actual_new_check_finalizer_without_consuming_delivery_attempt(sound_scene, monkeypatch, pause_while_finishing):
    """Observe the real worker commit boundary; do not handcraft Job/ACK status."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event as Barrier
    from sqlalchemy import event as sa_event
    from fastapi import HTTPException
    from generic_agents import perception_signals
    from generic_agents.security import Principal
    from generic_agents.worker import actor
    s = sound_scene; made, _, _ = create(s); rule = made['rule']; received = ack(s)
    initial, _, _ = existing(s, rule); finished = finish_checks(s, initial['receipt'])
    event_id = finished['records'][0]['result']['event_id']
    def unavailable(db, event): raise HTTPException(409, 'injected bounded recipient failure')
    with monkeypatch.context() as patch:
        patch.setattr(perception_signals, 'receive', unavailable)
        deliver_events(workspace_id=received['run']['sound_snapshot']['workspace_id'], event_kind='perception.candidate')
    retried, _, _ = existing(s, rule); retry_job_id = retried['receipt']['job_ids'][0]
    committed = Barrier(); release = Barrier(); real_process = sw.process
    def observed_process(db, p, job):
        result = real_process(db, p, job)
        if job.id == retry_job_id: db.info['sound_test_handler_commit'] = retry_job_id
        return result
    def after_commit(db):
        if db.info.pop('sound_test_handler_commit', None) == retry_job_id:
            committed.set()
            assert release.wait(60), 'test finalizer barrier was not released'
    monkeypatch.setattr(sw, 'process', observed_process)
    sa_event.listen(Session, 'after_commit', after_commit)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(process_once, job_id=retry_job_id, deliver=False)
            try:
                assert committed.wait(60), 'real handler did not reach its committed result checkpoint'
                with Session(engine()) as db:
                    job = db.get(m.Job, retry_job_id); event = db.get(m.Event, event_id)
                    assert job.status == 'running' and event.status == 'pending_delivery' and event.attempts == 1
                    assert event.data['sound_requeue_audits_v1']
                if pause_while_finishing:
                    response, _ = raw_post(s, base(s) + '/' + rule['id'] + '/status', {
                        'schema': 'sound-candidate-watch-status-v1', 'request_id': 'pause-during-finalizer-' + uuid4().hex,
                        'version': rule['version'], 'status': 'paused', 'target_pins': None})
                    assert good(response)['rule']['status'] == 'paused'
                else:
                    with Session(engine()) as db:
                        # The direct path also checks current authorization and
                        # every original command/queue/result/ACK anchor first.
                        assert sw.prepare_delivery(db, event_id, received['run']['sound_snapshot']['workspace_id']) is None
                deliver_events(workspace_id=received['run']['sound_snapshot']['workspace_id'], event_kind='perception.candidate')
                with Session(engine()) as db:
                    event = db.get(m.Event, event_id)
                    assert event.status == ('blocked_delivery' if pause_while_finishing else 'pending_delivery')
                    assert event.attempts == (2 if pause_while_finishing else 1)
                    assert not db.scalars(select(m.PerceptionSignal)).all()
            finally:
                release.set()
            assert future.result(timeout=90)
    finally:
        release.set(); sa_event.remove(Session, 'after_commit', after_commit)
    with Session(engine()) as db: assert db.get(m.Job, retry_job_id).status == 'succeeded'
    deliver_events(workspace_id=received['run']['sound_snapshot']['workspace_id'], event_kind='perception.candidate')
    with Session(engine()) as db:
        event = db.get(m.Event, event_id)
        assert event.status == ('blocked_delivery' if pause_while_finishing else 'received')
        assert event.attempts == 2
        assert count(db, m.PerceptionSignal) == (0 if pause_while_finishing else 1)
        assert count(db, m.Task) == count(db, m.Invocation) == 1


def test_recovery_rechecks_nonce_after_original_commit_and_normal_entity_version_advance(sound_scene, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event as Barrier
    s = sound_scene; body = create_body(s); raw = json.dumps(body, ensure_ascii=False, indent=2) + '\n'
    params = {'request_id': body['request_id'], 'raw_sha256': hashlib.sha256(raw.encode()).hexdigest(),
        'expected_target': json.dumps(body['target_pins'])}
    first_empty = Barrier(); advanced = Barrier(); real_superseded = sw._superseded
    def observe_superseded(db, p, instance, expected):
        first_empty.set()
        assert advanced.wait(60), 'original POST / actual target advance did not complete'
        return real_superseded(db, p, instance, expected)
    monkeypatch.setattr(sw, '_superseded', observe_superseded)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(s.client.get, base(s) + '/requests', params=params)
        try:
            assert first_empty.wait(60), 'GET did not reach the first unknown observation'
            made = good(s.client.post(base(s), content=raw.encode(), headers={'Content-Type': 'application/json'}))
            attributes = copy.deepcopy(s.entity['attributes']); field = next(iter(attributes))
            attributes[field] = str(attributes[field]) + '（正常更新）'
            changed = good(s.client.patch('/api/entities/' + s.entity['id'], json={
                'expected_version': s.entity['version'], 'attributes': attributes, 'reason': '原申请提交后正常更新对象'}))
            assert changed['version'] > body['target_pins']['entity_version']
        finally:
            advanced.set()
        recovered = good(future.result(timeout=90))
    assert recovered['found'] and recovered['superseded'] is False
    assert recovered['original_raw'] == raw and recovered['receipt'] == made['receipt']
    assert recovered['body_hash'] == made['receipt']['body_hash']
