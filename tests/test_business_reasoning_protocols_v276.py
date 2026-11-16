"""Fixed server dispatch and retained-original routing, with explicit IO spies.

These unit tests do not prove SQL, IAM, paid transport or the real graph journey.
The actual protocol modules retain their independent live/cold checks and suites.
"""
from types import SimpleNamespace as NS

import pytest

from generic_agents import business_reasoning_protocols_v276 as subject
from generic_agents import business_reasoning_capture_v275 as journal
from generic_agents import business_reasoning_v197 as legacy
from generic_agents import business_decision_runtime_v276 as decision


@pytest.mark.parametrize('adapter,protocol', [(subject.LEGACY, legacy), (subject.DECISION, decision)])
def test_only_fixed_actual_modules_are_selected(adapter, protocol):
    assert subject.family(adapter) is subject.for_adapter(adapter) is protocol
    assert subject.require_protocol(protocol) is protocol and subject.applies(adapter)


@pytest.mark.parametrize('value', [None, '', 'workflow_service', 'generic_agents.business_decision_runtime_v276',
    'business_decision_v999', NS(__name__='generic_agents.business_reasoning_v197'), object()])
def test_unknown_names_and_module_like_objects_do_not_select_code(value):
    assert subject.for_adapter(value) is None and not subject.applies(value)
    with pytest.raises(ValueError, match='Unsupported server-owned'):
        subject.family(value)
    with pytest.raises(ValueError, match='Unsupported server-owned'):
        subject.require_protocol(value)


@pytest.mark.parametrize('protocol', [NS(__name__=legacy.__name__, ADAPTER=legacy.ADAPTER),
    NS(__name__=decision.__name__, ADAPTER=decision.ADAPTER), 'business_decision_v276'])
def test_kernel_rejects_protocol_names_before_any_journal_access(protocol):
    class ForbiddenDB:
        def __getattribute__(self, name):
            pytest.fail('unsupported protocol accessed journal: ' + name)
    with pytest.raises(ValueError, match='Unsupported server-owned'):
        journal.execute(ForbiddenDB(), object(), object(), {}, 'original', protocol=protocol)


@pytest.mark.parametrize('adapter,protocol', [(subject.LEGACY, legacy), (subject.DECISION, decision)])
def test_validation_execution_and_qualification_keep_the_selected_protocol(adapter, protocol, monkeypatch):
    values = []
    monkeypatch.setattr(protocol, 'validate_manifest', lambda *args: values.append(('validate', args)) or 'manifest')
    monkeypatch.setattr(protocol, 'execute', lambda *args: values.append(('execute', args)) or 'output')
    monkeypatch.setattr(protocol, 'qualify_evidence', lambda *args: values.append(('qualify', args)) or 'sources')
    db, p, task = object(), object(), object()
    manifest = {'execution': {'adapter': adapter}}
    cap = NS(data=manifest); inputs = {'original_input': True}
    assert subject.validate_manifest(db, p, manifest) == 'manifest'
    assert subject.execute(db, p, cap, inputs, 'same-key') == 'output'
    assert subject.qualify_evidence(adapter, db, p, task, inputs) == 'sources'
    assert values == [('validate', (db, p, manifest)), ('execute', (db, p, cap, inputs, 'same-key')),
        ('qualify', (db, p, task, inputs))]


@pytest.mark.parametrize('protocol', [legacy, decision])
def test_existing_state_enters_only_original_recovery_without_new_post(protocol, monkeypatch):
    original = NS(data={protocol.STATE_KEY: {'original': 'retained'}})
    inputs = {'unchanged': True}; output = {'the_original_result': protocol.ADAPTER}; seen = []
    monkeypatch.setattr(protocol, '_call', lambda *args, **kwargs: original)
    def recover(db, p, cap, actual, key, invocation):
        assert actual is inputs and invocation is original and key == 'original-key'
        seen.append('recover')
        return output
    monkeypatch.setattr(protocol, 'recover', recover)
    monkeypatch.setattr(protocol, 'prepare_request', lambda *_args: pytest.fail('retained state prepared another request'))
    assert journal.execute(object(), object(), object(), inputs, 'original-key', protocol=protocol) is output
    assert seen == ['recover']


def test_original_state_keys_never_cross_between_protocols():
    old = {'schema': legacy.STATE, 'capture': {'original': 'legacy'}}
    new = {'schema': decision.STATE, 'capture': {'original': 'decision'}}
    call = NS(data={legacy.STATE_KEY: old, decision.STATE_KEY: new})
    assert subject.retained_state(subject.LEGACY, call) is old
    assert subject.retained_state(subject.DECISION, call) is new
    assert subject.retained_state('workflow_service', call) is None
    assert subject.retained_state(subject.LEGACY, None) is None
    assert not subject.original_capture('builtin', None, object(), object(), {})


@pytest.mark.parametrize('adapter,protocol', [(subject.LEGACY, legacy), (subject.DECISION, decision)])
@pytest.mark.parametrize('damage', [None, 'schema', 'other_protocol', 'job', 'task', 'input', 'capability'])
def test_recovery_scheduling_hint_keeps_original_identity(adapter, protocol, damage):
    task = NS(id='original-task'); job = NS(id='original-job', data={'input_hash': 'input-hash'})
    node = {'execution': {'capability_id': 'original-capability', 'capability_hash': 'capability-hash'}}
    state = {'schema': protocol.STATE, 'capture': {'raw_base64': 'original'},
        'request': {'pin': {'job_id': job.id, 'task_id': task.id}}}
    call = NS(capability_id='original-capability', data={protocol.STATE_KEY: state,
        'capability_hash': 'capability-hash', 'input_hash': 'input-hash', 'job_id': job.id})
    if damage == 'schema': state['schema'] = 'unknown'
    elif damage == 'other_protocol':
        other = decision if protocol is legacy else legacy
        call.data[other.STATE_KEY] = call.data.pop(protocol.STATE_KEY)
    elif damage == 'job': state['request']['pin']['job_id'] = 'replacement-job'
    elif damage == 'task': state['request']['pin']['task_id'] = 'replacement-task'
    elif damage == 'input': call.data['input_hash'] = 'replacement-input'
    elif damage == 'capability': call.capability_id = 'replacement-capability'
    assert subject.original_capture(adapter, call, task, job, node) is (damage is None)


def test_legacy_evidence_ids_and_recovery_audit_contract_stay_exact():
    ids = ['original-evidence']; inputs = {'evidence_ids': ids}
    assert legacy.evidence_ids(inputs) is ids and inputs == {'evidence_ids': ids}
    assert subject.recovery_record(subject.LEGACY) == (
        'original_answer_recovery_v197', 'workflow.business_reasoning.original_answer_recovered_v197')
    assert subject.recovery_record(subject.DECISION) == (
        'original_answer_recovery_v276', 'workflow.business_decision.original_answer_recovered_v276')


def test_review_protocol_is_a_fixed_real_module_with_independent_state_and_recovery():
    from generic_agents import business_decision_runtime_v284 as review
    assert subject.family('business_decision_v284') is review
    assert subject.require_protocol(review) is review
    assert subject.retained_state('business_decision_v284',NS(data={review.STATE_KEY:{'new':True},decision.STATE_KEY:{'old':True}}))=={'new':True}
    assert subject.recovery_record('business_decision_v284')==('original_answer_recovery_v284','workflow.business_decision.original_answer_recovered_v284')
    with pytest.raises(ValueError):subject.require_protocol(NS(__name__=review.__name__,ADAPTER=review.ADAPTER))
