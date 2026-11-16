"""Exact nested-review data contracts, not a source or execution authority.

Fresh material/IAM checks belong to the API's current_nested_review_source.
Engineering records explicitly do not claim a person's review or production
qualification. This module does not read a database, capture, file or model.
"""
import copy
import json

from . import business_flow_nested_plan_v192 as plans
from . import business_flow_review_store as reviews


RECORD = 'business-flow-nested-review-record-v195'
SNAPSHOT = 'business-flow-nested-review-snapshot-v195'
SOURCE = 'business-flow-nested-review-source-v195'
COMMAND = 'business-flow-nested-review-command-v195'
RESULT = 'business-flow-nested-review-result-v195'
REQUEST = 'business-flow-nested-review-original-result-v195'
PREVIEW = 'business-flow-nested-review-preview-v195'
MODES = ('author_self_review', 'engineering_simulation')
SOURCE_BINDINGS = {'business-flow-nested-review-source-v193', 'business-flow-nested-component-review-source-v194'}
MAX_WIRE_BYTES = 32 * 1024
BODY_KEYS = {'schema', 'request_id', 'expected_version', 'save_request_id', 'value_hash',
    'ack_scope', 'ack_inferences', 'reason', 'review_mode'}
SOURCE_KEYS = {'schema', 'attempt_id', 'source_binding_hash', 'value_hash', 'diagnostic_context_hash',
    'original_source_hash', 'current_source_hash', 'current_materials_hash', 'review_mode'}
EXTRA_RECORD_KEYS = {'review_mode', 'simulation_only', 'value_schema'}
ERROR = '递归稿核对模式、原保存版本或来源合同不完整；未记录人审或授予生产执行资格'


def need(ok):
    if not ok:
        raise ValueError(ERROR)


def review_mode(value):
    need(type(value) is str and value in MODES)
    return value


def mode_fields(value):
    mode = review_mode(value)
    simulation = mode == 'engineering_simulation'
    return {'review_mode': mode, 'simulation_only': simulation, 'value_schema': plans.PLAN,
        'human_review': 'not_human_review' if simulation else 'author_self_reviewed'}


def status(value):
    return 'engineering_reviewed' if review_mode(value) == 'engineering_simulation' else 'qualified'


def parse_wire(raw):
    try:
        need(type(raw) is bytes and 0 < len(raw) <= MAX_WIRE_BYTES)
        def pairs(items):
            result = {}
            for key, value in items:
                need(key not in result); result[key] = value
            return result
        def constant(_):
            raise ValueError(ERROR)
        value = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=pairs, parse_constant=constant)
        need(type(value) is dict and set(value) == BODY_KEYS and value['schema'] == COMMAND)
        reviews.identity(value['request_id']); reviews.identity(value['save_request_id'])
        need(type(value['expected_version']) is int and value['expected_version'] >= 1)
        need(type(value['value_hash']) is str and reviews.SHA.fullmatch(value['value_hash']) is not None)
        need(value['ack_scope'] is True and value['ack_inferences'] is True)
        need(type(value['reason']) is str and 2 <= len(value['reason']) <= 12000
            and len(value['reason'].strip()) >= 2)
        review_mode(value['review_mode'])
        reviews._bytes(value, MAX_WIRE_BYTES)
        return value
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def check_value(value, source_binding, *, mode):
    """Typed stored value only; callers still owe the complete fresh source gate."""
    review_mode(mode)
    need(type(source_binding) is dict and source_binding.get('schema') in SOURCE_BINDINGS
        and source_binding.get('value_schema') == plans.PLAN and source_binding.get('generation_profile') == 'flow-v193')
    checked = plans._typed_plan(value)
    need(reviews._same(value, checked))
    if mode == 'author_self_review':
        need(not checked['business']['questions'])
    return copy.deepcopy(checked)


def flow_summaries(value, source_binding, *, mode):
    checked = check_value(value, source_binding, mode=mode)
    def actions(phases):
        count = 0
        for phase in phases:
            if phase['kind'] in {'steps', 'repeat'}:
                count += len(phase['steps']) + (1 if phase['kind'] == 'repeat' else 0)
            else:
                count += 1 + sum(actions(branch['phases']) for branch in phase['branches'])
        return count
    return [{'flow_index': i, 'name': flow['name'], 'action_count': 1 + actions(flow['phases'])}
        for i, flow in enumerate(checked['business']['flows'])]


def source_snapshot(value, attempt_id, source_binding, value_hash, *, mode):
    review_mode(mode)
    need(type(value) is dict and set(value) == SOURCE_KEYS and value['schema'] == SOURCE
        and value['review_mode'] == mode)
    reviews._bytes(value, 8192); reviews.identity(value['attempt_id'])
    for key in SOURCE_KEYS - {'schema', 'attempt_id', 'review_mode'}:
        need(type(value[key]) is str and reviews.SHA.fullmatch(value[key]) is not None)
    need(value['attempt_id'] == attempt_id and value['source_binding_hash'] == reviews.digest(source_binding)
        and value['value_hash'] == value_hash and value['original_source_hash'] == value['current_source_hash'])
    return copy.deepcopy(value)
