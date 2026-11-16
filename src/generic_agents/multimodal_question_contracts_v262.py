"""Dedicated owner-private question wire; original V252 contracts stay intact."""
import copy
from typing import Literal

from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator

from .contracts import Contract
from . import multimodal_query_contracts_v262 as source
from . import multimodal_query_compute_v262 as arithmetic
from . import pixel_question_contracts_v252 as pixel

FAMILY = source.FAMILY
SCHEMA = 'multimodal-question-v262'
MAX_RAW = 128 * 1024
HASH = pixel.HASH
UUID_RE = pixel.UUID_RE
STATUS = pixel.STATUS
obj = pixel.object_schema
text = pixel.text_schema
array = pixel.array_schema


def unicode_text(value):
    value.encode('utf-8', errors='strict')
    if '\x00' in value: raise ValueError('内容不能包含不可保存的空字符')
    return value


class PreviewInput(Contract):
    schema: Literal['multimodal-question-preview-input-v262'] = 'multimodal-question-preview-input-v262'
    release_id: StrictStr
    task_id: StrictStr | None = None
    entity_id: StrictStr | None = None
    question: StrictStr = Field(min_length=1, max_length=4000)
    model_profile_id: StrictStr
    options_hash: StrictStr = Field(pattern=HASH)
    registry_hash: StrictStr = Field(pattern=HASH)

    @model_validator(mode='after')
    def actual_ids(self):
        for value in (self.release_id, self.task_id, self.entity_id, self.model_profile_id):
            if value is not None and not source.identity(value): raise ValueError('资源编号须为标准小写UUID')
        unicode_text(self.question)
        if not self.question.strip(): raise ValueError('请填写原问题')
        return self


class Submit(PreviewInput):
    schema: Literal['multimodal-question-submit-v262'] = 'multimodal-question-submit-v262'
    request_id: StrictStr = Field(min_length=12, max_length=100)
    preview_hash: StrictStr = Field(pattern=HASH)
    confirm_cloud: Literal[True]

    @field_validator('request_id')
    @classmethod
    def request_unicode(cls, value): return unicode_text(value)

    @field_validator('confirm_cloud', mode='before')
    @classmethod
    def consent(cls, value):
        if value is not True: raise ValueError('须明确同意本次问题规划外发')
        return value


class Command(Contract):
    schema: Literal['multimodal-question-command-v262'] = 'multimodal-question-command-v262'
    request_id: StrictStr = Field(min_length=12, max_length=100)
    version: StrictInt = Field(ge=1)
    reason: StrictStr = Field(min_length=1, max_length=2000)
    expected_request_hash: StrictStr = Field(pattern=HASH)
    operation: Literal['cancel', 'retry']

    @model_validator(mode='after')
    def strings(self):
        unicode_text(self.request_id); unicode_text(self.reason)
        if not self.reason.strip(): raise ValueError('请填写操作理由')
        return self


def preview_input(body):
    value = body.model_dump(mode='json') if hasattr(body, 'model_dump') else body
    return {key: value[key] for key in PreviewInput.model_fields if key != 'schema'} | {
        'schema': 'multimodal-question-preview-input-v262'}


def intent_schema():
    variants = [obj({'facet': {'const': facet}, 'question_quote': text(), 'intent': native})
        for facet, native in [('pixel', pixel.intent_schema()), ('task_sound', source.sound_intent_schema())]]
    return obj({'schema': {'const': source.INTENT}, 'subqueries': array({'oneOf': variants}, 2, 1),
        'unmet_requirements': array(text(2000), 8)})


def model_result_schema():
    return {'oneOf': [obj({'status': {'const': 'candidate'}, 'intent': intent_schema(), 'explanation': text(2000)}),
        obj({'status': {'const': 'needs_clarification'}, 'questions': array(text(2000), 8, 1),
            'limitations': array(text(2000), 8)})]}


def tool_input_schema():
    return obj({'query_id': {'type': 'string', 'pattern': UUID_RE}, 'plan_hash': {'type': 'string', 'pattern': HASH}})


def answer_schema():
    """Closed whole SDK output; exact deterministic recomputation is a second gate."""
    sha = {'type': 'string', 'pattern': HASH}; uid = {'type': 'string', 'pattern': UUID_RE}
    nullable_uid = {'type': ['string', 'null'], 'pattern': UUID_RE}
    nullable_sha = {'type': ['string', 'null'], 'pattern': HASH}
    integer = {'type': 'integer', 'minimum': 0, 'maximum': 1000}
    metric = {'type': 'integer', 'minimum': 0, 'maximum': 1000000}
    moment = {'type': ['string', 'null'], 'maxLength': 100}
    scope = obj({'instance_id': uid, 'authorization_release_id': uid, 'task_id': nullable_uid, 'entity_id': nullable_uid})
    refs = array(obj({'run_id': uid, 'record_hash': sha}), 1000)
    old = copy.deepcopy(pixel.answer_schema()['properties'])
    for key in ('query_id', 'request_hash', 'plan_hash', 'answer_hash'): old.pop(key)
    old['schema'] = {'const': 'pixel-question-computation-v252'}
    reception = obj({'state': {'enum': ['received', 'waiting_data', 'producer_failed', 'local_handoff_pending', 'blocked_integrity']},
        'observation_id': nullable_uid, 'event_id': nullable_uid,
        'memory': {'oneOf': [{'type': 'null'}, obj({'id': uid, 'version': {'type': 'integer', 'minimum': 1}, 'data_hash': sha})]},
        'receipt_hash': nullable_sha})
    timing = obj({key: moment for key in ('run_started_at', 'model_completed_at', 'registered_at', 'registration_day', 'received_at', 'recorded_at')})
    sound_ir = obj({'schema': {'const': 'task-sound-query-ir-v262'}, 'scope': scope,
        **source.sound_intent_schema()['properties']})
    sound_result = obj({'schema': {'const': arithmetic.SOUND_SCHEMA}, 'status': {'enum': ['computed', 'clarification_required']},
        'source_snapshot_hash': sha, **{key: source.sound_intent_schema()['properties'][key]
            for key in ('operation', 'metric', 'group_by', 'time', 'filters')},
        'coverage': obj({'basis': {'const': 'registered_run_scan'}, 'complete_registered_inventory': {'const': True},
            'scanned_records': integer, 'reception_matched_records': integer, 'matched_records': integer,
            'unknown_time_records': integer, 'time_filter_resolved': {'type': 'boolean'},
            'expected_sources_verified': {'const': False}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 1000}}),
        'state_counts': obj({key: integer for key in ('received', 'waiting_data', 'producer_failed', 'local_handoff_pending', 'blocked_integrity')}),
        'groups': array(obj({'key': text(nullable=True), 'run_ids': array(uid, 1000), 'matched_records': integer,
            'value': {'type': ['integer', 'null'], 'minimum': 0}}), 1000),
        'references': array(obj({'run_id': uid, 'task_id': uid, 'entity_id': nullable_uid, 'record_hash': sha,
            'selection': {'enum': ['matched', 'unknown_time']}, 'reception': reception, 'timing': timing,
            'result_hash': nullable_sha, 'values': {'oneOf': [{'type': 'null'}, obj({key: metric for key in
                ('window_count', 'candidate_class_count', 'candidate_window_count')})]}}), 1000),
        'unit': {'enum': ['registered_runs', 'none', 'summed_candidate_window_entries', 'summed_candidate_class_entries']},
        'metrics_basis': {'const': 'independently_received_results_only'}, 'candidate_only': {'const': True},
        'not_authoritative': {'const': True}, 'physical_state_verified': {'const': False}, 'source_time_verified': {'const': False},
        'unmet_requirements': array(text(2000), 10), 'limitations': array(text(2000), 32)})
    variants = []
    for facet, ir, computation in [('pixel', pixel.ir_schema(), obj(old)), ('task_sound', sound_ir, sound_result)]:
        variants.append(obj({'facet': {'const': facet}, 'question_quote': text(), 'ir': ir, 'as_of': text(100),
            'outer_source_snapshot_hash': sha, 'inner_source_snapshot_hash': sha,
            'status': {'enum': ['computed', 'clarification_required']}, 'source_record_refs': refs, 'computation': computation}))
    computation = obj({'schema': {'const': arithmetic.SCHEMA}, 'workspace_id': uid, 'captured_by': uid, 'scope': scope,
        'as_of': text(100), 'plan_hash': sha, 'question_hash': sha, 'registry_hash': sha, 'source_snapshot_hash': sha,
        'status': {'enum': ['computed', 'computed_with_unmet_requirements', 'clarification_required']},
        'facets': array({'oneOf': variants}, 2, 1), 'unmet_requirements': array(text(2000), 10),
        'verification': obj({key: {'const': value} for key, value in arithmetic.VERIFICATION.items()}), 'result_hash': sha})
    return obj({'schema': {'const': 'multimodal-question-answer-v262'}, 'query_id': uid, 'request_hash': sha,
        'plan_hash': sha, 'source_snapshot_hash': sha, 'source_audit_id': uid, 'source_proof_hash': sha,
        'computation': computation, 'answer_hash': sha})
