"""Fixed server reasoning protocols; names and retained markers grant no rights.

Imports are deliberately inside the fixed branches. No workspace input chooses
a Python module, callback or import path. Each protocol retains its own complete
manifest, source, actual Job/Invocation, lease, journal and result checks.
"""
LEGACY = 'business_reasoning'
DECISION = 'business_decision_v276'
REVIEW = 'business_decision_v284'
READY = 'business_goal_ready_v288'
ADVICE = 'same_goal_advice_v302'
ADAPTERS = frozenset({LEGACY, DECISION, REVIEW, READY, ADVICE})


def applies(adapter):
    return type(adapter) is str and adapter in ADAPTERS


def family(adapter):
    if type(adapter) is str and adapter == LEGACY:
        from . import business_reasoning_v197
        return business_reasoning_v197
    if type(adapter) is str and adapter == DECISION:
        from . import business_decision_runtime_v276
        return business_decision_runtime_v276
    if type(adapter) is str and adapter == REVIEW:
        from . import business_decision_runtime_v284
        return business_decision_runtime_v284
    if type(adapter) is str and adapter == READY:
        from . import business_goal_ready_runtime_v288
        return business_goal_ready_runtime_v288
    if type(adapter) is str and adapter == ADVICE:
        from . import same_goal_advice_runtime_v308
        return same_goal_advice_runtime_v308
    raise ValueError('Unsupported server-owned reasoning protocol')


def for_adapter(adapter):
    """Optional fixed dispatch for adjacent gates; unknown adapters stay None."""
    return family(adapter) if applies(adapter) else None


def require_protocol(protocol):
    if (protocol is family(LEGACY) or protocol is family(DECISION) or protocol is family(REVIEW)
            or protocol is family(READY) or protocol is family(ADVICE)):
        return protocol
    raise ValueError('Unsupported server-owned reasoning protocol')


def validate_manifest(db, p, manifest):
    return family(manifest['execution']['adapter']).validate_manifest(db, p, manifest)


def execute(db, p, cap, inputs, key):
    return family(cap.data['execution']['adapter']).execute(db, p, cap, inputs, key)


def qualify_evidence(adapter, db, p, task, inputs):
    return family(adapter).qualify_evidence(db, p, task, inputs)


def retained_state(adapter, call):
    """Read a marker for routing only; it is never proof of a paid response."""
    if not applies(adapter) or call is None or type(call.data) is not dict:
        return None
    return call.data.get(family(adapter).STATE_KEY)


def original_capture(adapter, call, task, job, node):
    """The original API scheduling predicate; recover independently checks all.

    This pure shape check neither authorizes a model call nor adopts an answer.
    In particular a missing capture never becomes permission to send again.
    """
    if not applies(adapter) or adapter == ADVICE:
        # Plan-only advice owns its actual SendJob and SDK reconciliation;
        # it cannot be routed as an old graph activation by this hint.
        return False
    state = retained_state(adapter, call)
    return (call is not None and type(state) is dict
        and state.get('schema') == family(adapter).STATE
        and type(state.get('capture')) is dict and type(state.get('request')) is dict
        and type(state['request'].get('pin')) is dict
        and call.capability_id == node['execution']['capability_id']
        and call.data.get('capability_hash') == node['execution']['capability_hash']
        and call.data.get('input_hash') == job.data.get('input_hash')
        and call.data.get('job_id') == job.id
        and state['request']['pin'].get('job_id') == job.id
        and state['request']['pin'].get('task_id') == task.id)


def recovery_record(adapter):
    if adapter == LEGACY:
        return ('original_answer_recovery_v197', 'workflow.business_reasoning.original_answer_recovered_v197')
    if adapter == DECISION:
        return ('original_answer_recovery_v276', 'workflow.business_decision.original_answer_recovered_v276')
    if adapter == REVIEW:
        return ('original_answer_recovery_v284', 'workflow.business_decision.original_answer_recovered_v284')
    if adapter == READY:
        return ('original_answer_recovery_v288', 'workflow.business_goal_ready.original_answer_recovered_v288')
    raise ValueError('Unsupported server-owned reasoning protocol')
