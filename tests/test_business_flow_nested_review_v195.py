"""V195 modes and actual shared qualification storage, synthetic I/O only.

The real review/qualification inventories, save commands and both save audits
run unchanged. Exact SQL/IAM boundaries reuse public fixtures; fresh source
snapshots here are explicitly trusted caller inputs, not report/PG proof.
No model, physical runtime, browser or production qualification is established.
"""
import copy
import hashlib

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from generic_agents import business_flow_nested_review_v195 as subject
from generic_agents import business_flow_qualification_store as q
from generic_agents import business_flow_qualification_api as api
from generic_agents import business_flow_review_store as reviews
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests import test_business_flow_review_store as base
from tests import test_business_flow_qualification_store as old
from tests.test_business_flow_qualification_store import no_io
from tests.test_report_business_flow_wire_v193 import fixture as plan_fixture


identity = base.identity
wire = base.wire


def scene(*, questions=False, component=False):
    c = base.scene()
    value, inputs = plan_fixture(nested=True)
    c.baseline = copy.deepcopy(value['plan'])
    if questions:
        c.baseline['business']['questions'] = [{'question': '补充核对当前业务条件是否满足？',
            'reason': '公开合成待核问题，不能当成已经人审。', 'citations': [inputs['allowed_span_refs'][0]]}]
    c.source.update(schema='business-flow-nested-review-source-v193', generation_profile='flow-v193',
        value_schema=subject.plans.PLAN)
    if component:
        c.source.update(schema='business-flow-nested-component-review-source-v194', component_path='/plan',
            component_value_hash=digest(c.baseline), component_validation_hash='f' * 64,
            original_protocol_valid=False)
    db = old.IO(c)
    base.save(c, db, base.body(value=copy.deepcopy(c.baseline), note='私人修订笔记不能默认共享'))
    return c, db, reviews.get(db, c.p, c.attempt.id)


def trusted_source(saved, mode='engineering_simulation'):
    value = old.trusted_source(saved)
    value.update(schema=subject.SOURCE, review_mode=mode)
    return value


def body(saved, mode='engineering_simulation', label='qfirst'):
    value = old.body(saved, label)
    value.update(schema=subject.COMMAND, review_mode=mode,
        reason='仅记录指定保存版本及明确模式；不证明真实业务语义或生产许可。')
    return value


def create(c, db, saved, request=None, source=None):
    request = body(saved) if request is None else request
    return q.create_nested(db, c.p, c.attempt.id, wire(request), method='POST',
        path=q.nested_qualify_path(c.attempt.id), saved=saved,
        source_snapshot=trusted_source(saved, request['review_mode']) if source is None else source)


@pytest.mark.parametrize('mode', ['author_self_review', 'engineering_simulation'])
def test_modes_bind_the_same_actual_saved_command_without_relabelling_review_or_execution(mode):
    c, db, saved = scene()
    before = canonical_json([db.drafts[0].data, c.attempt.data, c.baseline])
    command = body(saved, mode); result = create(c, db, saved, command)
    assert result['schema'] == subject.RESULT and result['version'] == 1
    assert result['review_mode'] == mode and result['value_schema'] == subject.plans.PLAN
    assert result['status'] == ('qualified' if mode == 'author_self_review' else 'engineering_reviewed')
    assert result['human_review'] == ('author_self_reviewed' if mode == 'author_self_review' else 'not_human_review')
    assert result['simulation_only'] is (mode == 'engineering_simulation')
    assert all(result[key] is False for key in reviews.FLAGS)
    assert result['value'] == saved['value'] and result['source_binding'] == saved['source_binding']
    assert result['draft_version'] == saved['version'] and result['save_request_id'] == saved['history'][-1]['request_id']
    row, original = db.qualifications[0], db.commands[0]
    assert row.data['schema'] == subject.RECORD and row.data['record']['schema'] == subject.SNAPSHOT
    assert row.save_command_id == original.id
    assert row.data['record']['save_command_hash'] == original.command_hash
    assert row.data['record']['save_body_sha256'] == original.body_sha256
    assert result['review_record_hash'] == digest(original.data['record_snapshot'])
    audit = next(event for event in db.events if event.action == q.AUDIT)
    assert audit.data['schema'] == subject.RECORD and audit.data['human_review'] == result['human_review']
    assert audit.data['simulation_only'] is result['simulation_only']
    assert '私人修订笔记' not in canonical_json(result)
    assert canonical_json([db.drafts[0].data, c.attempt.data, c.baseline]) == before
    assert db.drafts[0].status == 'incomplete' and c.attempt.status == 'failed'
    assert len(db.qualifications) == 1 and db.commits == 2


@pytest.mark.parametrize('component', [False, True])
def test_engineering_preserves_questions_and_component_original_failure_without_claiming_human_review(component):
    c, db, saved = scene(questions=True, component=component)
    result = create(c, db, saved)
    assert result['value']['business']['questions'] == saved['value']['business']['questions']
    assert result['human_review'] == 'not_human_review' and result['status'] != 'qualified'
    if component:
        assert result['source_binding']['original_protocol_valid'] is False
    assert c.attempt.status == 'failed'


def test_author_cannot_record_unresolved_questions_even_with_a_trusted_snapshot():
    c, db, saved = scene(questions=True)
    with pytest.raises(HTTPException): create(c, db, saved, body(saved, 'author_self_review'))
    assert not db.qualifications and db.commits == 1


@pytest.mark.parametrize('fault', ['schema', 'mode_missing', 'mode_unknown', 'mode_bool', 'version_bool',
    'version_float', 'zero_version', 'scope_false', 'inferences_false', 'integer_ack', 'extra',
    'duplicate', 'nonfinite', 'utf8', 'oversize'])
def test_nested_command_is_closed_typed_and_requires_deliberate_mode_and_both_acknowledgements(fault):
    _, _, saved = scene(); value = body(saved)
    if fault == 'schema': value['schema'] = q.COMMAND_SCHEMA
    elif fault == 'mode_missing': del value['review_mode']
    elif fault == 'mode_unknown': value['review_mode'] = 'admin_override'
    elif fault == 'mode_bool': value['review_mode'] = True
    elif fault == 'version_bool': value['expected_version'] = True
    elif fault == 'version_float': value['expected_version'] = 1.0
    elif fault == 'zero_version': value['expected_version'] = 0
    elif fault == 'scope_false': value['ack_scope'] = False
    elif fault == 'inferences_false': value['ack_inferences'] = False
    elif fault == 'integer_ack': value['ack_scope'] = 1
    elif fault == 'extra': value['source_snapshot'] = {}
    raw = wire(value)
    if fault == 'duplicate': raw = raw[:-1] + b',"review_mode":"engineering_simulation"}'
    elif fault == 'nonfinite': raw = raw[:-1] + b',"extra":NaN}'
    elif fault == 'utf8': raw = b'\xff'
    elif fault == 'oversize': raw += b' ' * (subject.MAX_WIRE_BYTES + 1)
    with pytest.raises(ValueError): q.parse_nested_wire(raw)


def test_original_nested_result_survives_new_draft_work_and_replay_precedes_fresh_source():
    c, db, saved = scene(); command = body(saved); raw = wire(command)
    result = create(c, db, saved, command)
    changed = copy.deepcopy(saved['value']); changed['business']['flows'][0]['name'] = '后续独立修订'
    base.save(c, db, base.body('second', 1, changed, '后来仍未完成的私人修改'))
    assert q.get(db, c.p, result['id']) == result
    assert q.list_records(db, c.p, attempt_id=c.attempt.id) == [result]
    assert q.create_nested(db, c.p, c.attempt.id, raw, method='POST', path=q.nested_qualify_path(c.attempt.id),
        saved=None, source_snapshot=None) == result
    original = q.original_nested_request(db, c.p, command['request_id'], hashlib.sha256(raw).hexdigest())
    assert original == {'schema': subject.REQUEST, 'request_id': command['request_id'], 'found': True,
        'body_sha256': hashlib.sha256(raw).hexdigest(), 'result': result}
    cold = old.IO(c)
    for field in ('drafts', 'commands', 'events', 'qualifications'):
        setattr(cold, field, copy.deepcopy(getattr(db, field)))
    assert q.original_nested_request(cold, c.p, command['request_id'], hashlib.sha256(raw).hexdigest()) == original
    assert cold.commits == 0 and len(db.qualifications) == 1


@pytest.mark.parametrize('fault', ['raw', 'mode', 'target', 'path', 'method', 'digest'])
def test_same_nonce_cannot_change_mode_wire_scope_or_original_result(fault):
    c, db, saved = scene(); command = body(saved); raw = wire(command); result = create(c, db, saved, command)
    target, path, method = c.attempt.id, q.nested_qualify_path(c.attempt.id), 'POST'
    if fault == 'raw': raw = raw.replace(b'"reason": ', b'"reason":  ')
    elif fault == 'mode': command['review_mode'] = 'author_self_review'; raw = wire(command)
    elif fault == 'target': target = identity('other-attempt'); path = q.nested_qualify_path(target)
    elif fault == 'path': path = q.qualify_path(target)
    elif fault == 'method': method = 'PUT'
    if fault == 'digest':
        with pytest.raises(HTTPException): q.original_nested_request(db, c.p, command['request_id'], '0' * 64)
    else:
        with pytest.raises(HTTPException): q.replay_nested(db, c.p, target, raw, method=method, path=path)
    assert q.get(db, c.p, result['id']) == result and len(db.qualifications) == 1


def test_nonce_and_storage_inventory_are_shared_while_old_and_nested_protocols_stay_distinct():
    c, db, saved = scene(); command = body(saved); result = create(c, db, saved, command)
    legacy = old.body(saved)
    with pytest.raises(HTTPException): q.replay(db, c.p, c.attempt.id, wire(legacy), method='POST', path=q.qualify_path(c.attempt.id))
    with pytest.raises(HTTPException): q.original_request(db, c.p, command['request_id'], hashlib.sha256(wire(command)).hexdigest())
    with pytest.raises(ValueError): q.parse_wire(wire(command))
    with pytest.raises(ValueError): q.parse_nested_wire(wire(legacy))
    assert q.get(db, c.p, result['id']) == result
    lc, ldb, lsaved = old.scene(); lresult = old.create(lc, ldb, lsaved)
    with pytest.raises(HTTPException): q.original_nested_request(ldb, lc.p, lresult['request_id'],
        hashlib.sha256(wire(old.body(lsaved))).hexdigest())
    assert q.get(ldb, lc.p, lresult['id']) == lresult and lresult['schema'] == q.RESULT


@pytest.mark.parametrize('fault', ['version', 'save_request', 'value_hash'])
def test_only_proven_current_version_conflicts_are_explicitly_not_applied(fault):
    c, db, saved = scene(); command = body(saved)
    if fault == 'version': command['expected_version'] = 2
    elif fault == 'save_request': command['save_request_id'] = identity('unknown')
    else: command['value_hash'] = '0' * 64
    with pytest.raises(HTTPException) as caught: create(c, db, saved, command)
    assert caught.value.detail == {'code': 'business_flow_nested_review_version_conflict',
        'not_applied': True, 'current_version': 1}
    assert not db.qualifications and db.commits == 1


@pytest.mark.parametrize('fault', ['mode', 'source_hash', 'source_binding', 'source_schema', 'extra'])
def test_source_snapshot_is_exact_and_cannot_switch_review_mode_or_original_materials(fault):
    c, db, saved = scene(); snapshot = trusted_source(saved)
    if fault == 'mode': snapshot['review_mode'] = 'author_self_review'
    elif fault == 'source_hash': snapshot['current_source_hash'] = '0' * 64
    elif fault == 'source_binding': snapshot['source_binding_hash'] = '0' * 64
    elif fault == 'source_schema': snapshot['schema'] = 'business-flow-author-review-source-v1'
    else: snapshot['production_allowed'] = True
    with pytest.raises(HTTPException): create(c, db, saved, source=snapshot)
    assert not db.qualifications and db.commits == 1


@pytest.mark.parametrize('fault', ['row', 'qualification_audit', 'command', 'command_audit', 'state_audit',
    'unknown_schema', 'result_human', 'result_status', 'record_mode', 'audit_mode'])
def test_every_shared_inventory_anchor_and_mode_is_verified_before_returning_history(fault):
    c, db, saved = scene(); command = body(saved); result = create(c, db, saved, command)
    if fault == 'row': db.qualifications.clear()
    elif fault == 'qualification_audit': db.events[:] = [event for event in db.events if event.action != q.AUDIT]
    elif fault == 'command': db.commands.clear()
    elif fault == 'command_audit': db.events[:] = [event for event in db.events if event.action != reviews.COMMAND_AUDIT]
    elif fault == 'state_audit': db.events[:] = [event for event in db.events if event.action != reviews.STATE_AUDIT]
    elif fault == 'unknown_schema': db.qualifications[0].data['schema'] = 'unknown-family'
    elif fault == 'result_human': db.qualifications[0].data['result']['human_review'] = 'author_self_reviewed'
    elif fault == 'result_status': db.qualifications[0].data['result']['status'] = 'qualified'
    elif fault == 'record_mode': db.qualifications[0].data['record']['review_mode'] = 'author_self_review'
    else: next(event for event in db.events if event.action == q.AUDIT).data['simulation_only'] = False
    with pytest.raises(HTTPException) as caught:
        q.original_nested_request(db, c.p, result['request_id'], hashlib.sha256(wire(command)).hexdigest())
    assert caught.value.status_code == 409


@pytest.mark.parametrize('fault', ['inactive', 'changed_role', 'database'])
def test_current_real_iam_query_and_database_errors_are_not_bypassed_by_the_new_mode(fault):
    c, db, saved = scene()
    if fault == 'inactive': db.active = False
    elif fault == 'changed_role': db.membership_role = 'viewer'
    else: db.failure = OperationalError('synthetic SQL inventory', {}, Exception('synthetic DB failure'))
    with pytest.raises(OperationalError if fault == 'database' else HTTPException): create(c, db, saved)
    assert not db.qualifications and db.commits == 1


def test_normal_api_helpers_use_actual_store_and_keep_engineering_questions_explicit(monkeypatch):
    c, db, saved = scene(questions=True); calls = []
    def fresh(actual_db, principal, attempt_id, actual_saved, *, review_mode):
        assert actual_db is db and principal is c.p and attempt_id == c.attempt.id
        assert actual_saved == saved
        calls.append(review_mode)
        return trusted_source(actual_saved, review_mode)
    # Only the fresh-source authority boundary is synthetic; all command,
    # nonce, IAM-query, review inventory and qualification code remains real.
    monkeypatch.setattr(api.source, 'current_nested_review_source', fresh)
    preview = api.nested_preview(db, c.p, c.attempt.id, 'engineering_simulation')
    assert preview['schema'] == subject.PREVIEW and preview['simulation_only'] is True
    assert preview['open_question_count'] == 1 and preview['value_schema'] == subject.plans.PLAN
    assert preview['action_count'] == sum(flow['action_count'] for flow in preview['flows'])
    assert preview['flows'][0]['flow_index'] == 0 and preview['flows'][0]['action_count'] > 1
    request = body(saved); result = api.qualify_nested(db, c.p, c.attempt.id, wire(request),
        'POST', q.nested_qualify_path(c.attempt.id))
    assert result['status'] == 'engineering_reviewed' and calls == ['engineering_simulation'] * 2
    assert api.list_reviews(db, c.p, c.attempt.id, nested_family=True) == [result]
    assert api.list_reviews(db, c.p, c.attempt.id, nested_family=False) == []
    def forbidden(*args, **kwargs): pytest.fail('original nonce recovery must precede current source reads')
    monkeypatch.setattr(api.source, 'current_nested_review_source', forbidden)
    assert api.qualify_nested(db, c.p, c.attempt.id, wire(request), 'POST', q.nested_qualify_path(c.attempt.id)) == result


@pytest.mark.parametrize('status', [409, 422, 403])
def test_api_only_archives_proven_unapplied_fresh_precheck_rejection(status, monkeypatch):
    c, db, saved = scene()
    def reject(*args, **kwargs): raise HTTPException(status, 'synthetic fresh-source boundary')
    monkeypatch.setattr(api.source, 'current_nested_review_source', reject)
    request = body(saved)
    with pytest.raises(HTTPException) as caught:
        api.qualify_nested(db, c.p, c.attempt.id, wire(request), 'POST', q.nested_qualify_path(c.attempt.id))
    if status in {409, 422}:
        assert caught.value.detail['code'] == 'nested_review_source_not_applied'
        assert caught.value.detail['not_applied'] is True
    else: assert type(caught.value.detail) is str
    assert not db.qualifications and db.commits == 1


def test_api_cannot_turn_create_or_database_errors_into_known_unapplied_source_rejection(monkeypatch):
    c, db, saved = scene()
    monkeypatch.setattr(api.source, 'current_nested_review_source', lambda *args, **kwargs: trusted_source(saved))
    def broken(*args, **kwargs): raise OperationalError('synthetic final insert', {}, Exception('synthetic failure'))
    monkeypatch.setattr(q, 'create_nested', broken)
    with pytest.raises(OperationalError): api.qualify_nested(db, c.p, c.attempt.id, wire(body(saved)),
        'POST', q.nested_qualify_path(c.attempt.id))
