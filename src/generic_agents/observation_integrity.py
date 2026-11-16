"""Check saved reported measurements against their original extraction receipt.

This establishes record integrity, never sensor calibration or physical truth.
"""
import math
from datetime import datetime
from fastapi import HTTPException
from .contracts import digest
from .models import Capability, Invocation
from .services import scoped
from .evidence import evidence_bytes


def checked_report(db, p, row, *, allow_candidates=False):
    from .simulation_observation_origin_v177 import guard as simulation_guard
    simulation_guard(db,p,row)
    if row.status != 'processed' or (row.data.get('candidate_only') and not allow_candidates):
        raise ValueError('未完成的处理或模型候选不能作为登记状态')
    if row.data.get('mqtt_receipt_id'):
        from .mqtt_observations import guard
        guard(db,p,row,execution=False,result=row.data.get('extracted'))
    from .observation_candidate_dispatch import verify_candidate
    verify_candidate(db,p,row,row.data['extractor_id'],execution=False)
    source = scoped(db, Capability, row.source_id, p)
    if source.kind != 'source':
        raise ValueError('原登记来源不是感知来源')
    if source.content_hash != digest(source.data):
        raise ValueError('原来源版本摘要不一致')
    original = row.data.get('original_request')
    if original is not None:
        if digest(original) != row.data.get('input_hash'):
            raise ValueError('原登记摘要不一致')
        from .observation_time_anchor_v135 import same_observation_instant
        if 'observed_at' not in original:raise ValueError('原登记时间字段缺失')
        same_observation_instant(original['observed_at'],row.observed_at)
        for key in ('source_id', 'source_record_id', 'processing_version', 'entity_id', 'business_day'):
            if original[key] != getattr(row, key):
                raise ValueError('原来源、编号、日期或对象不一致')
        if original['attributes'] != row.data.get('attributes') or original.get('evidence_ids', []) != row.data.get('evidence_ids', []):
            raise ValueError('原输入属性或证据不一致')
        if original['extractor_id'] != row.data.get('extractor_id'):
            raise ValueError('原提取器不一致')
    extracted = row.data['extracted']
    values = extracted['attributes']
    if not isinstance(values, dict):
        raise ValueError('提取字段不是对象')
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('提取值不是有限数值')
        if isinstance(value, dict):
            for child in value.values(): finite(child)
        if isinstance(value, list):
            for child in value: finite(child)
    finite(values)
    call = scoped(db, Invocation, row.data['invocation_id'], p)
    extractor = scoped(db, Capability, row.data['extractor_id'], p)
    if extractor.content_hash != digest(extractor.data) or call.data.get('capability_hash') != extractor.content_hash:
        raise ValueError('原提取器版本摘要不一致')
    inputs = {'attributes': row.data.get('attributes', {})}
    if row.data.get('evidence_ids'): inputs['evidence_ids'] = row.data['evidence_ids']
    if call.status != 'succeeded' or call.capability_id != row.data['extractor_id'] or call.data.get('input_hash') != digest(inputs) or call.data.get('output') != extracted:
        raise ValueError('提取调用、原输入或输出不一致')
    hashes = []
    for identity in row.data.get('evidence_ids', []):
        ev, _ = evidence_bytes(db, p, identity)
        hashes.append(ev.sha256)
    review = row.data.get('human_verification', {})
    review_hash = digest({'extracted': extracted, 'evidence_ids': row.data.get('evidence_ids', []), 'invocation_id': row.data['invocation_id']})
    decision = review.get('decision') if review.get('input_hash') == review_hash else None
    return {'attributes': values, 'extracted': extracted, 'source_name': source.data.get('description') or source.name,
            'invocation_id': call.id, 'file_hashes': hashes, 'review': decision,
            'basis': 'original_inputs_and_extractor_receipt' if original is not None else 'legacy_extractor_receipt_clock_not_attested',
            'source_time_verified': False, 'physical_state_verified': False}


CHECK_ERRORS = (HTTPException, KeyError, TypeError, ValueError, OSError, RecursionError)
