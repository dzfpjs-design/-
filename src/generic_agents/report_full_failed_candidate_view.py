"""Read-only preview of a failed extraction, never a correction or adoption."""
import json

from fastapi import HTTPException

from .contracts import digest
from .report_full_raw_view import raw_view


def _unavailable():
    raise HTTPException(409, {'code': 'full_candidate_preview_unavailable',
        'message': '此批次的原回答暂不能安全生成待核候选；原记录保留。'},
        headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


def failed_candidate_view(db, principal, identity, stage_key, revision):
    from . import report_full_reading_pipeline as full
    from .report_full_reading_review_candidate import CANDIDATE_ALGORITHM_VERSION, CandidateBudgetExceeded, build_candidate

    # This performs actor/workspace, revision, original checkpoint, paid ledger
    # and provider-wire checks in the caller's read-only snapshot.
    raw = raw_view(db, principal, identity, stage_key, revision)
    if (stage_key not in ('extract-1', 'extract-2') or raw['status'] != 'available'
            or raw['finish_reason'] != 'stop' or raw['response_body_status'] != 'complete'
            or raw['content_encoding'] != 'plain'):
        _unavailable()
    row = full.owned(db, principal, identity)
    stages = [s for s in row.data['extractions'] if s.get('batch_id') == stage_key]
    batches = [b for b in row.data['preview']['batches'] if b.get('batch_id') == stage_key]
    if (row.version != revision or row.status != 'failed' or row.data.get('error_code') != 'full_candidate_invalid'
            or len(stages) != 1 or len(batches) != 1 or stages[0].get('status') != 'failed'
            or stages[0].get('error_code') != 'full_candidate_invalid'
            or not full._complete_response(stages[0].get('raw_response'))):
        _unavailable()
    try:
        value = json.loads(raw['content'], parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        source_index = full._stage_sources(row.data['preview'], stage_key)
        context = batches[0]['payload']['context']
        built = build_candidate(value, source_index, context)
        result = {'schema': 'report-full-failed-candidate-preview-v1', 'scope': raw['scope'],
                  'candidate': built['candidate'], 'quarantine': built['quarantine'],
                  'algorithm_version': CANDIDATE_ALGORITHM_VERSION,
                  'status': 'needs_review', 'confirmed': False, 'can_apply': False, 'can_publish': False}
        # Reject non-serializable, invalid-Unicode or excessive computed output.
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(encoded) > 2 * 1024 * 1024:
            raise ValueError('oversize')
        result['view_hash'] = digest(result)
        return result
    except CandidateBudgetExceeded:
        raise HTTPException(409, {'code': 'full_candidate_preview_budget',
            'message': '来源过多或文字重复，暂无法安全生成预览；原回答和费用记录保留。可先查看原回答。'},
            headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        _unavailable()
