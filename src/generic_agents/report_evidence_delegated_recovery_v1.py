"""Explicit, free recovery of a delegated V3 response from durable HTTP.

Never calls a provider, never creates a job and never sends remaining chunks.
The caller owns the report row lock and commits only after all preflight checks.
"""
from __future__ import annotations

import copy

from fastapi import HTTPException

from . import report_evidence_delegated_wire_v1 as delegated
from . import report_evidence_native_bridge_v1 as bridge
from . import report_evidence_wire_v3 as projection
from . import report_evidence_journal as journal
from .services import validate


def _preflight(pipeline, row, key):
    original, receipt, state = delegated.response(pipeline, row, key)
    if receipt['finish_reason'] != 'stop' or receipt['response_choice_count'] != 1:
        raise ValueError('原回答没有唯一正常结束的结果')
    binding = pipeline.journal_binding(row, key, pipeline._stage(row, key)['reserved_call_id'])
    descriptor = bridge.check_missing_journal(journal, pipeline._stage_journal_root(row, key), binding, original, receipt)
    value = journal._loads(original.encode('utf-8', errors='strict'))
    validate(pipeline._spec(row, key)['output_schema'], value)
    response = {'receipt': receipt, 'value': value}
    pipeline.v3.check_response(pipeline, row, key, response, descriptor)
    catalog = pipeline._catalog(row.data['preview']); authorized = pipeline._authorized(row, key)
    if key == 'reduce':
        projection.build_reduce_projection(value, binding=binding, expected_binding=binding,
            wire_sha256=descriptor['wire_sha256'], catalog=catalog,
            allowed_span_refs=[item[0] for item in authorized['payload']['spans']],
            context=row.data['preview']['map_preparation']['context'],
            map_inputs=pipeline.v3.complete_map_inputs(pipeline, row, catalog))
    else:
        projection.build_map_projection(value, binding=binding, expected_binding=binding,
            wire_sha256=descriptor['wire_sha256'], catalog=catalog,
            allowed_span_refs=authorized['span_refs'])
    return original, receipt, state, descriptor


def recover(pipeline, db, p, row, command):
    if not row.request_id.startswith('fl_') or not pipeline.v3.applies(row.data['preview']):
        pipeline.fail('evidence_recovery_unsupported', '此原稿不属于受托原包恢复协议', 409)
    stages = ([('reduce', row.data['reduction'])] if row.data['reduction']['preview'] else
              [(item['chunk_id'], item) for item in row.data['maps'] if item['status'] != 'succeeded'])
    targets, remaining = [], []
    for key, stage in stages:
        if stage['reserved_call_id'] is None:
            if stage['inflight'] or key == 'reduce':
                pipeline.fail('evidence_saved_response_incomplete', '原调用状态未知，不能免费恢复或再次付费')
            remaining.append(key)
        else:
            targets.append(key)
    if not targets:
        pipeline.fail('evidence_saved_response_incomplete', '尚无可恢复的原付费回答，没有新增调用')
    checked = []
    try:
        for key in targets:
            call = pipeline._call_guard(db, p, row, key, pipeline._stage(row, key)['reserved_call_id'])
            original, receipt, state, descriptor = _preflight(pipeline, row, key)
            if call.status in {'failed', 'succeeded'}:
                saved = call.data.get('receipt')
                critical = ('call_id', 'provider', 'model', 'usage', 'request_id')
                if type(saved) is not dict or any(saved.get(field) != receipt.get(field) for field in critical):
                    raise ValueError('已结算回执与原 HTTP 回答不同')
            elif call.status != 'reserved':
                raise ValueError('原预留状态无法核对')
            checked.append((key, original, receipt, state, descriptor))
    except HTTPException:
        raise
    except (ValueError, OSError, UnicodeError, TypeError, KeyError):
        pipeline.fail('evidence_saved_response_invalid', '原回答、引用或磁盘片段无法核对；没有改写、结算或新调用', 422)
    accepted = row.version + 1
    for key, original, receipt, _, _ in checked:
        binding = pipeline.journal_binding(row, key, pipeline._stage(row, key)['reserved_call_id'])
        descriptor = bridge.write_missing_journal(journal, pipeline._stage_journal_root(row, key), binding, original, receipt)
        pipeline._replace_stage(row, key, {**pipeline._stage(row, key), 'journal': descriptor})
        pipeline._consume(db, p, row, key)
    if row.data['reduction']['preview'] is None:
        if remaining:
            row.status = 'failed'
            pipeline._set(row, {**row.data, 'phase': 'failed', 'error_code': 'evidence_remaining_unstarted',
                'error': '已免费恢复原付费结果；剩余资料尚未发送，须另行预览继续。'})
        else:
            pipeline._finish_maps(db, p, row)
    receipt = pipeline._recovery_receipt(row, p, command)
    row.version = accepted
    row.data = {**row.data, 'recovery_history': [*row.data['recovery_history'], receipt]}
    pipeline._state_size(row.data)
    pipeline.audit(db, p, 'setup.report_evidence.delegated_wire_recovered', row.id,
        {'stages': [item[0] for item in checked], 'remaining_count': len(remaining), 'new_call': False})
    db.commit()
    return {'draft': pipeline.draft_view(row, p), 'receipt': copy.deepcopy(receipt)}
