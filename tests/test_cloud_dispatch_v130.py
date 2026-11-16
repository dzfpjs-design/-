"""Pure phase/binding tests; real gateway and PG boundaries are separate tests."""
import copy
from contextvars import Context, copy_context
from types import SimpleNamespace as NS

import pytest

from generic_agents import cloud_dispatch_v130 as dispatch
from generic_agents.contracts import digest


IDENTITY = {'suggestion_id': 'original-suggestion', 'job_id': 'original-job',
    'task_id': 'original-task', 'request_hash': 'a' * 64}
P = NS(workspace_id='actual-workspace', user_id='actual-actor')
CAP = NS(id='actual-capability', workspace_id=P.workspace_id)
WIRE_HASH = 'b' * 64  # The prepared wire hash is not the suggestion hash.


class ModelBlocked(Exception):
    pass


class BudgetDenied(ModelBlocked, dispatch.ReservationDenied):
    pass


def checked(proof, identity=IDENTITY, workspace_id=P.workspace_id,
            actor_id=P.user_id, capability_id=CAP.id):
    return dispatch.validate_not_sent_proof(proof, identity, workspace_id, actor_id, capability_id)


def signed(value):
    result = copy.deepcopy(value)
    result['hash'] = digest({key: item for key, item in result.items() if key != 'hash'})
    return result


def proof_for(state):
    with dispatch.dispatch_scope(IDENTITY, P, CAP) as attempt:
        if state == 'confirmed':
            assert dispatch.reserve_tracked(lambda *args: 'real-call-id', P, CAP, 100, WIRE_HASH) == 'real-call-id'
        elif state in {'unknown', 'denied'}:
            error = BudgetDenied('positive quota rejection') if state == 'denied' else RuntimeError('reservation outcome unknown')

            def reserve(*args):
                raise error

            with pytest.raises(type(error)):
                dispatch.reserve_tracked(reserve, P, CAP, 100, WIRE_HASH)
        return attempt.not_sent_proof()


@pytest.mark.parametrize('state,stage', [('not_attempted', 'preflight'), ('unknown', 'reservation'),
    ('denied', 'reservation'), ('confirmed', 'authorization')])
def test_each_positive_not_started_phase_has_a_closed_valid_bound_proof(state, stage):
    proof = proof_for(state)
    assert set(proof) == {'schema', 'stage', 'transport', 'reservation_state', 'call_id',
        'reservation_request_hash', 'identity', 'attempt_id', 'hash'}
    assert proof['schema'] == 'cloud-dispatch-v130' and proof['transport'] == 'not_started'
    assert proof['stage'] == stage and proof['reservation_state'] == state
    assert proof['call_id'] == ('real-call-id' if state == 'confirmed' else None)
    assert proof['reservation_request_hash'] == (None if state == 'not_attempted' else WIRE_HASH)
    assert checked(proof) == proof
    assert proof['identity'] == {**IDENTITY, 'workspace_id': P.workspace_id,
        'actor_id': P.user_id, 'capability_id': CAP.id}


def test_reservation_that_committed_then_raised_remains_unknown_without_an_id():
    effects = []

    def reserve(*args):
        effects.append('simulated committed reservation')
        raise RuntimeError('commit acknowledgement lost')

    with dispatch.dispatch_scope(IDENTITY, P, CAP) as attempt:
        with pytest.raises(RuntimeError):
            dispatch.reserve_tracked(reserve, P, CAP, 100, WIRE_HASH)
        proof = attempt.not_sent_proof()
    assert effects and proof['reservation_state'] == 'unknown' and proof['call_id'] is None
    assert checked(proof)['transport'] == 'not_started'


@pytest.mark.parametrize('error', [ModelBlocked('budget words do not prove rejection'),
    ValueError('denied'), OSError('before reserve returned'), RuntimeError('unavailable')])
def test_only_typed_reservation_denial_can_claim_denied(error):
    def reserve(*args): raise error
    with dispatch.dispatch_scope(IDENTITY, P, CAP) as attempt:
        with pytest.raises(type(error)):
            dispatch.reserve_tracked(reserve, P, CAP, 100, WIRE_HASH)
        assert checked(attempt.not_sent_proof())['reservation_state'] == 'unknown'


@pytest.mark.parametrize('identity', [None, False, 1, [], ''])
def test_invalid_returned_call_id_is_not_a_confirmed_or_denied_reservation(identity):
    with dispatch.dispatch_scope(IDENTITY, P, CAP) as attempt:
        with pytest.raises(ValueError):
            dispatch.reserve_tracked(lambda *args: identity, P, CAP, 100, WIRE_HASH)
        assert checked(attempt.not_sent_proof())['reservation_state'] == 'unknown'


def test_reserved_callback_rejection_still_has_confirmed_reservation_and_no_http_entry():
    with dispatch.dispatch_scope(IDENTITY, P, CAP) as attempt:
        dispatch.reserve_tracked(lambda *args: 'original-call', P, CAP, 100, WIRE_HASH)
        try:
            raise RuntimeError('authorization callback refused before transport')
        except RuntimeError:
            proof = attempt.not_sent_proof()
    assert checked(proof)['stage'] == 'authorization'
    assert proof['call_id'] == 'original-call' and proof['reservation_state'] == 'confirmed'


def test_transport_entry_is_irreversible_even_when_post_or_response_persistence_fails():
    with dispatch.dispatch_scope(IDENTITY, P, CAP) as attempt:
        dispatch.reserve_tracked(lambda *args: 'original-call', P, CAP, 100, WIRE_HASH)
        dispatch.before_transport()
        assert attempt.transport_started and attempt.stage == 'transport'
        for error in (TimeoutError('HTTP result unknown'), OSError('response save failed')):
            try: raise error
            except type(error): assert attempt.not_sent_proof() is None
        dispatch.before_transport()
        assert attempt.not_sent_proof() is None
    assert attempt.not_sent_proof() is None


@pytest.mark.parametrize('outcome', ['success', 'denied', 'error'])
def test_late_reservation_completion_cannot_reset_an_observed_transport_entry(outcome):
    def reserve(*args):
        dispatch.before_transport()
        if outcome == 'denied': raise BudgetDenied('reentrant denial')
        if outcome == 'error': raise RuntimeError('reentrant failure')
        return 'call-after-marker'

    with dispatch.dispatch_scope(IDENTITY, P, CAP) as attempt:
        if outcome == 'success':
            dispatch.reserve_tracked(reserve, P, CAP, 100, WIRE_HASH)
        else:
            with pytest.raises(Exception):
                dispatch.reserve_tracked(reserve, P, CAP, 100, WIRE_HASH)
        assert attempt.transport_started and attempt.stage == 'transport'
        assert attempt.not_sent_proof() is None


def test_second_reservation_is_rejected_without_losing_the_first_call():
    calls = []
    def reserve(*args): calls.append(args); return 'first-call'
    with dispatch.dispatch_scope(IDENTITY, P, CAP) as attempt:
        dispatch.reserve_tracked(reserve, P, CAP, 100, WIRE_HASH)
        original = attempt.not_sent_proof()
        with pytest.raises(ValueError):
            dispatch.reserve_tracked(reserve, P, CAP, 100, WIRE_HASH)
        assert attempt.not_sent_proof() == original and len(calls) == 1


def test_untracked_helpers_are_unchanged_and_legacy_scope_masks_a_tracked_parent():
    sentinel, calls = object(), []
    def legacy(*args): calls.append(args); return sentinel
    assert dispatch.current_attempt() is None
    assert dispatch.reserve_tracked(legacy, None, None, 'old-bound', 'old-hash') is sentinel
    assert dispatch.before_transport() is None
    with dispatch.dispatch_scope(IDENTITY, P, CAP) as parent:
        before = parent.not_sent_proof()
        with dispatch.dispatch_scope(None, None, None) as legacy_attempt:
            assert legacy_attempt is None and dispatch.current_attempt() is None
            assert dispatch.reserve_tracked(legacy, None, None, 'old-bound', 'old-hash') is sentinel
            dispatch.before_transport()
        assert dispatch.current_attempt() is parent and parent.not_sent_proof() == before
    assert len(calls) == 2 and dispatch.current_attempt() is None


def test_nested_and_exceptional_scopes_restore_the_exact_parent_attempt():
    child_identity = {**IDENTITY, 'suggestion_id': 'child-suggestion'}
    with dispatch.dispatch_scope(IDENTITY, P, CAP) as parent:
        before = parent.not_sent_proof()
        with pytest.raises(RuntimeError):
            with dispatch.dispatch_scope(child_identity, P, CAP) as child:
                assert child is dispatch.current_attempt() and child is not parent
                assert child.attempt_id != parent.attempt_id
                dispatch.reserve_tracked(lambda *args: 'child-call', P, CAP, 100, WIRE_HASH)
                dispatch.before_transport()
                raise RuntimeError('child ended')
        assert dispatch.current_attempt() is parent and parent.not_sent_proof() == before
    assert dispatch.current_attempt() is None
    assert checked(parent.not_sent_proof()) == before


def test_fresh_context_and_explicit_copied_context_scope_do_not_pollute_outer_state():
    with dispatch.dispatch_scope(IDENTITY, P, CAP) as outer:
        before = outer.not_sent_proof()
        assert Context().run(dispatch.current_attempt) is None

        def child():
            with dispatch.dispatch_scope({**IDENTITY, 'job_id': 'other-job'}, P, CAP) as attempt:
                dispatch.before_transport()
                assert attempt.not_sent_proof() is None

        copy_context().run(child)
        assert dispatch.current_attempt() is outer and outer.not_sent_proof() == before


def test_proof_and_identity_are_detached_and_hashes_keep_distinct_request_meanings():
    identity = copy.deepcopy(IDENTITY)
    with dispatch.dispatch_scope(identity, P, CAP) as attempt:
        identity['task_id'] = 'external change'
        dispatch.reserve_tracked(lambda *args: 'call', P, CAP, 100, WIRE_HASH)
        proof = attempt.not_sent_proof(); original = copy.deepcopy(proof)
        result = checked(proof); result['identity']['task_id'] = 'mutate returned copy'
        proof['identity']['task_id'] = 'mutate emitted copy'
        snapshot = attempt.identity; snapshot['job_id'] = 'mutate property'
        assert attempt.not_sent_proof() == original
        assert original['identity']['request_hash'] != original['reservation_request_hash']


@pytest.mark.parametrize('field,value', [('suggestion_id', False), ('job_id', []), ('task_id', ''),
    ('request_hash', True), ('request_hash', 'A' * 64)])
def test_invalid_dispatch_identity_never_creates_a_scope(field, value):
    identity = {**IDENTITY, field: value}
    with pytest.raises(ValueError):
        with dispatch.dispatch_scope(identity, P, CAP): pytest.fail('invalid identity entered')
    assert dispatch.current_attempt() is None


def test_extra_identity_and_cross_workspace_are_rejected_before_reserve():
    with pytest.raises(ValueError):
        with dispatch.dispatch_scope({**IDENTITY, 'unapproved': True}, P, CAP): pass
    with pytest.raises(ValueError):
        with dispatch.dispatch_scope(IDENTITY, P, NS(id=CAP.id, workspace_id='other')): pass
    with dispatch.dispatch_scope(IDENTITY, P, CAP) as attempt:
        for principal, capability in ((NS(user_id='other', workspace_id=P.workspace_id), CAP),
                (P, NS(id='other', workspace_id=P.workspace_id))):
            with pytest.raises(ValueError):
                dispatch.reserve_tracked(lambda *args: pytest.fail('mismatched reserve called'),
                    principal, capability, 100, WIRE_HASH)
        assert attempt.reservation_state == 'not_attempted'


@pytest.mark.parametrize('binding', ['suggestion_id', 'job_id', 'task_id', 'request_hash',
    'workspace_id', 'actor_id', 'capability_id'])
def test_valid_proof_cannot_move_to_another_identity(binding):
    proof = proof_for('confirmed')
    identity, args = copy.deepcopy(IDENTITY), {'workspace_id': P.workspace_id,
        'actor_id': P.user_id, 'capability_id': CAP.id}
    if binding in identity: identity[binding] = 'c' * 64 if binding == 'request_hash' else 'other'
    else: args[binding] = 'other'
    with pytest.raises(ValueError): checked(proof, identity=identity, **args)


@pytest.mark.parametrize('field,value', [('schema', 'other'), ('stage', 'transport'),
    ('transport', 'started'), ('reservation_state', 'denied'), ('call_id', None),
    ('reservation_request_hash', None), ('attempt_id', True), ('attempt_id', 'not-a-uuid'),
    ('attempt_id', 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA'), ('identity', []), ('hash', True)])
def test_resealed_proof_still_requires_exact_types_and_state_relationships(field, value):
    proof = proof_for('confirmed'); proof[field] = value
    if field != 'hash': proof = signed(proof)
    with pytest.raises(ValueError): checked(proof)


@pytest.mark.parametrize('state,field,value', [('not_attempted', 'call_id', 'phantom'),
    ('not_attempted', 'reservation_request_hash', WIRE_HASH), ('unknown', 'call_id', 'phantom'),
    ('unknown', 'reservation_state', 'confirmed'), ('denied', 'stage', 'authorization')])
def test_missing_call_id_cannot_be_reinterpreted_as_confirmed_or_budget_denied(state, field, value):
    proof = proof_for(state); proof[field] = value
    with pytest.raises(ValueError): checked(signed(proof))


def test_unknown_and_denied_are_distinct_explicit_states_not_inferred_from_id_absence():
    unknown, denied = proof_for('unknown'), proof_for('denied')
    assert unknown['call_id'] is denied['call_id'] is None
    assert unknown['reservation_state'] == 'unknown' and denied['reservation_state'] == 'denied'
    assert checked(unknown) == unknown and checked(denied) == denied
    # Pure validation checks representation, not a cryptographic origin. Only a
    # gateway-created proof bound to the durable attempt is accepted by callers.


@pytest.mark.parametrize('change', ['extra', 'missing', 'hash', 'identity-extra', 'identity-bool'])
def test_closed_shape_original_hash_and_nested_identity_are_required(change):
    proof = proof_for('confirmed')
    if change == 'extra': proof['extra'] = True; proof = signed(proof)
    elif change == 'missing': del proof['stage']; proof = signed(proof)
    elif change == 'hash': proof['call_id'] = 'changed without original hash'
    elif change == 'identity-extra': proof['identity']['extra'] = True; proof = signed(proof)
    else: proof['identity']['actor_id'] = True; proof = signed(proof)
    with pytest.raises(ValueError): checked(proof)
