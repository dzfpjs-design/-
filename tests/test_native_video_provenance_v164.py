"""Actual provenance helpers with explicit read-only SQL IO seams only.

These tests do not create a valid full capture/Run, sign a source, execute a
model, or prove modern reception. They exercise closed anchors, early rejection,
independent Audit lookup and the historical installation sub-check. Root owns
actual PG capture/CPU/receiver/permission and full scope-tampering acceptance.
No validator is replaced with a successful authority/context stub.
"""
from copy import deepcopy
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from generic_agents import models as m
from generic_agents import native_video_provenance_v164 as origin
from generic_agents.contracts import digest


def uid(n):
    return '164abc00-0000-4000-8000-' + f'{n:012x}'


def principal():
    return SimpleNamespace(user_id=uid(1), workspace_id=uid(2), role='admin')


class Reads:
    """A finite ordered IO script; unknown reads and every write are errors."""
    def __init__(self, *steps):
        self.steps = list(steps)

    def take(self, method, statement):
        assert self.steps, 'unexpected provenance SQL'
        expected_method, expected, result = self.steps.pop(0)
        assert method == expected_method and statement.compare(expected), 'unexpected scoped provenance query'
        actual_lock, expected_lock = statement._for_update_arg, expected._for_update_arg
        assert (actual_lock is None) is (expected_lock is None)
        if expected_lock is not None:
            assert actual_lock.read is expected_lock.read and actual_lock.key_share is expected_lock.key_share
        assert statement.get_execution_options() == expected.get_execution_options()
        return result

    def scalar(self, statement): return self.take('scalar', statement)
    def scalars(self, statement): return self.take('scalars', statement)
    def execute(self, *args, **kwargs): raise AssertionError('unexpected execute')
    def add(self, *args, **kwargs): raise AssertionError('provenance rejection/history must not add records')
    def flush(self, *args, **kwargs): raise AssertionError('provenance rejection/history must not flush')
    def commit(self): raise AssertionError('provenance helper must not commit')


def read(p, cls, identity, value, *, lock=False):
    query = select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
    if lock: query = query.with_for_update(read=True)
    return ('scalar', query.execution_options(populate_existing=True), value)


def audits(p, action, identity, values):
    return ('scalars', select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == identity).limit(2), values)


def audit(p, identity, action, target, data):
    return m.Audit(id=identity, workspace_id=p.workspace_id, actor_id=p.user_id,
        action=action, object_id=target, data=data)


def capture_case():
    p = principal()
    job = m.Job(id=uid(3), workspace_id=p.workspace_id, kind='capture_media', status='running',
        lease_token='original-capture-lease', data={'session_id': uid(4), 'actor_id': p.user_id})
    session = m.StreamSession(id=uid(4), workspace_id=p.workspace_id, channel_id=uid(5),
        evidence_id=None, video_run_id=None, status='queued', data={
            'seconds': 1, 'configuration': {'sample_hz': 1, 'presence_rule': None},
            'installation_id': uid(6), 'installation_generation': 1, 'backend': 'cpu',
            'capture_hash': 'a' * 64, 'generation': 1, 'channel_generation': 1, 'gateway_generation': 1,
            'read_reservation_id': uid(7), 'read_reservation_hash': 'b' * 64,
            'domain_binding': {'association': 'observed_scene', 'not_physical_identity': True,
                'entity_version': 1, 'binding_generation': 1, 'instance_id': uid(8)},
            'supplement_plan_id': uid(9), 'job_id': job.id})
    return p, session, job


def test_input_value_is_closed_detached_and_preserves_original_typed_request():
    p, session, job = capture_case()
    scope = {key: uid(30 + index) for index, key in enumerate(sorted(origin.SCOPE_KEYS))}
    scope.update(entity_version=1, binding_generation=1, business_id='business_synthetic',
        business_name='合成来源范围', requirements_schema='instance-data-requirements-v1')
    context = {'session': session, 'capture_job': job, 'frozen_scope': scope,
        'definition': {'synthetic': ['definition-only']}, 'entity_snapshot': {'attributes': {'zero': -0.0}},
        'installation': {'synthetic': ['pin-only']}, 'authority_pin': {'synthetic': ['pin-only']}}
    # This calls an anchor builder, not the authority validator or signer.
    before = deepcopy(session.data); original = deepcopy(scope)
    value = origin._input_value(context, job.lease_token)
    assert set(value) == {'schema', 'session_id', 'capture_job_id', 'actor_id', 'lease_token',
        'session_request', 'frozen_scope', 'definition', 'entity_snapshot', 'installation', 'authority_pin'}
    assert value['schema'] == 'native-video-capture-input-v164'
    assert value['actor_id'] == p.user_id and value['capture_job_id'] == job.id
    assert set(value['session_request']) == {'channel_id', 'seconds', 'configuration', 'installation_id',
        'installation_generation', 'backend', 'capture_hash', 'generation', 'read_reservation_id',
        'read_reservation_hash', 'domain_binding', 'supplement_plan_id', 'job_id'}
    assert type(value['session_request']['generation']) is int
    assert digest(value['entity_snapshot']) == digest(context['entity_snapshot'])
    value['session_request']['configuration']['sample_hz'] = 2
    value['frozen_scope']['entity_id'] = uid(90)
    value['authority_pin']['synthetic'].append('changed')
    assert session.data == before and scope == original
    assert context['authority_pin'] == {'synthetic': ['pin-only']}


@pytest.mark.parametrize('count', [0, 1, 2], ids=['missing', 'unique', 'duplicate'])
def test_input_audit_lookup_uses_actual_workspace_action_target_and_requires_unique(count):
    p, session, _ = capture_case()
    values = [audit(p, uid(50 + i), origin.INPUT_ACTION, session.id, {}) for i in range(count)]
    db = Reads(audits(p, origin.INPUT_ACTION, session.id, values))
    if count == 1:
        assert origin._one(db, p, origin.INPUT_ACTION, session.id) is values[0]
    else:
        with pytest.raises(HTTPException) as error:
            origin._one(db, p, origin.INPUT_ACTION, session.id)
        assert error.value.status_code == 409
    assert db.steps == []


def test_independent_input_audit_is_detected_without_any_session_json_marker():
    p, session, _ = capture_case()
    anchor = audit(p, uid(50), origin.INPUT_ACTION, session.id, {})
    db = Reads(audits(p, origin.INPUT_ACTION, session.id, [anchor]),
        audits(p, origin.INPUT_ACTION, session.id, [anchor]))
    assert origin.has_input(db, p, session) is True
    assert origin.has_input(db, p, session.id) is True
    assert db.steps == [] and origin.ISSUED not in session.data


@pytest.mark.parametrize('fault', ['missing-scope-key', 'extra-scope-key', 'scope-list', 'wrong-session', 'empty-lease'])
def test_bad_input_scope_is_rejected_before_loading_job_or_authority(fault):
    p, session, _ = capture_case()
    scope = dict.fromkeys(origin.SCOPE_KEYS, 'untrusted-placeholder')
    data = {'schema': origin.INPUT_SCHEMA, 'session_id': session.id,
        'lease_token': 'original-lease', 'frozen_scope': scope}
    if fault == 'missing-scope-key': scope.pop('entity_id')
    elif fault == 'extra-scope-key': scope['extra'] = True
    elif fault == 'scope-list': data['frozen_scope'] = []
    elif fault == 'wrong-session': data['session_id'] = uid(99)
    else: data['lease_token'] = ''
    original = deepcopy(data)
    db = Reads(audits(p, origin.INPUT_ACTION, session.id,
        [audit(p, uid(50), origin.INPUT_ACTION, session.id, data)]))
    with pytest.raises(HTTPException) as error:
        origin._input_context(db, p, session)
    assert error.value.status_code == 409 and db.steps == [] and data == original


@pytest.mark.parametrize('state', ['connecting', 'captured', 'failed', 'queued-with-evidence'])
def test_old_unanchored_capture_never_acquires_a_new_input_audit(state):
    p, session, job = capture_case()
    session.status = 'queued' if state == 'queued-with-evidence' else state
    if state in {'captured', 'queued-with-evidence'}: session.evidence_id = uid(60)
    if state == 'captured': session.video_run_id = uid(61)
    before = deepcopy(session.data)
    db = Reads(read(p, m.StreamSession, session.id, session, lock=True),
        audits(p, origin.INPUT_ACTION, session.id, []))
    assert origin.freeze_capture(db, p, job) is False
    assert db.steps == [] and session.data == before


@pytest.mark.parametrize('field,value', [
    ('generation', True), ('generation', 1.0), ('generation', '1'), ('generation', 0), ('generation', 2),
    ('installation_generation', True), ('installation_generation', 1.0), ('installation_generation', 0),
    ('channel_generation', True), ('gateway_generation', 1.0)],
    ids=['generation-bool', 'generation-float', 'generation-string', 'generation-zero', 'generation-next',
        'install-bool', 'install-float', 'install-zero', 'channel-bool', 'gateway-float'])
def test_capture_generations_are_strict_before_any_authority_io(field, value):
    p, session, job = capture_case(); session.data = {**session.data, field: value}; db = Reads()
    with pytest.raises(HTTPException) as error:
        origin._authority(db, p, session, job, live=True)
    assert error.value.status_code == 409


@pytest.mark.parametrize('field,value', [('entity_version', True), ('binding_generation', 1.0),
    ('not_physical_identity', 1), ('association', 'physical_identity')],
    ids=['entity-bool', 'binding-float', 'identity-int', 'physical-association'])
def test_bad_scene_scope_fails_before_plan_or_watch_lookup(field, value):
    p, session, job = capture_case()
    session.data = {**session.data, 'domain_binding': {**session.data['domain_binding'], field: value}}
    with pytest.raises(HTTPException) as error:
        origin._authority(Reads(), p, session, job, live=True)
    assert error.value.status_code == 409


def run_case(p):
    run = m.VideoRun(id=uid(70), workspace_id=p.workspace_id, request_key='synthetic-run-key',
        installation_id=uid(6), evidence_id=uid(60), status='queued',
        data={'source_context': {'session_id': uid(4)}, 'saved_samples': 0})
    job = m.Job(id=uid(71), workspace_id=p.workspace_id, kind='process_video', status='running',
        data={'run_id': run.id, 'actor_id': p.user_id})
    return run, job


@pytest.mark.parametrize('fault', ['already-succeeded', 'saved-result', 'bool-sample-count', 'float-sample-count', 'existing-frame'])
def test_processing_start_cannot_retrofit_a_result_or_accept_existing_frames(fault):
    p = principal(); run, job = run_case(p)
    if fault == 'already-succeeded': run.status = 'succeeded'
    elif fault == 'saved-result': run.data = {**run.data, 'result': {}}
    elif fault == 'bool-sample-count': run.data = {**run.data, 'saved_samples': False}
    elif fault == 'float-sample-count': run.data = {**run.data, 'saved_samples': 0.0}
    steps = [read(p, m.VideoRun, run.id, run), audits(p, origin.INPUT_ACTION, uid(4),
        [audit(p, uid(50), origin.INPUT_ACTION, uid(4), {})]), audits(p, origin.RESULT_ACTION, run.id, [])]
    if fault == 'existing-frame':
        steps.append(('scalar', select(m.VideoFrame.id).where(m.VideoFrame.workspace_id == p.workspace_id,
            m.VideoFrame.run_id == run.id).limit(1), uid(72)))
    db = Reads(*steps)
    with pytest.raises(HTTPException) as error:
        origin.before_result(db, p, job)
    assert error.value.status_code == 409 and db.steps == []


@pytest.mark.parametrize('hook,expected', [(origin.before_result, None), (origin.freeze_result, False)],
    ids=['before-processing', 'after-processing'])
def test_legacy_video_without_input_anchor_is_not_retroactively_signed(hook, expected):
    p = principal(); run, job = run_case(p); run.status = 'succeeded'
    before = deepcopy(run.data)
    db = Reads(read(p, m.VideoRun, run.id, run), audits(p, origin.INPUT_ACTION, uid(4), []),
        audits(p, origin.RESULT_ACTION, run.id, []))
    assert hook(db, p, job) is expected
    assert db.steps == [] and run.data == before


def test_historical_installation_pin_reads_original_audit_without_renewing_ready(monkeypatch):
    from generic_agents import local_specialists
    p = principal(); recipe = local_specialists.RECIPE
    installation = m.SpecialistInstallation(id=uid(6), workspace_id=p.workspace_id,
        pack_id=recipe['pack_id'], status='failed', data={'generation': 99})
    saved = audit(p, uid(80), 'specialist.install.ready', installation.id,
        {'recipe_hash': local_specialists.RECIPE_HASH, 'backend': 'cpu'})
    pin = {'installation_id': installation.id, 'pack_id': installation.pack_id, 'generation': 1,
        'recipe_hash': local_specialists.RECIPE_HASH, 'backend': 'cpu',
        'artifact_hashes': {item['name']: item['sha256'] for item in recipe['files']},
        'ready_audit_id': saved.id, 'ready_audit_hash': digest(saved.data)}
    before = deepcopy(pin)

    def must_not_renew(*args, **kwargs):
        raise AssertionError('historical pin must not call current ready or inspect weights')

    monkeypatch.setattr(local_specialists, 'ready_detection_install', must_not_renew)
    db = Reads(read(p, m.SpecialistInstallation, installation.id, installation), read(p, m.Audit, saved.id, saved))
    result = origin._installation(db, p, installation.id, 1, live=False, frozen=pin)
    assert result == before and result is not pin and pin == before and db.steps == []
    assert installation.status == 'failed' and installation.data == {'generation': 99}


@pytest.mark.parametrize('admin', [False, True], ids=['processor-role', 'approval-admin'])
def test_current_signer_requires_actual_active_membership_query_and_read_lock(admin):
    p = principal(); actor_id = uid(81)
    expected = select(m.User.id).join(m.Membership, m.Membership.user_id == m.User.id).where(
        m.User.id == actor_id, m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
        m.Membership.role.in_(['admin'] if admin else ['admin', 'operator'])).with_for_update(read=True)
    db = Reads(('scalar', expected, None))
    with pytest.raises(HTTPException) as error:
        origin._actor(db, p, actor_id, admin=admin)
    assert error.value.status_code == 409 and db.steps == []


@pytest.mark.parametrize('live', [1, 'true', None], ids=['int', 'string', 'null'])
def test_public_history_reader_rejects_nonboolean_current_authority_flag_before_io(live):
    with pytest.raises(HTTPException) as error:
        origin.checked_run(Reads(), principal(), uid(70), live=live)
    assert error.value.status_code == 409
