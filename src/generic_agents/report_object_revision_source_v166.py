"""Original owner report source for independent new human structure values.

No provider execution, repair of old answers, or creation of a missing model
candidate. A source failure must leave already saved independent work readable.
"""
import copy

from fastapi import HTTPException
from sqlalchemy import select

from .contracts import digest
from .models import SetupReportDraft
from . import report_evidence_pipeline as pipeline
from .report_evidence_manual_v5 import manual_from_projection, MANUAL_VALUE
from .report_evidence_manual_v6_adapter import manual_from_projection_v6
from .report_evidence_reduction_wire_v6 import CANDIDATE as V6_CANDIDATE
from .report_object_semantics_v166 import new_from_value, validate_value


def load_source(db, p, report_id, lock=False):
    from .report_saved_map_review_source_v178 import load_selected_source
    selected = load_selected_source(db, p, report_id, lock=lock)
    if selected is not None:
        return selected
    query = select(SetupReportDraft).where(SetupReportDraft.id == str(report_id),
        SetupReportDraft.workspace_id == p.workspace_id, SetupReportDraft.actor_id == p.user_id)
    if lock:
        query = query.with_for_update(read=True).execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, '未找到本人在当前工作区的原报告稿')
    pipeline.check_row(row, p)
    if row.status not in {'review', 'needs_clarification'}:
        raise HTTPException(409, '原报告尚未形成可核对稿；新结构版本不会修复或重跑原模型调用')
    preview = row.data['preview']
    catalog = pipeline._catalog(preview)
    model_hash = row.data.get('model_result_hash')
    if not isinstance(model_hash, str) or len(model_hash) != 64:
        raise HTTPException(409, '原模型尚未形成完整候选；原报告和调用记录保留，新结构稿暂不可用，未自动重跑')
    from .report_full_semantic_handoff import material_refs
    material_refs(db, p, row, lock=lock)
    generation = preview.get('map_preparation', {}).get('generation_contract', {}).get('schema')
    new_family = generation in {'report-claim-array-map-generation-v168', 'report-claim-enum-map-generation-v172'}
    context = copy.deepcopy(preview['map_preparation']['context'])
    allowed = list(catalog['index'])
    base = row.data.get('review_value')
    if new_family and base is not None:
        raise HTTPException(409, '新报告不能借用旧人工稿合同，已有内容保持不变')
    if base is not None:
        if base.get('schema') != MANUAL_VALUE:
            raise HTTPException(409, '原人工稿合同不支持无损另存，已有内容保持不变')
        base = copy.deepcopy(base)
    else:
        original = pipeline._reduction_result(row, catalog)
        if not isinstance(original, dict) or digest(original['result']) != model_hash:
            raise HTTPException(409, '原完整候选与已保存模型摘要不一致')
        projection = original['result']
        try:
            if new_family:
                manual_value_from_candidate = pipeline._family(preview).manual_value_from_candidate
                base = manual_value_from_candidate(projection, catalog, allowed, context)
            else:
                base = (manual_from_projection_v6(projection) if projection.get('schema') == V6_CANDIDATE
                    else manual_from_projection(projection))
        except ValueError:
            raise HTTPException(409, '原完整候选不能无损转换；原回答和已存新稿保留') from None
    try:
        value = copy.deepcopy(base) if new_family else new_from_value(base)
        validate_value(value, catalog, allowed, context)
    except ValueError:
        raise HTTPException(409, '原人工稿或片段当前不能建立新结构基；已存新稿保留') from None
    basis = {'report_id': row.id, 'report_revision': row.version,
        'model_result_hash': model_hash, 'source_hash': preview['source_hash'],
        'catalog_hash': catalog['descriptor']['catalog_hash'],
        'context_hash': digest(context), 'base_value_hash': digest(base)}
    return {'basis': basis, 'basis_hash': digest(basis), 'value': value,
        'catalog': catalog, 'allowed_span_refs': allowed, 'context': context}
