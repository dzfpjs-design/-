"""Closed v2 advice snapshots; never a runtime/dispatch authorization.

The source record keeps its existing format because its evidence and consent
semantics do not contain spending policy. Preparation, preview and execution
have new schema identifiers and the closed unlimited policy. Real DB policy,
membership, source/consent, lease and transport-intent checks remain mandatory.
"""
import copy
from typing import Literal

from . import same_goal_advice_contracts_v302 as old
from . import same_goal_advice_unlimited_policy_v2 as policy
from .contracts import digest

PREPARATION = 'same-goal-advice-unlimited-preparation-v2'
PREVIEW = 'same-goal-advice-unlimited-preview-v2'
EXECUTION = 'same-goal-advice-unlimited-execution-v2'
RESULT = 'same-goal-advice-unlimited-result-v2'


class PreparationSnapshot(old.PreparationSnapshot):
    schema: Literal['same-goal-advice-unlimited-preparation-v2']
    budget: policy.Limits


class PreviewRecord(old.PreviewRecord):
    schema: Literal['same-goal-advice-unlimited-preview-v2']
    budget: policy.Limits


class ExecutionSnapshot(old.ExecutionSnapshot):
    schema: Literal['same-goal-advice-unlimited-execution-v2']
    preview: PreviewRecord


class AdviceResult(old.AdviceResult):
    schema: Literal['same-goal-advice-unlimited-result-v2']


def check_preparation(raw, *, manifest):
    return old._check_preparation_model(raw, manifest=manifest, model=PreparationSnapshot)


def check_source(raw, *, preparation, manifest):
    return old._check_source_model(raw, preparation=preparation, manifest=manifest,
        preparation_checker=check_preparation)


def check_preview(raw, *, preparation, source, manifest):
    return old._check_preview_model(raw, preparation=preparation, source=source, manifest=manifest,
        model=PreviewRecord, preparation_checker=check_preparation, source_checker=check_source)


def check_plan(raw, *, preparation, manifest):
    return old._check_plan_model(raw, preparation=preparation, manifest=manifest, model=ExecutionSnapshot,
        preparation_checker=check_preparation, preview_checker=check_preview)


def seal_preparation(unsigned, *, manifest):
    return old._seal(unsigned, check_preparation, manifest=manifest)


def seal_source(unsigned, *, preparation, manifest):
    return old._seal(unsigned, check_source, preparation=preparation, manifest=manifest)


def seal_preview(unsigned, *, preparation, source, manifest):
    old.need(type(unsigned) is dict and 'preview_hash' not in unsigned)
    value = copy.deepcopy(unsigned); value['preview_hash'] = digest(value)
    return check_preview(value, preparation=preparation, source=source, manifest=manifest)


def seal_plan(unsigned, *, preparation, manifest):
    return old._seal(unsigned, check_plan, preparation=preparation, manifest=manifest)


def derive_result(snapshot, model_value, *, preparation, manifest):
    """Pure result derivation, not acceptance or a completed cloud call."""
    plan = check_plan(snapshot, preparation=preparation, manifest=manifest)
    preview = plan['preview']
    opinion = old.check_model_value(preview['cloud_request']['decision_input'], model_value)
    return old._parse(AdviceResult, {'schema': RESULT,
        'plan_ref': {'id': plan['scope']['analysis_plan_id'], 'record_hash': plan['record_hash']},
        'preview_hash': preview['preview_hash'],
        'evaluation_ref': plan['source_record']['mature_head']['evaluation_ref'],
        'input_hash': preview['cloud_request']['input_hash'], 'model_value': opinion,
        'review_state': 'ready_for_review', **{key: False for key in old.FLAGS}})


def check_result(raw, *, snapshot, preparation, manifest):
    old.need(type(raw) is dict and all(type(raw.get(key)) is bool and raw[key] is False for key in old.FLAGS))
    value = old._parse(AdviceResult, raw)
    expected = derive_result(snapshot, value['model_value'], preparation=preparation, manifest=manifest)
    old.need(old.base._json(value) == old.base._json(expected))
    return value
