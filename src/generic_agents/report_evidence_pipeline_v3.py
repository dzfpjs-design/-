"""V3 adapters for the existing private pipeline, not a parallel executor.

All journal/ledger/lease/owner access belongs to the passed pipeline. This
module never promotes a saved-facts view or an old failed protocol response.
The outer row checker must verify the complete immutable preview first.
"""
import copy

from .contracts import digest
from .report_evidence_preparation import GENERATION_V3
from .report_evidence_wire_v3 import build_map_projection, build_reduce_projection, MAX_WIRE_VALUE_BYTES
from .report_evidence_manual_v5 import build_manual_projection, CONTEXT


def applies(preview):
    return preview['map_preparation']['generation_contract']['schema'] == GENERATION_V3


def dispatch(preview, key, authorized):
    return {'schema': 'report-evidence-v3-dispatch-v1',
        'model_id': preview['body']['model_id'], 'authorized': authorized,
        'map_preparation_hash': preview['map_preparation']['preparation_hash'],
        'map_plan_hash': preview['map_preparation']['plan']['plan_hash']}


def check_response(pipeline, row, key, response, descriptor):
    policy = copy.deepcopy(pipeline._spec(row, key)['input_policy'])
    if policy.pop('single_batch', None) is not True:
        raise ValueError('V3 requires its frozen single-response policy')
    receipt = response['receipt']
    if (receipt.get('evidence_profile') != policy or receipt.get('finish_reason') != 'stop'
            or type(receipt.get('response_choice_count')) is not int or receipt['response_choice_count'] != 1
            or descriptor['wire_bytes'] > MAX_WIRE_VALUE_BYTES):
        raise ValueError('Original V3 response is incomplete or differs from its frozen output profile')
    if getattr(row, 'request_id', '').startswith('fl_'):
        from .report_evidence_delegated_wire_v1 import verify_saved_response
        verify_saved_response(pipeline, row, key, response, descriptor)


def map_input(pipeline, row, key, catalog):
    stage = pipeline._stage(row, key)
    descriptor = pipeline._descriptor(row, key)
    response = pipeline._parsed(row, key)
    expected = pipeline.journal_binding(row, key, stage['reserved_call_id'])
    args = {'binding': descriptor['binding'], 'expected_binding': expected,
        'wire_sha256': descriptor['wire_sha256'], 'catalog': catalog,
        'allowed_span_refs': pipeline._authorized(row, key)['span_refs']}
    projection = build_map_projection(response['value'], **args)
    return {'wire': response['value'], **{name: copy.deepcopy(value)
        for name, value in args.items() if name != 'catalog'}, 'projection': projection}


def complete_map_inputs(pipeline, row, catalog):
    chunks = row.data['preview']['map_preparation']['plan']['chunks']
    if ([stage['chunk_id'] for stage in row.data['maps']] != [chunk['chunk_id'] for chunk in chunks]
            or any(stage['status'] != 'succeeded' for stage in row.data['maps'])):
        raise ValueError('V3 reduction requires exactly every frozen Map, in its original order')
    result = []
    for chunk, stage in zip(chunks, row.data['maps']):
        item = map_input(pipeline, row, chunk['chunk_id'], catalog)
        projection = item['projection']
        if (digest(projection) != stage['result_hash']
                or len(projection['claims']) != stage['result_count']):
            raise ValueError('V3 Map projection differs from the saved original stage')
        result.append(item)
    return result


def stage_projection(pipeline, row, key, catalog):
    if key != 'reduce':
        return map_input(pipeline, row, key, catalog)['projection']
    descriptor = pipeline._descriptor(row, key)
    response = pipeline._parsed(row, key)
    prepared = pipeline._authorized(row, key)
    return build_reduce_projection(response['value'], binding=descriptor['binding'],
        expected_binding=pipeline.journal_binding(row, key, pipeline._stage(row, key)['reserved_call_id']),
        wire_sha256=descriptor['wire_sha256'], catalog=catalog,
        allowed_span_refs=[item[0] for item in prepared['payload']['spans']],
        context=row.data['preview']['map_preparation']['context'],
        map_inputs=complete_map_inputs(pipeline, row, catalog))


def manual_projection(pipeline, row, p, body, value, catalog):
    preview = row.data['preview']
    review_context = {'schema': CONTEXT, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'draft_id': row.id, 'request_id': str(body.request_id), 'accepted_revision': body.revision + 1,
        'model_result_hash': row.data['model_result_hash'], 'source_hash': preview['source_hash'],
        'catalog_hash': catalog['descriptor']['catalog_hash']}
    return build_manual_projection(value, review_context=review_context, catalog=catalog,
        allowed_span_refs=list(catalog['index']), context=preview['map_preparation']['context'])


def reduction_metadata(reduced):
    return None if reduced is None else {key: copy.deepcopy(value) for key, value in reduced.items()
        if key not in {'result', 'reconciliation'}}
