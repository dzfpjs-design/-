"""Read-only evidence explanation; no model call or operational authority.

All public data is derived from authenticated, immutable originals. This
candidate is default off, scoped to the recovery reader and a disposable PG.
It never returns raw evidence, model text, credentials or internal references.
"""
from datetime import timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import engine
from .gateway import ModelBlocked
from .models import Audit, CloudCall
from .setup_api import DB, P
from .temporal import instant
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288, row_values
from . import same_goal_advice_contracts_v302 as contracts
from . import same_goal_advice_first_response_candidate_v2 as response
from . import same_goal_advice_first_response_storage_candidate_v2 as storage
from . import same_goal_advice_recovery_api_candidate_v2 as recovery_api
from . import same_goal_advice_source_scope_v309 as source_scope
from . import same_goal_advice_spool_recovery_candidate_v2 as recovery
from . import same_goal_advice_unlimited_preview_candidate_v2 as preview

router = APIRouter()
BASE = '/tasks/{task_id}/business-advice/evidence-explanation'


def _one_observation_freshness(fact, sources, recorded, age, now):
    if fact['kind'] != 'observation' or fact['semantics'] != 'instantaneous':
        return None
    window = fact['time_scope']
    if not window['start'] or not window['end']:
        return None
    at = instant(window['start'])
    if at != instant(window['end']) or at > now:
        return None
    matches = []
    for citation in fact['source_refs']:
        source = sources.get(citation['source_ref'])
        if not source or source['kind'] != 'observation':
            return None
        candidates = [r for r in recorded if r.get('observation_id') == source['source_id']
                      and r.get('entity_id') == fact['entity_id']]
        if len(candidates) != 1 or instant(candidates[0]['observed_at']) != at:
            return None
        deadline = at + timedelta(seconds=age)
        if candidates[0].get('valid_until'):
            deadline = min(deadline, instant(candidates[0]['valid_until']))
        matches.append(deadline)
    if not matches:
        return None
    return 'stale' if now >= min(matches) else 'current'


def _observation_meta(db, workspace, task_id, original, cloud):
    """Only map explicit source/time identities to a checked original policy.

    Observation occurrence time is not a validity deadline. The only freshness
    deadline derived here is the previously published rolling age policy and an
    optional original valid_until. Unknown/mismatched sources remain unknown.
    """
    frame = original['frame_snapshot']
    local = frame['decision_input']
    source_record = frame['source_record']
    evaluation_ref = source_record['mature_head']['evaluation_ref']
    row = db.scalar(select(GoalEvaluationV288).where(
        GoalEvaluationV288.workspace_id == workspace, GoalEvaluationV288.task_id == task_id,
        GoalEvaluationV288.id == evaluation_ref['id']).with_for_update(read=True, nowait=True))
    if row is None:
        return {}
    checked = row_values(row.data)
    if (checked['record_hash'] != row.record_hash or row.record_hash != evaluation_ref['record_hash']
            or checked['id'] != row.id or checked['workspace_id'] != workspace
            or checked['task_id'] != task_id):
        raise ValueError('original evaluation changed')
    policy = row.data['preparation_policy']
    if policy.get('time_basis') != 'rolling_latest':
        return {}
    age = policy.get('max_age_seconds')
    if type(age) is not int or age <= 0:
        return {}
    now = db.scalar(select(func.clock_timestamp()))
    if now is None or now.tzinfo is None:
        return {}
    recorded = row.data['evaluation_result']['result']['observation_evidence']
    sources = {item['source_ref']: item for item in local['sources']}
    cloud_sources = {item['source_ref']: item for item in cloud['sources']}
    facts = {item['fact_ref']: item for item in local['facts']}
    result = {}
    for item in cloud['facts']:
        if item['kind'] != 'observation':
            continue
        fact = facts.get(item['fact_ref'])
        if not fact or fact['kind'] != 'observation':
            continue
        original_refs = [citation['source_ref'] for citation in fact['source_refs']]
        cloud_refs = [citation['source_ref'] for citation in item['source_refs']]
        if (fact['time_scope'] != item['time_scope'] or fact['semantics'] != item['semantics']
                or original_refs != cloud_refs or any(ref not in sources or ref not in cloud_sources
                or sources[ref]['kind'] != cloud_sources[ref]['kind'] for ref in original_refs)):
            raise ValueError('original observation projection mismatch')
        freshness = _one_observation_freshness(fact, sources, recorded, age, now)
        if freshness:
            result[item['fact_ref']] = freshness
    return result


def _derive(cloud, opinion, freshness):
    """Return counts and categories, never infer current equipment status."""
    opinion = contracts.check_model_value(cloud, opinion)
    scopes = source_scope.provided_scopes(cloud, opinion)
    by_source = {row['source_ref']: row for row in cloud['sources']}
    by_scope = {row['source_ref']: row for row in scopes}
    by_fact = {row['fact_ref']: row for row in cloud['facts']}
    selected = opinion['recommendation']['supporting_fact_refs']
    entries = []
    counts = {'capability_count': 0, 'observation_count': 0, 'other_count': 0}
    for ref in selected:
        fact = by_fact[ref]
        kind = fact['kind'] if fact['kind'] in {'capability_declaration', 'observation'} else 'other'
        counts[{'capability_declaration':'capability_count', 'observation':'observation_count', 'other':'other_count'}[kind]] += 1
        scope_rows = [by_scope[c['source_ref']]['review_scope'] for c in fact['source_refs']]
        if 'not_reviewed' in scope_rows:
            scope = 'not_reviewed'
        elif 'provided_excerpt_only' in scope_rows:
            scope = 'provided_excerpt'
        elif all(x == 'provided_source_text_only' for x in scope_rows):
            scope = 'provided_source_text'
        else:
            scope = 'unknown'
        source_types = sorted({by_source[c['source_ref']]['kind'] for c in fact['source_refs']})
        window = fact['time_scope']
        at = (window['start'] if kind == 'observation' and fact['semantics'] == 'instantaneous'
              and window['start'] == window['end'] else None)
        entries.append({'kind': kind, 'source_scope': scope, 'source_types': source_types,
            'observed_at': at, 'observed_start': window['start'] if kind == 'observation' else None,
            'observed_end': window['end'] if kind == 'observation' else None,
            'freshness': freshness.get(ref, 'unknown') if kind == 'observation' else 'not_applicable'})
    observations = [item['freshness'] for item in entries if item['kind'] == 'observation']
    state = ('not_cited' if not observations else 'stale' if 'stale' in observations else
             'current' if all(x == 'current' for x in observations) else 'unknown')
    return {**counts, 'has_recommendation': opinion['recommendation']['candidate_ref'] is not None,
        'observation_state': state,
        'partial_source_count': sum(row['readability'] == 'partial' for row in cloud['sources']), 'items': entries}


def _read_one(workspace, task_id, permit_id, call_id, intent_id):
    # First prove that the supplied permit is the unique original for this call.
    recovery.current_context(workspace, call_id, intent_id, permit_id)
    with Session(engine()) as db:
        saved = storage.read(db, workspace, call_id, intent_id)
        if not isinstance(saved, tuple) or len(saved) != 3:
            raise ValueError('original response unavailable')
        call = db.scalar(select(CloudCall).where(CloudCall.workspace_id == workspace,
            CloudCall.id == call_id).with_for_update(read=True, nowait=True))
        if call is None or call.status != 'succeeded':
            raise ValueError('business advice unavailable')
        permit = db.scalar(select(Audit).where(Audit.workspace_id == workspace, Audit.id == permit_id)
            .with_for_update(read=True, nowait=True))
        preview_id = permit.data['preview_ref']['id']
        shown = db.scalar(select(Audit).where(Audit.workspace_id == workspace, Audit.id == preview_id,
            Audit.action == preview.ACTION).with_for_update(read=True, nowait=True))
        if shown is None or shown.data['task_id'] != task_id:
            raise ValueError('preview task mismatch')
        cloud = shown.data['preview_record']['cloud_request']['decision_input']
        opinion, _ = response.parse_saved(saved[2], cloud_call_id=call_id,
            intent_audit_id=intent_id, schema=contracts.model_value_schema())
        # All reference and supplied-source checks precede any display.
        contracts.check_model_value(cloud, opinion)
        freshness = _observation_meta(db, workspace, task_id, shown.data, cloud)
        value = _derive(cloud, opinion, freshness)
        db.rollback()
        return value


@router.get(BASE)
def listing(task_id: str, p: P, db: DB, cursor: str | None = None):
    if not settings().v2_advice_evidence_explanation_enabled:
        return {'enabled': False, 'items': []}
    # Existing recovery access requires authenticated admin and disposable PG.
    identities, next_cursor = recovery_api._identities(db, p, task_id, cursor=cursor)
    db.rollback()
    items = []
    for permit_id, call_id, intent_id, recorded_at, is_permit in identities:
        entry = {'state': 'needs_review', 'recorded_at': recorded_at}
        if is_permit:
            try:
                entry = {'state': 'ready', 'recorded_at': recorded_at,
                    'evidence': _read_one(p.workspace_id, task_id, permit_id, call_id, intent_id)}
            except (HTTPException, ModelBlocked, ValueError, TypeError, KeyError, AttributeError, IndexError):
                pass
        items.append(entry)
    recovery_api._access(db, p, task_id)
    return {'enabled': True, 'items': items, 'next_cursor': next_cursor}
