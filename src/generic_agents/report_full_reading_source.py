"""Exact private Word + saved OCR views; never a publication/source approval.

Only source/evidence/OCR tables are read. No conversion, recognition, provider,
Job dispatch or commit occurs here. Existing version validators remain the
authority; the compact snapshot deliberately excludes runtime result payloads.
"""
import copy
import hashlib
import json
import math
import re
from functools import wraps
from pathlib import PurePath
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select

from .contracts import Contract, digest
from .evidence import evidence_bytes
from .models import Job, SetupMaterial, MaterialOcrUnit as Unit
from .services import scoped
from . import document_copy, material_ocr as ocr
from .material_ocr_coverage import verify_coverage
from .native_report_reading import read_docx

SOURCE = 'report-full-source-view-v1'
OPTIONS = 'report-full-source-options-v1'
PROJECTION = 'report-full-reading-cloud-source-v1'
MODE = 'docx_and_saved_ocr_v1'
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
Hash = Annotated[str, Field(strict=True, pattern=r'^[a-f0-9]{64}$')]
Positive = Annotated[int, Field(strict=True, ge=1)]


def fail(code='full_source_integrity', message='完整来源的范围或版本无法核对；原记录保留', status=409):
    raise HTTPException(status, {'code': code, 'message': message})


def _safe(value):
    if isinstance(value, str):
        value.encode('utf-8')
        if '\x00' in value: raise ValueError('NUL')
    elif isinstance(value, list):
        for item in value: _safe(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str): raise ValueError('key')
            _safe(key); _safe(item)
    elif value is not None and type(value) not in (bool, int):
        # Coordinates/scores are reversible decimal strings in this protocol.
        raise ValueError('JSON type')
    return value


class Safe(Contract):
    @model_validator(mode='after')
    def safe_json(self):
        try: _safe(self.model_dump(mode='json'))
        except (ValueError, UnicodeError, RecursionError): raise ValueError('来源选择含不能保存的文字或数值')
        return self


class MaterialRef(Safe):
    id: UUID
    version: Positive
    hash: Hash


class PageSelection(Safe):
    page: Annotated[int, Field(strict=True, ge=1, le=500)]
    unit_id: UUID
    unit_version: Positive
    revision_id: UUID
    revision_hash: Hash
    revision_kind: Literal['raw', 'edited', 'confirmed']


class FullSourceSelection(Safe):
    source_mode: Literal['docx_and_saved_ocr_v1']
    original_ref: MaterialRef
    copy_ref: MaterialRef
    provenance_hash: Hash
    run_id: UUID
    generation: Positive
    run_version: Positive
    pages: list[PageSelection] = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def complete_order(self):
        if (self.original_ref.id == self.copy_ref.id
                or [page.page for page in self.pages] != list(range(1, len(self.pages) + 1))
                or len({page.unit_id for page in self.pages}) != len(self.pages)
                or len({page.revision_id for page in self.pages}) != len(self.pages)):
            raise ValueError('必须明确选择不同原件和副本，及从第一页起连续、不重复的全部页面')
        return self


def integrity(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try: return function(*args, **kwargs)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError,
                OverflowError, RecursionError): fail()
    return checked


def _keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys.split()): fail()


def _seal(value, key):
    return {**value, key: digest(value)}


def _ref(material):
    return {'id': material.id, 'version': material.version, 'hash': material.sha256}


def _material(db, p, ref, *, lock, suffix):
    row = scoped(db, SetupMaterial, str(ref.id), p, lock)
    if (row.archived or row.status not in {'parsed', 'needs_input'} or _ref(row) != ref.model_dump(mode='json')
            or PurePath(row.name).suffix.lower() != suffix):
        fail('full_source_changed', '所选原件或副本的版本、格式或状态已变化，请重新选择')
    return row


def _evidence(db, p, material):
    evidence, content = evidence_bytes(db, p, material.evidence_id)
    if (evidence.sha256 != material.sha256 or evidence.size_bytes != len(content)
            or hashlib.sha256(content).hexdigest() != material.sha256): fail()
    return evidence, content


def _provenance(db, p, word, pdf, expected=None):
    value = document_copy.checked_provenance(db, pdf)
    if not isinstance(value, dict): fail('full_source_copy_required', '请选择此原 Word 的可验证最终修订 PDF 副本')
    receipt = value.get('receipt')
    if (value.get('workspace_id') != p.workspace_id or value.get('source_material_id') != word.id
            or value.get('source_material_version') != word.version or value.get('source_sha256') != word.sha256
            or value.get('source_evidence_id') != word.evidence_id or value.get('source_name') != word.name
            or value.get('revision_policy') != 'accepted_final_v1'
            or value.get('engine_id') != 'libreoffice-docx-final-v1'
            or expected is not None and value.get('integrity_hash') != expected
            or not isinstance(receipt, dict) or receipt.get('source_sha256') != word.sha256
            or receipt.get('pdf_sha256') != pdf.sha256 or receipt.get('engine_id') != value['engine_id']
            or receipt.get('manifest_hash') != value.get('engine_manifest_hash')
            or receipt.get('revision_policy') != 'accepted_final_v1'
            or type(receipt.get('page_count')) is not int or not 1 <= receipt['page_count'] <= 500): fail()
    _safe(value)
    return value


def _run(db, p, pdf, provenance, run_id, *, lock, freeze_batch=False):
    if pdf.data.get('ocr_run_id') != run_id: fail('full_source_run_changed', '副本识别代次已变化，请重新选择；已保存来源不改写')
    if freeze_batch:
        run, recognition = ocr._checked_run_for_freeze(db, p, pdf, run_id, lock=lock)
    else:
        run = ocr.checked_run(db, p, pdf, run_id, lock=lock)
    coverage = verify_coverage(run.data['coverage'])
    count = provenance['receipt']['page_count']
    if (coverage.get('contract_version') != 2 or coverage.get('force_all_pages') is not True
            or coverage.get('profile') != {'kind': 'document_copy_full_pages_v1', 'provenance_hash': provenance['integrity_hash']}
            or coverage.get('source_sha256') != pdf.sha256 or coverage.get('unit_count') != count
            or coverage.get('gaps') or type(run.data['prepared']) is not int or run.data['prepared'] != count):
        fail('full_source_pages_incomplete', '副本尚未保存全部页面的识别结果；不发送残缺的完整报告视图')
    query = select(Unit).where(Unit.workspace_id == p.workspace_id, Unit.run_id == run.id).order_by(Unit.unit_index)
    if lock: query = query.with_for_update().execution_options(populate_existing=True)
    units = list(db.scalars(query))
    if len(units) != count or [unit.unit_index for unit in units] != list(range(1, count + 1)):
        fail('full_source_pages_incomplete', '全部页面的连续保存范围无法核对')
    return (run, units, recognition) if freeze_batch else (run, units)


def _region(region):
    return {'id': region['id'], 'text': region['text'], 'box': [str(x) for x in region['box']],
            'score': None if region.get('score') is None else str(region['score']),
            'source_region_id': region.get('source_region_id')}


def _page(unit, revision):
    return _seal({'page': unit.unit_index, 'unit_id': unit.id, 'unit_version': unit.version,
        'revision': {'id': revision.id, 'number': revision.number, 'kind': revision.kind, 'hash': revision.content_hash},
        'image': {'evidence_id': unit.evidence_id, 'sha256': unit.data['image_sha256'],
                  'width': unit.data['width'], 'height': unit.data['height']},
        'recognition_engine_hash': revision.data['engine_manifest_hash'],
        'regions': [_region(region) for region in revision.data['regions']]}, 'page_hash')


def _coverage(reading, pages):
    counts = {kind: sum(page['revision']['kind'] == kind for page in pages) for kind in ('raw', 'edited', 'confirmed')}
    return {'expected_pages': len(pages), 'prepared_pages': len(pages), 'selected_pages': len(pages),
        'versions_count': counts, 'word_blocks': len(reading['blocks']), 'ocr_regions': sum(len(page['regions']) for page in pages),
        'word_characters': sum(len(block['text']) for block in reading['blocks']),
        'ocr_characters': sum(len(region['text']) for page in pages for region in page['regions']),
        'word_gaps': copy.deepcopy(reading['coverage']['gaps']),
        'limitations': ['ocr_text_accuracy_unconfirmed', 'visual_and_table_semantics_unconfirmed',
                        'two_views_of_one_origin_not_independent_evidence'],
        'full_report_confirmed': False, 'can_apply': False, 'can_publish': False}


@integrity
def freeze_full_source(db, p, selection, lock=True):
    """Resolve *exact* refs once. Caller owns transaction; never selects latest."""
    selection = FullSourceSelection.model_validate(selection)
    if lock is not False:
        return _freeze_full_source(db, p, selection, lock=lock)
    from .report_full_source_read_scope import read_full_source
    return read_full_source(db, p, selection.model_dump(mode='json'), lock=lock,
        policy_factory=_full_source_read_policy,
        compute=lambda: _freeze_full_source(db, p, selection, lock=lock))


def _full_source_read_policy():
    """Actual validator identities; no live source/registry verdict is stored."""
    import sys
    from . import material_ocr_recognition as recognition
    from . import material_ocr_runtime as runtime
    from . import native_report_reading, material_ocr_coverage, typed_payload_codec_v131, evidence
    # A changed helper or parser invalidates a memo within this operation too.
    modules = (sys.modules[__name__], ocr, recognition, document_copy, native_report_reading,
        material_ocr_coverage, typed_payload_codec_v131, evidence)
    functions = tuple((module.__name__, tuple(sorted((name, id(value))
        for name, value in vars(module).items() if callable(value)))) for module in modules)
    recognition_methods = tuple(sorted((name, id(value))
        for name, value in vars(recognition._FreezeRecognition).items() if callable(value)))
    return (SOURCE, MODE, MAX_SNAPSHOT_BYTES, functions, recognition_methods,
        id(FullSourceSelection.__pydantic_validator__), runtime.recognition_profile_pairs())


def _freeze_full_source(db, p, selection, *, lock):
    # Same order as existing material→run→unit mutations; no runtime dispatch.
    refs = [(selection.original_ref, '.docx'), (selection.copy_ref, '.pdf')]
    materials = {str(ref.id): _material(db, p, ref, lock=lock, suffix=suffix)
                 for ref, suffix in sorted(refs, key=lambda pair: str(pair[0].id))}
    word, pdf = materials[str(selection.original_ref.id)], materials[str(selection.copy_ref.id)]
    evidence, content = _evidence(db, p, word)
    provenance = _provenance(db, p, word, pdf, selection.provenance_hash)
    run, units, recognition = _run(db, p, pdf, provenance, str(selection.run_id), lock=lock, freeze_batch=True)
    if (run.generation != selection.generation or run.version != selection.run_version
            or len(selection.pages) != len(units)): fail('full_source_changed', '识别代次、页面数量或版本已变化，请重新核对')
    pages = []
    checked_pages = ocr._freeze_units(db, p, pdf, run, units, recognition)
    for chosen, (unit, versions) in zip(selection.pages, checked_pages, strict=True):
        revision = next((row for row in versions if row.id == str(chosen.revision_id)), None)
        if (str(chosen.unit_id) != unit.id or chosen.unit_version != unit.version or chosen.page != unit.unit_index
                or revision is None or revision.content_hash != chosen.revision_hash or revision.kind != chosen.revision_kind):
            fail('full_source_revision_changed', '明确选择的页面文字版本无法核对，不会自动改选较新版本')
        pages.append(_page(unit, revision))
    reading = read_docx(content)
    snapshot = _seal({'schema': SOURCE, 'source_mode': MODE, 'workspace_id': p.workspace_id,
        'selection': selection.model_dump(mode='json'),
        'original': {'ref': _ref(word), 'name': word.name, 'evidence_id': word.evidence_id,
                     'evidence_sha256': evidence.sha256, 'evidence_size': evidence.size_bytes, 'reading': reading},
        'copy': {'ref': _ref(pdf), 'name': pdf.name, 'evidence_id': pdf.evidence_id, 'provenance': provenance,
                 'run': {'id': run.id, 'generation': run.generation, 'version': run.version, 'status': run.status,
                         'coverage': copy.deepcopy(run.data['coverage']),
                         'renderer': {'manifest_hash': run.data['engine']['manifest_hash'],
                                      'summary_hash': run.data['engine']['summary_hash']}}},
        'pages': pages, 'coverage': _coverage(reading, pages)}, 'snapshot_hash')
    return check_full_source(snapshot)


def _decimal(value):
    if not isinstance(value, str) or not re.fullmatch(r'-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?', value): fail()
    number = float(value)
    if not math.isfinite(number): fail()
    return number


@integrity
def check_full_source(snapshot):
    """Pure snapshot validation; fresh source/byte verification is freeze's job.

    It never upgrades an old raw selection to a newer revision/confirmation.
    A self-consistent hash is not an approval or a replacement for fresh DB checks.
    """
    _safe(snapshot)
    _keys(snapshot, 'schema source_mode workspace_id selection original copy pages coverage snapshot_hash')
    if (snapshot['schema'] != SOURCE or snapshot['source_mode'] != MODE
            or snapshot['snapshot_hash'] != digest({k:v for k,v in snapshot.items() if k != 'snapshot_hash'})): fail()
    if len(json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')).encode()) > MAX_SNAPSHOT_BYTES:
        fail('full_source_capacity', '完整来源超过私人记录容量，未截断或发送', 413)
    UUID(snapshot['workspace_id']); selection = FullSourceSelection.model_validate(snapshot['selection'])
    if selection.model_dump(mode='json') != snapshot['selection']: fail()
    original, copied = snapshot['original'], snapshot['copy']
    _keys(original, 'ref name evidence_id evidence_sha256 evidence_size reading')
    _keys(copied, 'ref name evidence_id provenance run')
    for material, ref, suffix in ((original, selection.original_ref, '.docx'), (copied, selection.copy_ref, '.pdf')):
        if material['ref'] != ref.model_dump(mode='json') or PurePath(material['name']).suffix.lower() != suffix: fail()
        UUID(material['evidence_id'])
    if original['evidence_sha256'] != selection.original_ref.hash or type(original['evidence_size']) is not int or original['evidence_size'] < 1: fail()
    reading = original['reading']
    _keys(reading, 'schema source_sha256 normalized_sha256 revision_policy hidden_content_policy blocks coverage warnings full_report_confirmed can_apply')
    if (reading['schema'] != 'report-body-draft-source-v1' or reading['source_sha256'] != selection.original_ref.hash
            or reading['revision_policy'] != 'accepted_final_v1' or reading['hidden_content_policy'] != 'reveal_retained_text_v1'
            or reading['can_apply'] is not False or reading['full_report_confirmed'] is not False): fail()
    if not re.fullmatch(r'[a-f0-9]{64}', reading['normalized_sha256']): fail()
    if not isinstance(reading['blocks'], list) or len(reading['blocks']) > 12000: fail()
    ids = set()
    for block in reading['blocks']:
        _keys(block, 'id text kind location')
        if (not re.fullmatch(r'p[1-9]\d*', block['id']) or block['id'] in ids or not block['text'].strip()
                or block['kind'] not in {'paragraph', 'table_paragraph', 'textbox_paragraph'}
                or block['location']['view'] != 'accepted_final_v1'
                or any(type(block['location'][key]) is not int or block['location'][key] < 1 for key in ('body_index', 'paragraph_index'))): fail()
        ids.add(block['id'])
    counts = reading['coverage']['counts']
    expected_counts = {'source_bytes': original['evidence_size'], 'paragraphs_read': len(reading['blocks']),
        'characters': sum(len(block['text']) for block in reading['blocks']),
        'utf8_text_bytes': sum(len(block['text'].encode()) for block in reading['blocks'])}
    if (reading['coverage']['scope'] != 'accepted_view_main_body_text' or reading['coverage']['status'] != 'body_text_only'
            or any(type(counts[key]) is not int or counts[key] != count for key, count in expected_counts.items())
            or expected_counts['characters'] > 2000000
            or any(type(counts[key]) is not int or counts[key] < 0 for key in ('paragraphs_total', 'paragraphs_empty'))
            or counts['paragraphs_total'] != counts['paragraphs_read'] + counts['paragraphs_empty']): fail()
    prov, run = copied['provenance'], copied['run']; receipt = prov['receipt']
    if (prov['integrity_hash'] != selection.provenance_hash or prov['integrity_hash'] != digest({k:v for k,v in prov.items() if k != 'integrity_hash'})
            or any(prov[key] != expected for key, expected in {
                'workspace_id': snapshot['workspace_id'], 'source_material_id': str(selection.original_ref.id),
                'source_material_version': selection.original_ref.version, 'source_sha256': selection.original_ref.hash,
                'source_name': original['name'], 'source_evidence_id': original['evidence_id'],
                'copy_material_id': str(selection.copy_ref.id), 'copy_name': copied['name'], 'copy_evidence_id': copied['evidence_id'],
                'pdf_sha256': selection.copy_ref.hash, 'revision_policy': 'accepted_final_v1', 'engine_id': 'libreoffice-docx-final-v1'}.items())
            or receipt['page_count'] != len(selection.pages) or type(receipt['page_count']) is not int
            or receipt['source_sha256'] != selection.original_ref.hash or receipt['pdf_sha256'] != selection.copy_ref.hash
            or receipt['revision_policy'] != 'accepted_final_v1' or receipt['engine_id'] != prov['engine_id']
            or receipt['manifest_hash'] != prov['engine_manifest_hash']): fail()
    _keys(run, 'id generation version status coverage renderer')
    _keys(run['renderer'], 'manifest_hash summary_hash')
    if (run['id'] != str(selection.run_id) or run['generation'] != selection.generation or run['version'] != selection.run_version
            or run['status'] not in ocr.STATES): fail()
    coverage = verify_coverage(run['coverage'])
    if (coverage['unit_count'] != len(selection.pages) or coverage['source_sha256'] != selection.copy_ref.hash
            or coverage['contract_version'] != 2 or coverage.get('force_all_pages') is not True or coverage['gaps']
            or coverage['profile'] != {'kind': 'document_copy_full_pages_v1', 'provenance_hash': selection.provenance_hash}
            or len(snapshot['pages']) != len(selection.pages)): fail()
    for chosen, page in zip(selection.pages, snapshot['pages']):
        _keys(page, 'page unit_id unit_version revision image recognition_engine_hash regions page_hash')
        _keys(page['revision'], 'id number kind hash'); _keys(page['image'], 'evidence_id sha256 width height')
        if (page['page_hash'] != digest({k:v for k,v in page.items() if k != 'page_hash'})
                or {'page': page['page'], 'unit_id': page['unit_id'], 'unit_version': page['unit_version'],
                    'revision_id': page['revision']['id'], 'revision_hash': page['revision']['hash'],
                    'revision_kind': page['revision']['kind']} != chosen.model_dump(mode='json')
                or type(page['revision']['number']) is not int or page['revision']['number'] < 1
                or any(type(page['image'][key]) is not int or page['image'][key] < 1 for key in ('width', 'height'))): fail()
        UUID(page['image']['evidence_id'])
        if any(not re.fullmatch(r'[a-f0-9]{64}', page[key]) for key in ('recognition_engine_hash',)): fail()
        if not re.fullmatch(r'[a-f0-9]{64}', page['image']['sha256']): fail()
        regions = page['regions']; seen = set()
        if not isinstance(regions, list) or len(regions) > 500 or sum(len(r['text']) for r in regions) > 20000: fail()
        for region in regions:
            _keys(region, 'id text box score source_region_id')
            if not isinstance(region['id'], str) or not region['id'] or region['id'] in seen or not isinstance(region['text'], str): fail()
            seen.add(region['id'])
            if not isinstance(region['box'], list) or len(region['box']) != 4: fail()
            x,y,w,h = map(_decimal, region['box'])
            if min(x,y) < 0 or min(w,h) <= 0 or x+w > page['image']['width']+.01 or y+h > page['image']['height']+.01: fail()
            if region['score'] is not None and not 0 <= _decimal(region['score']) <= 1: fail()
            if region['source_region_id'] is not None and (not isinstance(region['source_region_id'], str) or not region['source_region_id']): fail()
            if page['revision']['kind'] == 'raw' and region['source_region_id'] is not None: fail()
    if snapshot['coverage'] != _coverage(reading, snapshot['pages']): fail()
    return copy.deepcopy(snapshot)


@integrity
def project_full_source(snapshot):
    """Cloud gets every paragraph/page; local index retains exact region offsets.

    Matrix row = [source_ref, kind, location, text]. Word location is the
    accepted-view XML tuple; OCR location is [page]. No deduplication, scrubbing,
    normalization, top-k, separator insertion or semantic interpretation occurs.
    """
    snapshot = check_full_source(snapshot)
    original, copied = snapshot['original'], snapshot['copy']; blocks, index = [], {}
    for block in original['reading']['blocks']:
        ref = 'w:' + block['id']; position = block['location']
        location = [position['body_index'], position['paragraph_index'],
                    *[position.get(key, 0) for key in ('table_index', 'row_index', 'cell_index', 'table_depth')],
                    position.get('compatibility_branch', '')]
        blocks.append([ref, block['kind'], location, block['text']])
        index[ref] = {'text': block['text'], 'kind': block['kind'], 'material_id': original['ref']['id'],
                      'location': copy.deepcopy(position)}
    for page in snapshot['pages']:
        ref = 'o:p' + str(page['page']); text = ''.join(region['text'] for region in page['regions'])
        offset, regions = 0, []
        for ordinal, region in enumerate(page['regions'], 1):
            end = offset + len(region['text'])
            regions.append({**copy.deepcopy(region), 'ordinal': ordinal, 'start': offset, 'end': end}); offset = end
        blocks.append([ref, 'ocr_page', [page['page']], text])
        index[ref] = {'text': text, 'kind': 'ocr_page', 'material_id': copied['ref']['id'],
            'location': {'page': page['page'], 'run_id': copied['run']['id'], 'unit_id': page['unit_id'],
                         'revision_id': page['revision']['id'], 'revision_hash': page['revision']['hash'],
                         'revision_kind': page['revision']['kind'], 'image_sha256': page['image']['sha256']},
            'regions': regions}
    return {'schema': PROJECTION, 'snapshot_hash': snapshot['snapshot_hash'],
        'views': [{'prefix': 'w', 'kind': 'accepted_final_word_body', 'material_id': original['ref']['id']},
                  {'prefix': 'o', 'kind': 'saved_ocr_pages', 'material_id': copied['ref']['id']}],
        'blocks': blocks, 'coverage': copy.deepcopy(snapshot['coverage'])}, index


@integrity
def full_source_options(db, p, word_ref):
    """RR-read caller proposes last saved revisions once; no implicit authority.

    A proposal has exact refs. The caller must explicitly accept it, and send
    that selection unchanged to preview. No selector named `latest` is accepted.
    """
    word_ref = MaterialRef.model_validate(word_ref)
    word = _material(db, p, word_ref, lock=False, suffix='.docx'); _evidence(db, p, word)
    jobs = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id, Job.kind == document_copy.KIND,
        Job.data['provenance']['source_material_id'].as_string() == word.id,
        Job.data['provenance']['source_material_version'].as_integer() == word.version,
        Job.data['copy_status'].as_string() == 'succeeded').order_by(Job.created_at, Job.id).limit(51)))
    if not jobs: fail('full_source_copy_required', '尚无此原件版本的真实 PDF 副本，请先转换并保存全部页识别结果')
    if len(jobs) > 50: fail('full_source_capacity', '同源副本过多，请整理后重试；未截取候选', 413)
    copies, seen = [], set()
    for job in jobs:
        pdf = scoped(db, SetupMaterial, job.data['output_material_id'], p)
        if pdf.archived: continue
        if pdf.id in seen: fail()
        seen.add(pdf.id)
        _material(db, p, MaterialRef.model_validate(_ref(pdf)), lock=False, suffix='.pdf')
        provenance = _provenance(db, p, word, pdf)
        run_id = pdf.data.get('ocr_run_id')
        if not run_id: fail('full_source_pages_incomplete', '同源 PDF 尚无已保存的整份识别范围')
        run, units = _run(db, p, pdf, provenance, run_id, lock=False)
        pages, choices, counts = [], [], {'raw': 0, 'edited': 0, 'confirmed': 0}
        for unit in units:
            ocr.checked_unit(db, p, pdf, run, unit.id, image=True)
            versions = ocr.checked_versions(db, p, run, unit)
            if not versions: fail('full_source_pages_incomplete', '仍有页面没有识别结果；不能以已识别页代替完整报告')
            selected = versions[-1]; counts[selected.kind] += 1
            choices.append({'page': unit.unit_index, 'unit_id': unit.id, 'unit_version': unit.version,
                'revision_id': selected.id, 'revision_hash': selected.content_hash, 'revision_kind': selected.kind})
            pages.append({'page': unit.unit_index, 'unit_id': unit.id, 'unit_version': unit.version,
                'image_sha256': unit.data['image_sha256'],
                'versions': [{'revision_id': version.id, 'revision_hash': version.content_hash,
                              'revision_kind': version.kind, 'number': version.number} for version in versions],
                'selected_revision_id': selected.id})
        proposal = FullSourceSelection.model_validate({'source_mode': MODE, 'original_ref': _ref(word), 'copy_ref': _ref(pdf),
            'provenance_hash': provenance['integrity_hash'], 'run_id': run.id, 'generation': run.generation,
            'run_version': run.version, 'pages': choices}).model_dump(mode='json')
        copies.append({'copy_ref': _ref(pdf), 'copy_name': pdf.name, 'provenance_hash': provenance['integrity_hash'],
            'run_id': run.id, 'generation': run.generation, 'run_version': run.version, 'page_count': len(units),
            'prepared_pages': run.data['prepared'], 'versions_count': counts, 'pages': pages,
            'proposal': proposal, 'proposal_hash': digest(proposal)})
    if not copies: fail('full_source_copy_required', '当前原件的副本均已归档，请明确选择可用副本')
    return _seal({'schema': OPTIONS, 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
                  'original_ref': _ref(word), 'original_name': word.name,
                  'copies': copies, 'can_apply': False, 'can_publish': False}, 'options_hash')
