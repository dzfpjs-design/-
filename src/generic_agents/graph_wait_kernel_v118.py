"""Effect-free admission-cut waits over the unchanged graph v1 state wire.

The caller authenticates binding/source/producer pins, entity scope, and a complete
committed admission prefix before calling this module. This sealed sidecar grants
no database or execution permission. Times are supplied DB decision times.
"""
import copy
import re
from uuid import UUID

from . import full_semantic_graph_v1 as kernel


SCHEMA = 'graph-wait-authority-v118'
MAX_SEQUENCE = (1 << 63) - 1
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_AUTHORITY = {'schema', 'graph_hash', 'input_hash', 'binding_hash', 'scope_hash', 'entity_ids',
              'cancel_epoch', 'initial_cut', 'cursor', 'windows', 'receipts', 'authority_hash'}
_WINDOW = {'activation_id', 'node_id', 'epoch', 'context_hash', 'subscription_id', 'open_sequence',
           'opened_at', 'deadline', 'correlation_hash', 'status', 'closed_sequence', 'closed_at', 'event_id'}
_RECEIPT = {'event_id', 'event_type', 'correlation', 'output', 'sequence', 'received_at', 'producer_id', 'contract_hash'}
_METADATA = {'event_id', 'event_type', 'sequence', 'received_at', 'producer_id', 'contract_hash',
             'correlation_hash', 'output_hash', 'body_hash'}
_COMPLETE = {'activation_id', 'output', 'request_id', 'actor_id', 'expected_version', 'cancel_epoch'}


def _need(value, code):
    if not value: raise ValueError('graph_wait_' + code)


def _keys(value, keys, code):
    _need(type(value) is dict and set(value) == keys, code)


def _hash(value):
    _need(type(value) is str and _HASH.fullmatch(value) is not None, 'hash')


def _uuid(value):
    try: valid = type(value) is str and str(UUID(value)) == value
    except (TypeError, ValueError, AttributeError): valid = False
    _need(valid, 'entity_identity')


def _entities(values):
    _need(type(values) is list and 0 < len(values) <= kernel.MAX_ACTIVATIONS, 'entity_scope')
    for value in values: _uuid(value)
    _need(len(values) == len(set(values)), 'entity_scope')
    return sorted(values)


def _cut(value):
    _keys(value, {'sequence', 'at'}, 'cut_shape')
    kernel._integer(value['sequence'], 0, MAX_SEQUENCE); kernel._time(value['at'])
    return copy.deepcopy(value)


def _seal(value):
    result = copy.deepcopy(value)
    result.pop('authority_hash', None)
    result['authority_hash'] = kernel._hash(result)
    return result


def _metadata(receipt):
    return {key: receipt[key] for key in ('event_id', 'event_type', 'sequence', 'received_at', 'producer_id', 'contract_hash')} | {
        'correlation_hash': kernel._hash(receipt['correlation']), 'output_hash': kernel._hash(receipt['output']),
        'body_hash': kernel._hash(receipt)}


def _receipt_from_event(item, meta):
    return {key: item[key] for key in ('event_id', 'event_type', 'correlation', 'output')} | {
        key: meta[key] for key in ('sequence', 'received_at', 'producer_id', 'contract_hash')}


def _eligible(window, meta):
    return (window['open_sequence'] < meta['sequence']
        and window['opened_at'] <= meta['received_at'] < window['deadline']
        and window['correlation_hash'] == meta['correlation_hash'])


def _same(value, other):
    return kernel._hash(value) == kernel._hash(other)


def _inspect(graph, state, authority, *, binding_hash, entity_ids, scope_hash, cancelling=False):
    kernel._safe(authority); _keys(authority, _AUTHORITY, 'authority_shape')
    _hash(binding_hash); _hash(scope_hash); entities = _entities(entity_ids)
    _need(authority['schema'] == SCHEMA and authority['authority_hash'] == kernel._hash({
        key: value for key, value in authority.items() if key != 'authority_hash'}), 'authority_hash')
    _need(authority['binding_hash'] == binding_hash and authority['scope_hash'] == scope_hash
        and authority['entity_ids'] == entities and authority['graph_hash'] == state['graph_hash']
        and authority['input_hash'] == state['input_hash'], 'authority_pins')
    initial, cursor = _cut(authority['initial_cut']), _cut(authority['cursor'])
    _need(initial['sequence'] <= cursor['sequence'] and initial['at'] <= cursor['at'] <= state['clock'], 'authority_clock')
    first = state['activations'].get('a1')
    _need(first is not None and first['created_at'] == initial['at'], 'initial_cut')
    kernel._integer(authority['cancel_epoch'], 0, 1000000000)
    pending_cancel = cancelling and state['status'] in {'cancelled', 'stopped', 'failed'} and state['cancel_epoch'] == authority['cancel_epoch'] + 1
    _need(state['cancel_epoch'] == authority['cancel_epoch'] or pending_cancel, 'epoch')
    _need(type(authority['windows']) is dict and len(authority['windows']) <= kernel.MAX_ACTIVATIONS
        and type(authority['receipts']) is dict and len(authority['receipts']) <= kernel.MAX_EVENTS, 'authority_collections')
    nodes = {node['id']: node for node in graph['nodes']}
    subscriptions = {item['id']: item for item in graph['subscriptions']}
    events = {item['event_id']: item for item in state['events']}
    _need(set(events) == set(authority['receipts']), 'receipt_ledger')
    sequences = set()
    for identity, meta in authority['receipts'].items():
        _keys(meta, _METADATA, 'receipt_metadata')
        kernel._id(identity); _uuid(meta['producer_id']); _hash(meta['contract_hash'])
        kernel._integer(meta['sequence'], 1, MAX_SEQUENCE); kernel._time(meta['received_at'])
        _need(meta['event_id'] == identity and initial['sequence'] < meta['sequence'] <= cursor['sequence']
            and initial['at'] <= meta['received_at'] <= cursor['at'] and meta['sequence'] not in sequences, 'receipt_cursor')
        sequences.add(meta['sequence'])
        item = events[identity]
        _need(item['correlation'] in entities and item['received_at'] == meta['received_at']
            and _same(meta, _metadata(_receipt_from_event(item, meta))), 'receipt_changed')
    ordered = sorted(authority['receipts'].values(), key=lambda item: item['sequence'])
    _need(all(left['received_at'] <= right['received_at'] for left, right in zip(ordered, ordered[1:])), 'receipt_clock_backwards')
    for identity, window in authority['windows'].items():
        _keys(window, _WINDOW, 'window_shape')
        activation = state['activations'].get(identity)
        _need(activation is not None and window['activation_id'] == identity, 'window_activation')
        node = nodes[activation['node_id']]
        kernel._integer(window['epoch'], 0, state['cancel_epoch'])
        _need(node['kind'] == 'wait' and window['node_id'] == node['id'] and window['subscription_id'] == node['subscription_id']
            and window['epoch'] == activation['epoch'] and window['context_hash'] == kernel._hash(activation['context']), 'window_context')
        kernel._integer(window['open_sequence'], 0, MAX_SEQUENCE)
        kernel._time(window['opened_at']); kernel._time(window['deadline']); _hash(window['correlation_hash'])
        _need(initial['sequence'] <= window['open_sequence'] <= cursor['sequence']
            and activation['created_at'] <= window['opened_at'] <= cursor['at']
            and window['deadline'] == window['opened_at'] + node['timeout_seconds']
            and activation['wait_started_at'] == window['opened_at'] and activation['deadline'] == window['deadline']
            and activation['correlation_hash'] == window['correlation_hash'], 'window_clock')
        resolved = kernel._resolved(graph, state, kernel._metadata(graph), activation['context'])
        correlation = resolved.get(subscriptions[node['subscription_id']]['correlation_ref'])
        _need(type(correlation) is str and correlation in entities and kernel._hash(correlation) == window['correlation_hash'], 'window_correlation')
        if window['status'] == 'open':
            _need((activation['status'] in {'waiting_event', 'waiting_guard'} and window['epoch'] == state['cancel_epoch'])
                or (pending_cancel and activation['status'] == 'cancelled'), 'window_open')
            _need(window['closed_sequence'] is None and window['closed_at'] is None and window['event_id'] is None, 'window_open')
        else:
            _need(window['status'] in {'consumed', 'timed_out', 'cancelled'}, 'window_status')
            kernel._integer(window['closed_sequence'], window['open_sequence'], cursor['sequence'])
            kernel._time(window['closed_at'])
            _need(window['opened_at'] <= window['closed_at'] <= cursor['at'], 'window_closed_clock')
            if window['status'] == 'consumed':
                item = events.get(window['event_id']); meta = authority['receipts'].get(window['event_id'])
                _need(item is not None and meta is not None and activation['status'] == 'completed'
                    and item['consumed_by'] == identity and _same(activation['output'], item['output'])
                    and item['event_type'] == subscriptions[node['subscription_id']]['event_type']
                    and meta['sequence'] <= window['closed_sequence'] and _eligible(window, meta)
                    and activation['completed_at'] == window['closed_at'], 'window_consumption')
            elif window['status'] == 'timed_out':
                _need(activation['status'] == 'timed_out' and window['event_id'] is None
                    and activation['completed_at'] == window['closed_at'] >= window['deadline'], 'window_timeout')
            else:
                _need(activation['status'] == 'cancelled' and window['event_id'] is None
                    and window['epoch'] < state['cancel_epoch'], 'window_cancel')
    for identity, activation in state['activations'].items():
        if nodes[activation['node_id']]['kind'] != 'wait': continue
        if activation['deadline'] is not None or activation['status'] in {'completed', 'timed_out'}:
            _need(identity in authority['windows'], 'missing_window')
    for item in events.values():
        if item['consumed_by'] is not None:
            window = authority['windows'].get(item['consumed_by'])
            _need(window is not None and window['status'] == 'consumed' and window['event_id'] == item['event_id'], 'consumption_ledger')
        receipt = authority['receipts'][item['event_id']]
        _need(any(subscriptions[window['subscription_id']]['event_type'] == item['event_type']
            and _eligible(window, receipt) and receipt['sequence'] <= (
                cursor['sequence'] if window['status'] == 'open' else window['closed_sequence'])
            for window in authority['windows'].values()), 'receipt_without_window')
    for window in authority['windows'].values():
        if window['status'] not in {'consumed', 'timed_out'}: continue
        selected_sequence = (authority['receipts'][window['event_id']]['sequence']
            if window['status'] == 'consumed' else MAX_SEQUENCE + 1)
        for item in events.values():
            receipt = authority['receipts'][item['event_id']]
            if (item['event_type'] != subscriptions[window['subscription_id']]['event_type']
                    or not _eligible(window, receipt) or receipt['sequence'] > window['closed_sequence']
                    or receipt['sequence'] >= selected_sequence): continue
            owner = authority['windows'].get(item['consumed_by'])
            _need(owner is not None and owner['closed_sequence'] <= window['closed_sequence']
                and owner['closed_at'] <= window['closed_at'], 'skipped_timely_receipt')
    return copy.deepcopy(authority)


@kernel._public
def check(graph, state, authority, *, binding_hash, entity_ids, scope_hash):
    graph = kernel.validate_graph(graph); kernel.check_run(graph, state)
    return _inspect(graph, state, authority, binding_hash=binding_hash, entity_ids=entity_ids, scope_hash=scope_hash)


def _close_cancelled(state, authority, cut):
    for identity, window in authority['windows'].items():
        if window['status'] == 'open' and state['activations'][identity]['status'] == 'cancelled':
            window.update(status='cancelled', closed_sequence=cut['sequence'], closed_at=cut['at'])
    authority['cancel_epoch'] = state['cancel_epoch']


def _hook(authority, cut, *, _observer=None):
    def apply(graph, state, meta, activation, node, now):
        subscription = meta['subscriptions'][node['subscription_id']]
        correlation = kernel._resolved(graph, state, meta, activation['context']).get(subscription['correlation_ref'])
        if correlation is None:
            activation['status'] = 'waiting_guard'; return False
        _need(type(correlation) is str and correlation in authority['entity_ids'], 'unauthorized_correlation')
        window = authority['windows'].get(activation['id'])
        if window is None:
            window = {'activation_id': activation['id'], 'node_id': node['id'], 'epoch': activation['epoch'],
                'context_hash': kernel._hash(activation['context']), 'subscription_id': node['subscription_id'],
                'open_sequence': cut['sequence'], 'opened_at': now, 'deadline': now + node['timeout_seconds'],
                'correlation_hash': kernel._hash(correlation), 'status': 'open',
                'closed_sequence': None, 'closed_at': None, 'event_id': None}
            authority['windows'][activation['id']] = window
            activation.update(wait_started_at=now, deadline=window['deadline'], correlation_hash=window['correlation_hash'])
        _need(window['status'] == 'open' and window['epoch'] == state['cancel_epoch']
            and window['correlation_hash'] == kernel._hash(correlation), 'live_window')
        activation['status'] = 'waiting_event'
        matching = [item for item in state['events'] if item['consumed_by'] is None
            and item['event_type'] == subscription['event_type'] and _eligible(window, authority['receipts'][item['event_id']])
            and authority['receipts'][item['event_id']]['sequence'] <= cut['sequence']]
        if matching:
            item = min(matching, key=lambda value: authority['receipts'][value['event_id']]['sequence'])
            kernel._validate_output(subscription['output_schema'], item['output'])
            kernel._check_produced_scalars(graph, meta, node['id'], item['output'])
            item['consumed_by'] = activation['id']; kernel._finish(activation, item['output'], now)
            window.update(status='consumed', closed_sequence=cut['sequence'], closed_at=now, event_id=item['event_id'])
            kernel._follow(graph, state, meta, activation, 'success', now, _observer=_observer)
            return True
        if now >= window['deadline']:
            kernel._finish(activation, None, now, 'timed_out'); activation['error'] = 'wait_timeout'
            window.update(status='timed_out', closed_sequence=cut['sequence'], closed_at=now)
            kernel._follow(graph, state, meta, activation, 'timeout', now, _observer=_observer)
            return True
        return False
    return apply


def _buffer(graph, state, authority, receipts, cut):
    _need(type(receipts) is list and len(receipts) <= kernel.MAX_EVENTS, 'batch_limit')
    kernel._safe(receipts)
    subscriptions = {item['event_type']: item for item in graph['subscriptions']}
    observed = {meta['sequence']: identity for identity, meta in authority['receipts'].items()}
    by_identity = copy.deepcopy(authority['receipts'])
    batch_times = {}
    for receipt in receipts:
        _keys(receipt, _RECEIPT, 'receipt_shape')
        kernel._id(receipt['event_id']); _uuid(receipt['producer_id']); _hash(receipt['contract_hash'])
        kernel._integer(receipt['sequence'], 1, cut['sequence']); kernel._time(receipt['received_at'])
        _need(receipt['received_at'] <= cut['at'], 'receipt_future')
        if receipt['sequence'] > authority['cursor']['sequence']:
            _need(receipt['received_at'] >= authority['cursor']['at'], 'receipt_clock_backwards')
        _need(type(receipt['correlation']) is str and receipt['correlation'] in authority['entity_ids'], 'unauthorized_correlation')
        subscription = subscriptions.get(receipt['event_type'])
        _need(subscription is not None, 'subscription')
        kernel._validate_output(subscription['output_schema'], receipt['output'])
        reference = graph['references'][subscription['correlation_ref']]
        _need(reference['kind'] == 'string', 'correlation_type')
        previous_id = observed.get(receipt['sequence'])
        _need(previous_id is None or previous_id == receipt['event_id'], 'sequence_conflict')
        observed[receipt['sequence']] = receipt['event_id']
        batch_times[receipt['sequence']] = receipt['received_at']
        metadata = _metadata(receipt); previous = by_identity.get(receipt['event_id'])
        if previous is not None:
            _need(_same(previous, metadata), 'receipt_changed')
            continue
        by_identity[receipt['event_id']] = metadata
        if receipt['sequence'] <= authority['initial_cut']['sequence']:
            continue
        eligible = any(window['status'] == 'open'
            and subscriptions[receipt['event_type']]['id'] == window['subscription_id']
            and _eligible(window, metadata) for window in authority['windows'].values())
        if not eligible:
            continue
        _need(receipt['sequence'] > authority['cursor']['sequence'], 'receipt_before_cursor')
        _need(receipt['received_at'] >= authority['cursor']['at'], 'receipt_clock_backwards')
        _need(len(authority['receipts']) < kernel.MAX_EVENTS, 'receipt_limit')
        kernel._buffer_event(graph, state, receipt['event_id'], receipt['event_type'], receipt['correlation'],
            receipt['output'], cut['at'], received_at=receipt['received_at'], explicit_received_at=True)
        authority['receipts'][receipt['event_id']] = metadata
    ordered = sorted(batch_times.items())
    _need(all(left[1] <= right[1] for left, right in zip(ordered, ordered[1:])), 'receipt_clock_backwards')


def _command(state, command):
    _keys(command, _COMPLETE, 'command_shape'); kernel._safe(command)
    body = {key: command[key] for key in ('activation_id', 'output', 'actor_id', 'expected_version', 'cancel_epoch')}
    return kernel._command(state, 'complete', command['request_id'], command['actor_id'], body)


def _completion(graph, state, meta, command):
    kernel._integer(command['expected_version'], 0, 1000000000)
    kernel._integer(command['cancel_epoch'], 0, 1000000000)
    activation = state['activations'].get(command['activation_id'])
    _need(activation is not None, 'activation')
    node = meta['nodes'][activation['node_id']]
    _need(state['status'] not in kernel._TERMINAL and activation['status'] == 'ready'
        and activation['id'] in state['eligible_ability_ids'] + state['ready_human_ids']
        and activation['version'] == command['expected_version']
        and activation['epoch'] == command['cancel_epoch'] == state['cancel_epoch'], 'activation_stale')
    _need(node['kind'] in {'human', 'ability'} and node['actor_id'] == command['actor_id'], 'actor')
    schema = node['execution']['output_schema'] if node['kind'] == 'ability' else node['output_schema']
    kernel._validate_output(schema, command['output'])
    kernel._check_produced_scalars(graph, meta, node['id'], command['output'])
    return activation


@kernel._public
def new_run(graph, inputs, cut, *, binding_hash, entity_ids, scope_hash, _observer=None):
    cut = _cut(cut); _hash(binding_hash); _hash(scope_hash); entities = _entities(entity_ids)
    graph = kernel.validate_graph(graph)
    kernel._validate_output(graph['input_schema'], inputs)
    meta = kernel._metadata(graph)
    state = {'schema': kernel.STATE_SCHEMA, 'graph_hash': kernel._hash(graph), 'input_hash': kernel._hash(inputs),
        'inputs': copy.deepcopy(inputs), 'status': 'waiting', 'version': 1, 'cancel_epoch': 0,
        'clock': cut['at'], 'next_activation': 1, 'next_scope': 1, 'activations': {}, 'fork_scopes': {},
        'events': [], 'commands': [], 'goal_receipts': [], 'predicate_receipts': [],
        'eligible_ability_ids': [], 'ready_human_ids': [], 'error': None}
    kernel._resolved(graph, state, meta, kernel._context(meta))
    kernel._spawn(state, meta, graph['entry'], kernel._context(meta), None, None, cut['at'], _observer=_observer)
    authority = {'schema': SCHEMA, 'graph_hash': state['graph_hash'], 'input_hash': state['input_hash'],
        'binding_hash': binding_hash, 'scope_hash': scope_hash, 'entity_ids': entities,
        'cancel_epoch': state['cancel_epoch'], 'initial_cut': copy.deepcopy(cut), 'cursor': copy.deepcopy(cut),
        'windows': {}, 'receipts': {}}
    # One guarded advance owns both the v1 frontier and every window. Running
    # the old public initializer first could open a sibling wait before another
    # initial branch stops the run, leaving an unrecorded window behind.
    kernel._advance(graph, state, meta, cut['at'], wait_hook=_hook(authority, cut, _observer=_observer), _observer=_observer)
    _close_cancelled(state, authority, cut)
    result = kernel._return(None, state); authority = _seal(authority)
    check(graph, result, authority, binding_hash=binding_hash, entity_ids=entities, scope_hash=scope_hash)
    return result, authority


@kernel._public
def transition(graph, state, authority, cut, receipts, *, scope_hash, operation='advance', command=None, _observer=None):
    graph = kernel.validate_graph(graph)
    frozen = check(graph, state, authority, binding_hash=authority['binding_hash'],
        entity_ids=authority['entity_ids'], scope_hash=scope_hash)
    working, meta = kernel._state(graph, state, state['clock'])
    _need(operation in {'advance', 'complete'}, 'operation')
    if operation == 'complete':
        if _command(working, command): return copy.deepcopy(state), frozen
        _completion(graph, working, meta, command)  # Original CAS before any cut-driven changes.
    else:
        _need(command is None, 'command_shape')
    cut = _cut(cut)
    _need(cut['sequence'] >= frozen['cursor']['sequence'] and cut['at'] >= state['clock'], 'cut_backwards')
    _buffer(graph, working, frozen, receipts, cut)
    hook = _hook(frozen, cut, _observer=_observer)
    kernel._advance(graph, working, meta, cut['at'], wait_hook=hook, _observer=_observer)
    if operation == 'complete':
        activation = _completion(graph, working, meta, command)  # Preserve stop/unknown/epoch guards.
        kernel._finish(activation, command['output'], cut['at']); activation['draft'] = None
        kernel._observe_v264(_observer, {'kind': 'complete', 'activation_id': command['activation_id']})
        kernel._follow(graph, working, meta, activation, 'success', cut['at'], _observer=_observer)
        kernel._advance(graph, working, meta, cut['at'], wait_hook=hook, _observer=_observer)
    frozen['cursor'] = copy.deepcopy(cut); _close_cancelled(working, frozen, cut)
    result = kernel._return(state, working); frozen = _seal(frozen)
    check(graph, result, frozen, binding_hash=frozen['binding_hash'], entity_ids=frozen['entity_ids'], scope_hash=scope_hash)
    return result, frozen


@kernel._public
def sync(graph, state, authority, cut, *, scope_hash):
    """Cold sidecar synchronization after old-v1 draft/cancel; never advances."""
    graph = kernel.validate_graph(graph); kernel.check_run(graph, state)
    frozen = _inspect(graph, state, authority, binding_hash=authority['binding_hash'],
        entity_ids=authority['entity_ids'], scope_hash=scope_hash, cancelling=True)
    cut = _cut(cut)
    _need(_same(cut, frozen['cursor']), 'sync_cut')
    _close_cancelled(state, frozen, cut)
    frozen = _seal(frozen)
    check(graph, state, frozen, binding_hash=frozen['binding_hash'], entity_ids=frozen['entity_ids'], scope_hash=scope_hash)
    return frozen
