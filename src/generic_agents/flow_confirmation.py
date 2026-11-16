"""Preserve edits while clearing confirmations whose material basis changed."""
from __future__ import annotations

import copy
import json

from .flow_sources import flow_source_ids


def _materials(configuration):
    return {row['id']: row for row in configuration.get('materials', [])
            if isinstance(row, dict) and isinstance(row.get('id'), str)}


def _basis(material):
    if material is None:
        return None
    return {**{key: material.get(key) for key in ('name', 'hash', 'version')},
            'archived': bool(material.get('archived'))}


_STEP_FACTS = ('name', 'purpose', 'agent', 'role', 'output', 'condition', 'nextId', 'elseId')
_FLOW_FACTS = ('trigger', 'output')
_HISTORY_LIMIT = 20


def _text(item, key):
    value = item.get(key, '')
    return value if isinstance(value, str) else ''


def _refs(item):
    value = item.get('citations', [])
    return value if isinstance(value, list) else []


def _only_old_refs(previous, item):
    def keys(owner):
        return {json.dumps([ref.get(key) for key in ('material_id', 'block_id', 'quote')],
                           ensure_ascii=False, sort_keys=True) for ref in _refs(owner)}
    return keys(item) <= keys(previous)


def _protected_basis(item):
    return bool(_refs(item) or _text(item, 'inferenceReason'))


def _basis_snapshot(item, scope):
    return {'scope': scope, 'reason': '人工修改前的依据，仅供回查，不证明当前值',
            'values': {key: _text(item, key) for key in (_STEP_FACTS if scope == 'step' else _FLOW_FACTS)},
            'sourceId': _text(item, 'sourceId'), 'citation': _text(item, 'citation'),
            'citations': copy.deepcopy(_refs(item)), 'inferenceReason': _text(item, 'inferenceReason')}


def _basis_history(previous, item, extra=()):
    rows = []
    for owner in (previous, item):
        history = owner.get('basisHistory', [])
        for row in history if isinstance(history, list) else []:
            if isinstance(row, dict) and row not in rows:
                rows.append(copy.deepcopy(row))
    for row in extra:
        if row not in rows:
            rows.append(copy.deepcopy(row))
    counts = [owner.get('basisHistoryOmitted', 0) for owner in (previous, item)]
    omitted = max([count for count in counts if type(count) is int] + [0]) + max(0, len(rows) - _HISTORY_LIMIT)
    if rows:
        # Retain the original suggestion as well as the most recent changes.
        item['basisHistory'] = rows if len(rows) <= _HISTORY_LIMIT else [rows[0], *rows[-(_HISTORY_LIMIT - 1):]]
    if omitted:
        item['basisHistoryOmitted'] = omitted


def _synchronize_item_basis(previous, item, scope):
    if previous is None:
        return
    facts = _STEP_FACTS if scope == 'step' else _FLOW_FACTS
    source_changed = scope == 'step' and _text(previous, 'sourceId') != _text(item, 'sourceId')
    manual_changed = source_changed or scope == 'step' and _text(previous, 'citation') != _text(item, 'citation')
    facts_changed = any(_text(previous, key) != _text(item, key) for key in facts)
    stale_refs = (manual_changed or facts_changed) and bool(_refs(item)) and _only_old_refs(previous, item)
    stale_explanation = ((manual_changed or facts_changed) and bool(_text(item, 'inferenceReason'))
                         and _text(previous, 'inferenceReason') == _text(item, 'inferenceReason'))
    withdrew = _protected_basis(previous) and not _protected_basis(item)
    archive = (stale_refs or stale_explanation or withdrew
               or source_changed and bool(_text(previous, 'citation') or _protected_basis(previous)))
    _basis_history(previous, item, [_basis_snapshot(previous, scope)] if archive else [])
    if stale_refs:
        item['citations'] = []
    if stale_explanation:
        item['inferenceReason'] = ''
    if (scope == 'step' and _text(previous, 'citation') == _text(item, 'citation')
            and (source_changed or stale_refs or stale_explanation)):
        item['citation'] = '；'.join(ref['block_id'] + '：' + ref['quote'] for ref in _refs(item))
    if (scope == 'step' and facts_changed and not manual_changed
            and (stale_refs or stale_explanation) and not _refs(item)):
        item['sourceId'] = ''
    if (stale_refs or stale_explanation or withdrew) and not _protected_basis(item):
        item['basisKind'] = 'manual'
    if _protected_basis(item) and item.get('basisKind') == 'manual':
        item.pop('basisKind', None)


def synchronize_flow_basis(previous, proposed):
    """Rebind human edits without treating an old model answer as new evidence.

    Histories are local audit records, never consumed by flow_source_ids or
    workflow_context. New structured references remain current. Comparisons are
    scoped to the business fields or the same step ID, preserving other steps.
    """
    value = copy.deepcopy(proposed)
    _synchronize_item_basis(previous, value, 'business')
    steps = {item.get('id'): item for item in previous.get('steps', [])}
    for item in value.get('steps', []):
        _synchronize_item_basis(steps.get(item.get('id')), item, 'step')
    return value


def synchronize_flow_confirmations(previous, proposed, *, changed_sources=()):
    """A material edit cannot silently carry an earlier approval forward.

    Newly confirmed flows keep the existing save contract. Already approved
    flows lose their approval when a referenced original's saved metadata or
    version changes, even if a caller repeats the old confirmation fields.
    The configuration, manual edits and unrelated approvals remain intact.
    """
    from .instance_compiler import signature_value

    value = copy.deepcopy(proposed)
    for name, flow in value.get('flows', {}).items():
        value['flows'][name] = synchronize_flow_basis(previous.get('flows', {}).get(name, {}), flow)
    old_materials, materials = _materials(previous), _materials(value)
    changed = set(changed_sources)
    changed.update(identity for identity in old_materials.keys() | materials.keys()
                   if _basis(old_materials.get(identity)) != _basis(materials.get(identity)))
    for name, flow in value.get('flows', {}).items():
        if not flow.get('confirmedSignature'):
            if flow.get('confirmedAt'):
                flow['confirmedAt'] = None
            continue
        identities = flow_source_ids(flow)
        before = previous.get('flows', {}).get(name, {})
        unusable = any(identity not in materials or materials[identity].get('archived')
                       for identity in identities)
        affected = bool(before.get('confirmedSignature')) and bool(changed.intersection(identities))
        try:
            matches = json.loads(flow['confirmedSignature']) == signature_value(value, flow)
        except (TypeError, ValueError):
            matches = False
        if unusable or affected or not matches:
            flow['confirmedSignature'] = None
            flow['confirmedAt'] = None
    return value
