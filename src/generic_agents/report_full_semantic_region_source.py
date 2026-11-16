"""Authority for explicitly selected final copies; never legacy business scope.

The reading snapshot remains immutable. A current PDF confirmation may have a
new material/revision version; it is selected explicitly rather than promoted
from the old reading. Private source checks are not formal mapping consent.
"""
import copy
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select

from . import material_ocr as ocr
from . import report_full_semantic_source_review as review
from . import report_full_semantic_handoff as full
from . import report_source_projection as projection
from . import report_source_region_binding as region
from .contracts import digest
from .models import SetupMaterial, MaterialOcrUnit as Unit
from .report_reading_drafts import SafeContract
from .report_full_reading_source import project_full_source

router = APIRouter()
SCHEMA = 'report-full-semantic-region-source-selection-v1'
PAGE_FIELDS = ('page', 'unit_id', 'unit_version', 'revision_id', 'revision_hash', 'revision_kind', 'image_sha256')


def fail(code, message, status=409):
    review.fail(code, message, status)


class PageRef(SafeContract):
    page: int = Field(strict=True, ge=1, le=500)
    unit_id: UUID
    unit_version: int = Field(strict=True, ge=1)
    revision_id: UUID
    revision_hash: str = Field(strict=True, pattern=full.HASH)
    revision_kind: Literal['raw', 'edited', 'confirmed']
    image_sha256: str = Field(strict=True, pattern=full.HASH)


class Selection(SafeContract):
    schema: Literal['report-full-semantic-region-source-selection-v1']
    anchor: region.CopyAnchor
    copy_ref: region.MaterialRef
    pages: list[PageRef] = Field(min_length=1, max_length=500)
    selection_hash: str = Field(strict=True, pattern=full.HASH)

    @model_validator(mode='after')
    def exact(self):
        value = self.model_dump(mode='json')
        if (digest({k: v for k, v in value.items() if k != 'selection_hash'}) != self.selection_hash
                or self.copy_ref.id != self.anchor.copy_material_id
                or self.copy_ref.hash != self.anchor.copy_sha256
                or [page.page for page in self.pages] != list(range(1, len(self.pages) + 1))
                or len({page.unit_id for page in self.pages}) != len(self.pages)):
            raise ValueError('副本、连续页清单或明确选择的摘要不一致')
        return self


def page_ref(unit, version):
    return {'page': unit.unit_index, 'unit_id': unit.id, 'unit_version': unit.version,
        'revision_id': version.id, 'revision_hash': version.content_hash,
        'revision_kind': version.kind, 'image_sha256': unit.data['image_sha256']}


def selected_pages(db, p, material, run, pages=None, *, lock=False, current=True):
    """Cold-check stored OCR versions; no implicit reading from a newer version."""
    if current and run.data['prepared'] != run.data['coverage']['unit_count']:
        fail('region_source_preparation_pending', '请先完成副本全部页面的识别准备；不能只扫描部分页就报告全部匹配位置')
    units = list(db.scalars(select(Unit).where(Unit.workspace_id == p.workspace_id,
        Unit.run_id == run.id, Unit.material_id == material.id).order_by(Unit.unit_index)))
    if (len(units) != run.data['prepared']
            or [u.unit_index for u in units] != list(range(1, len(units) + 1))):
        fail('region_source_pages_changed', '已准备的页面范围不完整，原定位稿保留')
    if pages is not None and current and len(pages) != len(units):
        fail('region_source_pages_changed', '准备页数已变化，请明确重新选择当前副本，原定位稿保留')
    requested = {page['unit_id']: page for page in pages} if pages is not None else None
    result, refs = [], []
    for unit in units:
        if requested is not None and unit.id not in requested:
            continue
        unit = ocr.checked_unit(db, p, material, run, unit.id, lock=lock, image=True)
        versions = ocr.checked_versions(db, p, run, unit)
        if requested is None:
            identity = unit.data.get('confirmed_id') or unit.data.get('draft_id') or unit.data.get('raw_id')
            version = next((v for v in versions if v.id == identity), None)
            if version is None:
                fail('region_source_page_pending', '页面尚未形成可保存的文字版本，请先完成识别与核对')
        else:
            wanted = requested[unit.id]
            version = next((v for v in versions if v.id == wanted['revision_id']), None)
            if (version is None or version.content_hash != wanted['revision_hash'] or version.kind != wanted['revision_kind']
                    or wanted['page'] != unit.unit_index or wanted['image_sha256'] != unit.data['image_sha256']
                    or current and unit.version != wanted['unit_version']):
                fail('region_source_revision_changed', '明确选择的页图或文字版本已变化，原定位稿保留')
        refs.append(page_ref(unit, version))
        result.append({'page': unit.unit_index, 'unit_id': unit.id, 'revision_id': version.id,
            'revision_hash': version.content_hash, 'revision_kind': version.kind,
            'image_sha256': unit.data['image_sha256'], 'regions': [
                {'id': item['id'], 'text': item['text'], 'box': region._box(item)}
                for item in version.data['regions']]})
    if requested is not None and len(result) != len(requested):
        fail('region_source_page_pending', '原选择的页面已不可用，原定位稿保留')
    return result, refs


def source_chain(db, p, snapshot, selection, *, lock=False, current=True, formal=False):
    """Real material bytes, bidirectional conversion and OCR lineage authority."""
    selection = Selection.model_validate(selection).model_dump(mode='json')
    anchor = selection['anchor']; frozen = snapshot['original']
    if anchor['source_ref'] != frozen['ref'] or anchor['source_evidence_id'] != frozen['evidence_id'] or anchor['source_name'] != frozen['name']:
        fail('region_source_outside_scope', '副本必须属于本次完整设计冻结的同一原件、版本和证据')
    if current:
        origin, material, run = region._chain(db, p, anchor, lock=lock)
        if region._ref(material) != selection['copy_ref']:
            fail('region_source_copy_changed', '副本已有新版本，请明确选择，不自动沿用旧位置')
    else:
        # Historical receipt readers may read an old immutable OCR revision.
        # Current-source trust is established only by the strict branch above.
        origin = db.get(SetupMaterial, anchor['source_ref']['id'])
        material = db.get(SetupMaterial, anchor['copy_material_id'])
        if origin is None or material is None or origin.workspace_id != p.workspace_id or material.workspace_id != p.workspace_id:
            fail('region_source_unavailable', '原定位范围的资料已不可用')
        projection.copy_provenance(db, p, origin, material, anchor['provenance_hash'])
        run = ocr.checked_run(db, p, material, anchor['run_id'], lock=lock)
        if run.generation != anchor['generation'] or material.sha256 != anchor['copy_sha256'] or material.evidence_id != anchor['copy_evidence_id']:
            fail('region_source_unavailable', '原定位范围的副本或识别代次无法核对')
    pages, _ = selected_pages(db, p, material, run, selection['pages'], lock=lock, current=current)
    confirmation = None
    if formal:
        if not current:
            raise ValueError('formal mapping must use an explicitly current selection')
        _, confirmation = projection.confirmed_copy(db, p, origin, material, anchor['provenance_hash'], lock=lock)
        for page in selection['pages']:
            unit = ocr.checked_unit(db, p, material, run, page['unit_id'], lock=lock)
            if (page['revision_kind'] != 'confirmed' or unit.status != 'confirmed'
                    or unit.data.get('confirmed_id') != page['revision_id']):
                fail('region_source_confirmation_pending', '每条引用必须明确定位到当前人工确认版本，旧识别位置不自动升格')
        if len(pages) != run.data['coverage']['unit_count']:
            fail('region_source_confirmation_pending', '引用来源还未完整覆盖全部页面')
    return origin, material, run, pages, confirmation


def source_options(db, p, identity):
    ctx = review.context(db, p, identity); configuration, record, heads, manifest, _, snapshot = ctx
    frozen = snapshot['original']; rows = list(db.scalars(select(SetupMaterial).where(
        SetupMaterial.workspace_id == p.workspace_id, SetupMaterial.archived.is_(False),
        SetupMaterial.data['document_copy']['source_material_id'].as_string() == frozen['ref']['id']
    ).order_by(SetupMaterial.id).limit(51)))
    if len(rows) > 50:
        fail('region_source_capacity', '同源副本超过完整核对上限；原稿保留', 413)
    options = []
    for material in rows:
        reason, selected, whole, confirmed = None, None, False, 0
        try:
            origin = db.get(SetupMaterial, frozen['ref']['id'])
            if origin is None or region._ref(origin) != frozen['ref']:
                fail('region_source_original_changed', '原件版本已变化，不能替换冻结读取的来源')
            projection.origin_identity(origin, {**frozen['ref'], 'name': frozen['name'], 'evidence_id': frozen['evidence_id']}, p, require_word=True)
            provenance = projection.copy_provenance(db, p, origin, material, material.data['document_copy']['integrity_hash'])
            run = ocr.checked_run(db, p, material, material.data.get('ocr_run_id'))
            anchor = region._copy_anchor(origin, material, provenance, run)
            region._chain(db, p, anchor)
            pages, refs = selected_pages(db, p, material, run)
            value = {'schema': SCHEMA, 'anchor': anchor, 'copy_ref': region._ref(material), 'pages': refs}
            selected = {**value, 'selection_hash': digest(value)} if refs else None
            confirmed = sum(page['revision_kind'] == 'confirmed' for page in refs)
            try:
                if selected:
                    source_chain(db, p, snapshot, selected, formal=True)
                    whole = True
            except HTTPException as error:
                reason = error.detail['message'] if isinstance(error.detail, dict) else str(error.detail)
        except HTTPException as error:
            reason = error.detail['message'] if isinstance(error.detail, dict) else str(error.detail)
        options.append({'copy_ref': region._ref(material), 'name': material.name,
            'selection': selected, 'confirmed_pages': confirmed, 'whole_document_confirmed': whole,
            'reason': reason, 'page_count': material.data.get('document_copy', {}).get('receipt', {}).get('page_count')})
    value = {'schema': 'report-full-semantic-region-source-options-v1', 'handoff_id': str(identity),
        'active': str(identity) in heads, 'manifest_hash': manifest['manifest_hash'],
        'configuration': {'revision': configuration.version, 'hash': digest(configuration.data)},
        'copies': options, 'can_confirm': False, 'can_publish': False}
    return {**value, 'options_hash': digest(value)}


@router.get('/full-semantic-region-mappings/{identity}/source-options')
def options_route(identity: UUID, p: review.pipeline.ReadP, db: review.pipeline.DB):
    return source_options(db, p, identity)
