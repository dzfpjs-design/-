"""Per-call evidence that this gateway attempt has not entered its transport.

This is opt-in control-flow evidence, not permission to send, retry, release a
budget reservation, or infer that no CloudCall exists. Gateway integrations
must call before_transport immediately before every actual post/stream entry.
Legacy calls explicitly mask an enclosing tracked scope and remain unchanged.
"""
import copy
from contextlib import contextmanager
from contextvars import ContextVar
import re
from uuid import UUID, uuid4

from .contracts import digest


SCHEMA = 'cloud-dispatch-v130'
ERROR = '云调用未发送证明的范围、阶段或摘要无法核对'
_HASH = re.compile(r'[0-9a-f]{64}')
_INPUT_KEYS = {'suggestion_id', 'job_id', 'task_id', 'request_hash'}
_IDENTITY_KEYS = _INPUT_KEYS | {'workspace_id', 'actor_id', 'capability_id'}
_PROOF_KEYS = {'schema', 'stage', 'transport', 'reservation_state', 'call_id',
    'reservation_request_hash', 'identity', 'attempt_id', 'hash'}
_CURRENT = ContextVar('cloud_dispatch_v130_attempt', default=None)


class ReservationDenied(Exception):
    """Mixin for a positively known quota rejection before reservation commit.

    Only the real budget implementation may classify that branch as denied.
    An ordinary ModelBlocked/HTTP/DB error is deliberately not this evidence.
    """


def _need(value):
    if not value:
        raise ValueError(ERROR)


def _id(value):
    _need(type(value) is str and bool(value))
    value.encode('utf-8')


def _hash(value):
    _need(type(value) is str and _HASH.fullmatch(value) is not None)


def _identity(identity, workspace_id, actor_id, capability_id):
    _need(type(identity) is dict and set(identity) == _INPUT_KEYS)
    for key in ('suggestion_id', 'job_id', 'task_id'):
        _id(identity[key])
    _hash(identity['request_hash'])
    for value in (workspace_id, actor_id, capability_id):
        _id(value)
    return {**copy.deepcopy(identity), 'workspace_id': workspace_id,
        'actor_id': actor_id, 'capability_id': capability_id}


class CloudDispatchAttempt:
    """One attempt; its mutable phase never authorizes the request itself.

    The state tuple is replaced atomically so readers cannot obtain a half
    confirmed reservation. A proof may be copied after the scope has reset;
    it remains bound to this attempt, never to a subsequent scoped call.
    """
    def __init__(self, identity, *, workspace_id, actor_id, capability_id):
        self._identity = _identity(identity, workspace_id, actor_id, capability_id)
        self._attempt_id = str(uuid4())
        self._state = ('preflight', 'not_attempted', None, None, False)

    @property
    def identity(self):
        return copy.deepcopy(self._identity)

    @property
    def attempt_id(self):
        return self._attempt_id

    @property
    def stage(self):
        return self._state[0]

    @property
    def reservation_state(self):
        return self._state[1]

    @property
    def call_id(self):
        return self._state[2]

    @property
    def transport_started(self):
        return self._state[4]

    def not_sent_proof(self):
        stage, reservation, call_id, request_hash, started = self._state
        if started:
            return None
        value = {'schema': SCHEMA, 'stage': stage, 'transport': 'not_started',
            'reservation_state': reservation, 'call_id': call_id,
            'reservation_request_hash': request_hash, 'identity': self.identity,
            'attempt_id': self.attempt_id}
        return {**value, 'hash': digest(value)}


def current_attempt():
    return _CURRENT.get()


@contextmanager
def dispatch_scope(dispatch_identity, principal, capability):
    """Bind actual principal/capability IDs; None is an isolated legacy scope.

    The context must enclose every selected cloud_json branch. Creating a scope
    alone cannot observe HTTP: each branch must mark its own real transport.
    """
    attempt = None
    if dispatch_identity is not None:
        _need(capability.workspace_id == principal.workspace_id)
        attempt = CloudDispatchAttempt(dispatch_identity,
            workspace_id=principal.workspace_id, actor_id=principal.user_id,
            capability_id=capability.id)
    token = _CURRENT.set(attempt)
    try:
        yield attempt
    finally:
        _CURRENT.reset(token)


def reserve_tracked(reserve, principal, capability, bound, request_hash):
    """Do not infer reservation rejection from an exception or a missing ID."""
    attempt = current_attempt()
    if attempt is None:
        return reserve(principal, capability, bound, request_hash)
    identity = attempt.identity
    _need(capability.workspace_id == principal.workspace_id == identity['workspace_id']
        and principal.user_id == identity['actor_id'] and capability.id == identity['capability_id'])
    _need(attempt._state == ('preflight', 'not_attempted', None, None, False))
    _hash(request_hash)
    attempt._state = ('reservation', 'unknown', None, request_hash, False)
    try:
        call_id = reserve(principal, capability, bound, request_hash)
    except ReservationDenied:
        started = attempt.transport_started
        attempt._state = ('transport' if started else 'reservation',
            'denied', None, request_hash, started)
        raise
    # A committed reservation can exist even if reserve raises or returns an
    # invalid ID. Keep that outcome unknown instead of claiming zero budget use.
    _id(call_id)
    started = attempt.transport_started
    attempt._state = ('transport' if started else 'authorization',
        'confirmed', call_id, request_hash, started)
    return call_id


def before_transport():
    """Conservatively mark entry, even if the library fails before any bytes."""
    attempt = current_attempt()
    if attempt is not None:
        _, reservation, call_id, request_hash, _ = attempt._state
        attempt._state = ('transport', reservation, call_id, request_hash, True)


def validate_not_sent_proof(proof, identity, workspace_id, actor_id, capability_id):
    """Validate a saved proof's closed shape and binding, never current authority.

    The caller must additionally bind its actual attempt/job and durable record.
    A self-consistent JSON hash does not independently prove that code ran.
    """
    try:
        expected = _identity(identity, workspace_id, actor_id, capability_id)
        _need(type(proof) is dict and set(proof) == _PROOF_KEYS
            and proof['schema'] == SCHEMA and proof['transport'] == 'not_started')
        _need(type(proof['identity']) is dict and set(proof['identity']) == _IDENTITY_KEYS
            and all(type(value) is str for value in proof['identity'].values())
            and proof['identity'] == expected)
        _id(proof['attempt_id'])
        _need(str(UUID(proof['attempt_id'])) == proof['attempt_id'])
        _hash(proof['hash'])
        _need(proof['hash'] == digest({key: value for key, value in proof.items() if key != 'hash'}))
        stage, reservation = proof['stage'], proof['reservation_state']
        if stage == 'preflight':
            _need(reservation == 'not_attempted' and proof['call_id'] is None
                and proof['reservation_request_hash'] is None)
        elif stage == 'reservation':
            _need(reservation in {'unknown', 'denied'} and proof['call_id'] is None)
            _hash(proof['reservation_request_hash'])
        elif stage == 'authorization':
            _need(reservation == 'confirmed')
            _id(proof['call_id']); _hash(proof['reservation_request_hash'])
        else:
            _need(False)
        return copy.deepcopy(proof)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError(ERROR) from error
