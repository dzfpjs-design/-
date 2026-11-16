"""Read-only UI explanation of the original graph goal's current limits.

This projection grants no authority. The worker and final dispatch fence check
again under the Task lock. No client derives a budget from private ledgers.
"""
from fastapi import HTTPException

from . import native_goals
from .native_goal_time import timeout
from .temporal import instant
from .workflow_graph_goal_guard_v133 import _context, _usage, verify_reservations

SCHEMA = 'workflow-graph-goal-gate-v133'


def _result(status, reason, human=False, ability=False):
    return {'schema': SCHEMA, 'status': status, 'reason': reason,
        'can_submit_new_result': human, 'can_retry_ability': ability}


def project(db, p, task):
    try:
        if db is not None:
            # Independent records distinguish genuine legacy tasks from a
            # once-confirmed goal whose Task-local markers are now missing.
            verify_reservations(db, p, task)
        context = _context(task)
        if context is None:
            return None
        if db is None:
            return _result('unavailable', 'goal_context_unavailable')
        if task.data.get('native_goal_acceptance'):
            return _result('accepted', 'goal_accepted')
        if task.status == 'workflow_cancelled' or task.data.get('native_goal_cancelled'):
            return _result('stopped', 'goal_stopped')
        spec = task.data['goal_spec']
        if timeout(task) or spec['deadline'] and native_goals.now() >= instant(spec['deadline']):
            return _result('expired', 'deadline_expired')
        calls, steps = _usage(db, p, task)
        if steps >= spec['max_steps']:
            from .workflow_graph_human_budget import authorize
            state = task.data.get('graph_runtime', {})
            ready = [identity for identity, activation in state.get('activations', {}).items()
                if activation.get('status') == 'ready']
            if len(ready) == 1 and authorize(db, p, task, ready[0], steps=steps, lock_sources=False) is not None:
                return _result('budget_blocked', 'human_budget_approved', human=True)
            return _result('budget_blocked', 'max_steps')
        if calls >= spec['max_tool_calls']:
            # Calling an ability and submitting a human result spend different
            # limits. A zero-call goal can still permit a human result.
            return _result('budget_blocked', 'max_tool_calls', human=True)
        return _result('active', None, human=True, ability=True)
    except (HTTPException, KeyError, ValueError, TypeError, AttributeError):
        return _result('unavailable', 'goal_context_unavailable')
