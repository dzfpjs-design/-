"""Human-journal invariants with explicit SELECT IO, not video/PG acceptance.

No successful VideoRun or machine validator is fabricated. Root's separate
actual capture/CPU/receipt journey provides the video provenance evidence.
"""
import copy
import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.native_video_review_contracts_v165 import parse_wire, Draft, wire_sha256, command_path
from generic_agents.native_video_review_models_v165 import NativeVideoReview as Review, NativeVideoReviewCommand as Command
from generic_agents import native_video_review_store_v165 as store

WS = '10000000-0000-4000-8000-000000000001'
ACTOR = '10000000-0000-4000-8000-000000000002'
INSTANCE = '10000000-0000-4000-8000-000000000003'
OBS = '10000000-0000-4000-8000-000000000004'
RUN = '10000000-0000-4000-8000-000000000005'
RELEASE = '10000000-0000-4000-8000-000000000006'
FRAME = '10000000-0000-4000-8000-000000000007'
REVIEW = '10000000-0000-4000-8000-000000000008'
COMMAND = '10000000-0000-4000-8000-000000000009'
AUDIT = '10000000-0000-4000-8000-000000000010'
P = NS(workspace_id=WS, user_id=ACTOR, role='operator')
SCOPE = {'instance_id': INSTANCE, 'observation_id': OBS, 'run_id': RUN,
    'release_id': RELEASE, 'receipt_hash': 'a' * 64}
WHEN = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)


def human_inventory():
    raw = ('{ "request_id":"video-review-original-001", "expected_version":0,'
        ' "note":"原人工理由，不代表现场事实", "frame_ids":["' + FRAME + '"], "decision":null }').encode()
    body = parse_wire(raw, Draft).model_dump()
    opinion = {key: copy.deepcopy(body[key]) for key in ('note', 'frame_ids', 'decision')}
    state = {'schema': store.STATE_SCHEMA, 'scope': copy.deepcopy(SCOPE), 'actor_id': ACTOR,
        'version': 1, 'status': 'draft', 'draft': opinion, 'allowed_frame_ids': [FRAME],
        'human_history': [{'version': 1, 'operation': 'draft', 'actor_id': ACTOR,
            **copy.deepcopy(opinion), 'saved_at': WHEN.isoformat()}]}
    review = Review(id=REVIEW, workspace_id=WS, actor_id=ACTOR, instance_id=INSTANCE,
        observation_id=OBS, run_id=RUN, release_id=RELEASE, receipt_hash=SCOPE['receipt_hash'],
        version=1, status='draft', data=state, record_hash='')
    snapshot = {'id': REVIEW, 'workspace_id': WS, **SCOPE, 'actor_id': ACTOR,
        'version': 1, 'status': 'draft', 'data': copy.deepcopy(state)}
    review.record_hash = digest(snapshot)
    command = Command(id=COMMAND, workspace_id=WS, actor_id=ACTOR, instance_id=INSTANCE,
        observation_id=OBS, review_id=REVIEW, record_version=1,
        request_id=body['request_id'], operation='draft', method='POST',
        path=command_path(INSTANCE, OBS, 'draft'), wire_sha256=wire_sha256(raw),
        body_hash=digest(body), created_at=WHEN, command_hash='', data={
            'schema': 'native-video-review-command-v165', 'wire': raw.decode(),
            'body': body, 'record_snapshot': snapshot, 'previous_hash': None,
            'result': {'observation_id': OBS, 'version': 1, 'operation': 'draft', 'status': 'draft'}})
    command.command_hash = digest(store._command_pin(command))
    saved_audit = m.Audit(id=AUDIT, workspace_id=WS, actor_id=ACTOR,
        action=store.ACTION, object_id=COMMAND, data=store._audit_pin(command))
    return review, command, saved_audit


class InventoryIO:
    def __init__(self, reviews=(), commands=(), audits=()):
        self.rows = {Review: list(reviews), Command: list(commands), m.Audit: list(audits)}
        self.reads = []

    def execute(self, statement, params):
        expected_key = int(digest({'native_video_review_observation_v165': OBS,
            'workspace_id': WS, 'instance_id': INSTANCE})[:16], 16) - (1 << 63)
        assert str(statement) == 'SELECT pg_advisory_xact_lock_shared(:key)'
        assert params == {'key': expected_key}

    def scalars(self, statement):
        entity = statement.column_descriptions[0]['entity']
        assert entity in self.rows, 'only the actual journal inventory SELECTs are supplied'
        params = statement.compile().params
        flattened = []
        for value in params.values():
            flattened.extend(value if isinstance(value, list) else [value])
        assert WS in flattened and OBS in flattened, 'actual scoped inventory query is retained'
        if entity is m.Audit:
            assert store.ACTION in flattened
        self.reads.append(entity)
        return self.rows[entity]


def test_actual_complete_journal_inventory_is_read_only_and_preserves_original_wire():
    review, command, saved_audit = human_inventory()
    before = copy.deepcopy((review.data, command.data, saved_audit.data))
    db = InventoryIO([review], [command], [saved_audit])
    rows, commands = store._inventory(db, P, INSTANCE, OBS)
    assert rows == [review] and commands == [command]
    assert db.reads == [Review, Command, m.Audit]
    assert (review.data, command.data, saved_audit.data) == before
    assert command.data['wire'].startswith('{ "request_id"')


@pytest.mark.parametrize('missing', ['review', 'command', 'audit'])
def test_any_independent_missing_edge_cannot_become_empty_version_zero(missing):
    review, command, saved_audit = human_inventory()
    db = InventoryIO([] if missing == 'review' else [review],
        [] if missing == 'command' else [command], [] if missing == 'audit' else [saved_audit])
    with pytest.raises(HTTPException) as error:
        store._inventory(db, P, INSTANCE, OBS)
    assert error.value.status_code == 409


@pytest.mark.parametrize('fault', ['actor', 'wire', 'scope', 'state', 'audit', 'version'])
def test_self_resealed_command_cannot_replace_independent_human_audit(fault):
    review, command, saved_audit = human_inventory()
    if fault == 'actor':
        command.actor_id = '20000000-0000-4000-8000-000000000002'
    elif fault == 'wire':
        data = copy.deepcopy(command.data)
        data['wire'] = json.dumps(data['body'], ensure_ascii=False, separators=(',', ':'))
        command.wire_sha256 = wire_sha256(data['wire'].encode()); command.data = data
    elif fault == 'scope':
        command.instance_id = '20000000-0000-4000-8000-000000000003'
    elif fault == 'state':
        data = copy.deepcopy(command.data)
        data['record_snapshot']['data']['draft']['note'] = '另一条没有独立签收的人工理由'
        command.data = data
    elif fault == 'audit':
        saved_audit.actor_id = '20000000-0000-4000-8000-000000000002'
    else:
        review.version = 2
    command.command_hash = digest(store._command_pin(command))
    with pytest.raises(HTTPException) as error:
        store._inventory(InventoryIO([review], [command], [saved_audit]), P, INSTANCE, OBS)
    assert error.value.status_code == 409


def test_saving_another_draft_retains_prior_human_submission_without_mutating_it():
    submitted = {'version': 1, 'operation': 'submit', 'actor_id': ACTOR,
        'note': '已看原画面，仍不能认定现场事实', 'frame_ids': [FRAME],
        'decision': 'uncertain', 'saved_at': WHEN.isoformat()}
    previous = {'schema': store.STATE_SCHEMA, 'scope': copy.deepcopy(SCOPE), 'actor_id': ACTOR,
        'version': 1, 'status': 'reviewed', 'draft': {key: copy.deepcopy(submitted[key])
            for key in ('note', 'frame_ids', 'decision')}, 'human_history': [submitted], 'allowed_frame_ids': [FRAME]}
    original = copy.deepcopy(previous)
    body = {'expected_version': 1, 'note': '尚未提交的新稿', 'frame_ids': [], 'decision': None}
    next_state = store._transition(previous, SCOPE, [FRAME], ACTOR, body, 'draft',
        (WHEN + timedelta(seconds=1)).isoformat())
    assert previous == original and next_state['status'] == 'draft'
    assert next_state['human_history'][0] == submitted
    assert next_state['draft']['note'] == '尚未提交的新稿'
    next_state['human_history'][0]['frame_ids'].clear()
    assert previous['human_history'][0]['frame_ids'] == [FRAME]


@pytest.mark.parametrize('version', [True, 1.0, 0, 2])
def test_transition_cas_is_strict_and_never_changes_source_or_prior_draft(version):
    review, _, _ = human_inventory()
    original = copy.deepcopy(review.data)
    body = {'expected_version': version, 'note': '本机修改仍保留', 'frame_ids': [FRAME], 'decision': 'uncertain'}
    with pytest.raises(HTTPException) as error:
        store._transition(review.data, SCOPE, [FRAME], ACTOR, body, 'submit', WHEN.isoformat())
    assert error.value.status_code == 409 and review.data == original


def test_saved_envelope_is_only_original_reference_and_unknown_contains_no_result():
    _, command, _ = human_inventory()
    assert store._envelope(command.request_id, command.wire_sha256, command) == {
        'schema': store.REQUEST_SCHEMA, 'request_id': command.request_id,
        'wire_sha256': command.wire_sha256, 'outcome': 'saved',
        'result': {'observation_id': OBS, 'version': 1, 'operation': 'draft', 'status': 'draft'}}
    assert store._envelope(command.request_id, command.wire_sha256)['result'] is None
    assert store._envelope(command.request_id, command.wire_sha256)['outcome'] == 'unknown'


def test_original_saved_clock_hash_is_stable_across_session_timezone_display():
    _, command, saved_audit = human_inventory()
    original = command.command_hash
    command.created_at = WHEN.astimezone(timezone(timedelta(hours=-7)))
    assert digest(store._command_pin(command)) == original
    assert store._checked_command(command, saved_audit)['note'] == '原人工理由，不代表现场事实'


def test_write_permission_uses_actual_session_validator_and_rejects_before_instance_io():
    principal = NS(workspace_id=WS, user_id=ACTOR, role='operator',
        session_id='10000000-0000-4000-8000-000000000099')

    class DeniedSessionIO:
        def execute(self, statement):
            entities = {item['entity'] for item in statement.column_descriptions}
            assert {m.User, m.Membership, m.AuthSession} <= entities
            params = statement.compile().params.values()
            assert principal.session_id in params and ACTOR in params and WS in params
            assert statement._for_update_arg is not None and statement._for_update_arg.read is True
            return NS(first=lambda: None)

        def scalar(self, statement):
            pytest.fail('a rejected current session must not progress to Instance or source gates')

    with pytest.raises(HTTPException) as error:
        store._permission(DeniedSessionIO(), principal, {}, lock=True)
    assert error.value.status_code == 403


def test_missing_command_and_audit_still_cannot_hide_a_retained_review_as_unknown():
    review, command, _ = human_inventory()

    class MissingCommandIO:
        def scalar(self, statement):
            assert statement.column_descriptions[0]['entity'] is m.DomainInstance
            return NS(id=INSTANCE, workspace_id=WS)

        def scalars(self, statement):
            entity = statement.column_descriptions[0]['entity']
            assert entity in {Review, Command, m.Audit}, 'unknown recovery must not renew machine sources'
            return [review] if entity is Review else []

        def execute(self, statement, params):
            assert str(statement) == 'SELECT pg_advisory_xact_lock_shared(:key)'
            nonce_key = int(digest({'native_video_review_nonce_v165': command.request_id,
                'workspace_id': WS, 'actor_id': ACTOR})[:16], 16) - (1 << 63)
            observation_key = int(digest({'native_video_review_observation_v165': OBS,
                'workspace_id': WS, 'instance_id': INSTANCE})[:16], 16) - (1 << 63)
            actor_key = int(digest({'native_video_review_actor_inventory_v166': ACTOR,
                'workspace_id': WS})[:16], 16) - (1 << 63)
            assert params['key'] in {nonce_key, observation_key, actor_key}

    with pytest.raises(HTTPException) as error:
        store.request_result(MissingCommandIO(), P, INSTANCE, command.request_id, command.wire_sha256)
    assert error.value.status_code == 409
