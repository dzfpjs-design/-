"""Shared paid-response journal for explicitly known server reasoning protocols.

This preserves the original V197 first-response transaction, single provider
POST and all of its live authority checks. A workspace cannot name or inject a
Python protocol. Only the original reasoning and explicit decision protocols
are enabled by the fixed server dispatch.
"""

def execute(db, p, cap, inputs, key, *, protocol):
    """The normal gateway has already created the real running Invocation."""
    from .business_reasoning_protocols_v276 import require_protocol
    protocol = require_protocol(protocol)
    # Historical settlement uses its own transaction and locks this same row.
    # Never enter that path while retaining our own Invocation FOR UPDATE.
    call = protocol._call(db, p, key)
    if call.data.get(protocol.STATE_KEY) is not None:
        return protocol.recover(db, p, cap, inputs, key, invocation=call)
    if protocol.ADAPTER == 'same_goal_advice_v302':
        # This family owns a Plan/SendJob, not a graph activation. Establish
        # Task -> Plan -> Job ownership before taking its SDK row lock.
        # prepare_request repeats all live checks rather than trusting a cache.
        protocol._current(db, p, cap, inputs, key)
    call = protocol._call(db, p, key, lock=True)
    protocol.need(call.data.get(protocol.STATE_KEY) is None,
        '原业务分析已由另一次处理保存；请核对原号，未在持锁事务中重入或重发')
    model, frozen = protocol.prepare_request(db, p, cap, inputs, key)
    protocol.need(call.status == 'running' and call.capability_id == cap.id
        and call.data.get('input_hash') == frozen['pin']['input_hash']
        and call.data.get('capability_hash') == frozen['pin']['capability_hash'])
    protocol.need(not list(db.scalars(protocol.select(protocol.Audit).where(protocol.Audit.workspace_id == p.workspace_id,
        protocol.Audit.action == protocol.PREPARED, protocol.Audit.object_id == call.id))), '原业务分析来源审计仍存在，未重建或重发')
    call.data = {**call.data, protocol.STATE_KEY: {'schema': protocol.STATE, 'request': frozen, 'call_id': None, 'capture': None}}
    protocol.audit(db, p, protocol.PREPARED, call.id, protocol._anchor(frozen)); db.commit()

    def reserved(call_id):
        # A fresh graph/IAM/cancel/lease/current-model check, not a second
        # before_dispatch authorization or a permission inferred from a hash.
        _actual, current_model, _task, _job, _node, _scope = protocol._current(db, p, cap, inputs, key)
        protocol.qualify_evidence(db, p, _task, inputs)
        protocol.need(current_model.id == model.id and current_model.content_hash == frozen['pin']['model_profile_hash'])
        ids = (protocol.evidence_ids_from_request(frozen) if protocol.ADAPTER == 'same_goal_advice_v302'
            else protocol.evidence_ids(inputs))
        pins, _parts = protocol._evidence(db, p, ids, current_model)
        protocol.need(protocol._same(pins, frozen['evidence_pins']), '实际证据字节、版本或模型授权已变化，未发送')
        original = protocol._call(db, p, key, lock=True); state = protocol._checked_state(db, p, cap, inputs, key, original)
        protocol.need(state['call_id'] is None and state['capture'] is None)
        ledger = protocol.scoped(db, protocol.CloudCall, call_id, p)
        protocol.need(ledger.status == 'reserved' and ledger.capability_id == model.id
            and ledger.data.get('actor_id') == p.user_id
            and ledger.data.get('request_hash') == frozen['prepared']['request_hash']
            and ledger.data.get('dispatch_options_hash') == frozen['prepared']['dispatch_options_hash']
            and protocol._same(ledger.data.get('gateway_dispatch', {}).get('identity'), {
                'suggestion_id': original.id, 'job_id': frozen['pin']['job_id'],
                'task_id': frozen['pin']['task_id'], 'request_hash': frozen['request_hash'],
                'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'capability_id': model.id}),
            '实际模型预算预留未固定到原业务分析调用，未发送')
        state['call_id'] = call_id; original.data = {**original.data, protocol.STATE_KEY: state}
        protocol.audit(db, p, protocol.RESERVED, original.id, {**protocol._anchor(frozen), 'call_id': call_id}); db.commit()

    def captured(raw, receipt):
        # The paid first answer must survive cancellation or lease expiry.
        # This historical save does not revive a worker or authorise any action.
        from .db import engine
        body = raw.encode('utf-8', errors='surrogateescape')
        capture = {'raw_base64': protocol.base64.b64encode(body).decode('ascii'),
            'raw_sha256': protocol.hashlib.sha256(body).hexdigest(), 'receipt': protocol.copy.deepcopy(receipt)}
        with protocol.Session(engine()) as saved_db:
            original = protocol._call(saved_db, p, key, lock=True)
            state = protocol._checked_state(saved_db, p, cap, inputs, key, original)
            protocol.need(state['call_id'] == receipt.get('call_id'))
            if state['capture'] is not None:
                protocol.need(protocol._same(state['capture'], capture), '原首答已保存，不能替换为另一个回答')
                return
            state['capture'] = capture; protocol._bytes(state, protocol.MAX_STATE_BYTES)
            original.data = {**original.data, protocol.STATE_KEY: state}
            protocol.audit(saved_db, p, protocol.CAPTURED, original.id, {**protocol._anchor(frozen), 'call_id': state['call_id'],
                'raw_sha256': capture['raw_sha256'], 'receipt_hash': protocol.digest(receipt)}); saved_db.commit()

    from .gateway import cloud_json
    limited = ({'output_token_limit': frozen['output_token_limit']}
        if protocol.ADAPTER == 'same_goal_advice_v302' else {})
    value, receipt = cloud_json(model, frozen['messages'], frozen['model_value_schema'], p,
        on_reserved=reserved, on_response=captured, full_report=False,
        evidence_stage=protocol.transport.STAGE, dispatch_identity={'suggestion_id': call.id,
            'job_id': frozen['pin']['job_id'], 'task_id': frozen['pin']['task_id'],
            'request_hash': frozen['request_hash']}, **limited)
    result = protocol.check_result(frozen, value)
    original = protocol._call(db, p, key, lock=True)
    protocol._checked_state(db, p, cap, inputs, key, original)
    original.data = {**original.data, 'model_receipt': protocol.copy.deepcopy(receipt)}
    db.commit()
    return result

