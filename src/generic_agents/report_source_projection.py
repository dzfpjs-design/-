"""Bind an original Word report to a genuine, fully confirmed final-view copy.

No API initialization, provider, conversion or OCR execution occurs here. The
handoff module is imported only while a helper is called, avoiding router cycles.
"""
import copy
from pathlib import PurePath
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import select

from .contracts import digest
from . import report_reading_drafts as rd
from .models import SetupMaterial, Job
from . import document_copy, material_ocr
from .material_ocr_coverage import verify_coverage

SCHEMA = 'report-source-projection-v1'
OPTIONS = 'report-source-projection-options-v1'
MODE = 'confirmed_final_view_v1'
BASIS_KEYS = ('sourceId', 'citation', 'citations', 'inferenceReason')


class ProjectionRef(rd.SafeContract):
    source_material_id: UUID
    copy_ref: rd.MaterialRef
    provenance_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)


def handoff_module():
    from . import report_configuration_handoff
    return report_configuration_handoff


def fail(code, message, **extra):
    handoff_module().fail(code, message, **extra)


def projection_value(body):
    values = body.model_dump(mode='json') if hasattr(body, 'model_dump') else body
    if (not isinstance(values, dict) or values.get('review_mode') != MODE
            or values.get('final_view_confirmed') is not True):
        fail('projection_consent_required', '请明确选择以已核对的最终修订副本作为本次来源')
    refs = values.get('projection_refs')
    if not isinstance(refs, list) or not 1 <= len(refs) <= 50:
        fail('projection_refs_invalid', '请选择实际存在的最终修订副本')
    refs = [ProjectionRef.model_validate(ref).model_dump(mode='json') for ref in refs]
    if (len({r['source_material_id'] for r in refs}) != len(refs)
            or len({r['copy_ref']['id'] for r in refs}) != len(refs)):
        fail('projection_refs_invalid', '每份原件只能选择一份独立副本，不能重复替代来源')
    return {'schema': SCHEMA, 'review_mode': MODE, 'final_view_confirmed': True, 'projection_refs': refs}


def validate_projection_command(record, command):
    """For ledger replay: absent/broken projection never falls back to old mode."""
    try:
        return record.get('projection') == projection_value(command)
    except (HTTPException, KeyError, TypeError, AttributeError, ValueError, RecursionError):
        return False


def selected(handoff, p, index):
    h = handoff_module(); h.checked_public(handoff)
    if handoff.get('schema') != h.HANDOFF or handoff.get('workspace_id') != p.workspace_id or type(index) is not int:
        fail('projection_scope_invalid', '来源交接不属于当前工作区')
    items = [s for s in handoff['selection'] if s['candidate_index'] == index]
    if len(items) != 1: fail('selection_invalid', '未找到此已交接业务')
    return items[0]


def origin_identity(origin, frozen, p, *, require_word=False):
    if (origin.workspace_id != p.workspace_id or origin.id != frozen['id']
            or origin.sha256 != frozen['hash'] or origin.evidence_id != frozen['evidence_id']
            or origin.name != frozen['name'] or origin.archived
            or require_word and PurePath(origin.name).suffix.lower() != '.docx'):
        fail('projection_original_changed', '原 Word 的归属、摘要、证据或名称已变化；保留草稿重新核对')


def copy_provenance(db, p, origin, material, expected_hash):
    try: provenance = document_copy.checked_provenance(db, material)
    except HTTPException:
        fail('projection_provenance_invalid', '副本与真实转换任务的双向来源记录不一致，不能作为正式来源')
    if not isinstance(provenance, dict):
        fail('projection_provenance_invalid', '此 PDF 没有可验证的系统转换来源，不能作为原 Word 的替代来源')
    receipt = provenance.get('receipt', {})
    if (material.workspace_id != p.workspace_id or provenance.get('workspace_id') != p.workspace_id
            or provenance.get('integrity_hash') != expected_hash
            or provenance.get('source_material_id') != origin.id
            or provenance.get('source_sha256') != origin.sha256
            or provenance.get('source_material_version') != origin.version
            or provenance.get('source_evidence_id') != origin.evidence_id
            or provenance.get('source_name') != origin.name
            or provenance.get('revision_policy') != 'accepted_final_v1'
            or provenance.get('engine_id') != 'libreoffice-docx-final-v1'
            or not isinstance(receipt, dict)
            or receipt.get('source_sha256') != origin.sha256 or receipt.get('pdf_sha256') != material.sha256
            or receipt.get('engine_id') != provenance.get('engine_id')
            or receipt.get('manifest_hash') != provenance.get('engine_manifest_hash')
            or receipt.get('revision_policy') != 'accepted_final_v1'
            or type(receipt.get('page_count')) is not int or receipt['page_count'] < 1):
        fail('projection_original_changed', '副本不是当前原 Word、版本和最终修订策略对应的真实转换结果')
    return provenance


def confirmed_copy(db, p, origin, material, provenance_hash, *, lock=False):
    h = handoff_module(); provenance = copy_provenance(db, p, origin, material, provenance_hash)
    try: cov = verify_coverage(material.data.get('ocr_coverage'))
    except (ValueError, TypeError, KeyError):
        fail('projection_coverage_pending', '副本完整页覆盖无法核对，请返回逐页核对')
    if (cov.get('contract_version') != 2 or cov.get('force_all_pages') is not True
            or cov.get('profile') != {'kind': 'document_copy_full_pages_v1', 'provenance_hash': provenance_hash}
            or cov.get('source_sha256') != material.sha256 or cov.get('gaps')
            or cov.get('unit_count') != provenance['receipt']['page_count']
            or not material.data.get('ocr_confirmation_id') or material.status != 'parsed' or material.data.get('ocr_pending')):
        fail('projection_coverage_pending', '副本尚未完成全部页面核对和整篇确认，请先保存并确认每个必要页面')
    # This is the existing full gate, including bytes and independently rederived coverage.
    h.current_materials(db, p, [{'id': material.id, 'version': material.version, 'hash': material.sha256}], ready=True, lock=lock)
    try: confirmation = material_ocr.checked_confirmation(db, p, material, material.data['ocr_confirmation_id'])
    except HTTPException:
        fail('projection_confirmation_invalid', '全部页确认记录与副本不一致，请核对原确认版本')
    snapshot = confirmation.data['snapshot']; units = snapshot['units']
    if (confirmation.id != material.data['ocr_confirmation_id'] or confirmation.material_id != material.id
            or confirmation.material_version != material.version or snapshot.get('coverage') != cov
            or confirmation.content_hash != digest(snapshot)
            or any(snapshot['scope'].get(key) != value for key, value in {
                'workspace_id': p.workspace_id, 'material_id': material.id, 'evidence_id': material.evidence_id,
                'source_sha256': material.sha256, 'name': material.name}.items())
            or len(units) != cov['unit_count'] or [u['index'] for u in units] != list(range(1, cov['unit_count'] + 1))
            or confirmation.data.get('blocks') != material.data.get('blocks')
            or confirmation.data.get('blocks_hash') != digest(material.data.get('blocks', []))):
        fail('projection_confirmation_invalid', '逐页确认数量、图像版本或正式段落与副本不一致')
    return provenance, confirmation


def original_snapshot(material):
    return {'role': 'origin', 'id': material.id, 'name': material.name, 'version': material.version,
            'hash': material.sha256, 'evidence_id': material.evidence_id}


def collect_chain(db, p, handoff, original_refs, projection, *, lock=False):
    h = handoff_module(); originals = {m['id']: m for m in handoff['materials']}
    refs = [rd.MaterialRef.model_validate(ref).model_dump(mode='json') for ref in original_refs]
    if (len({r['id'] for r in refs}) != len(refs) or {r['id'] for r in refs} != set(originals)
            or any(r['hash'] != originals[r['id']]['hash'] for r in refs)):
        fail('projection_original_changed', '原件清单必须完整对应交接时的 Word 摘要，不能换源')
    projected = {r['source_material_id']: r for r in projection['projection_refs']}
    if set(projected) - set(originals): fail('projection_refs_invalid', '副本关联了未授权原件')
    origin_rows = h.current_materials(db, p, refs, ready=False, lock=lock)
    copies = h.current_materials(db, p, [r['copy_ref'] for r in projection['projection_refs']], ready=False, lock=lock)
    formal = {}; snapshots = []
    for ref in refs:
        origin = origin_rows[ref['id']]; origin_identity(origin, originals[origin.id], p, require_word=origin.id in projected)
        if origin.id not in projected:
            h.current_materials(db, p, [ref], ready=True, lock=lock)
            formal[origin.id] = origin
            snapshots.append({**h.material_snapshot(origin), 'name': origin.name, 'role': 'unprojected'})
            continue
        requested = projected[origin.id]; material = copies[requested['copy_ref']['id']]
        provenance, confirmation = confirmed_copy(db, p, origin, material, requested['provenance_hash'], lock=lock)
        formal[origin.id] = material
        snapshots.extend([original_snapshot(origin), {**h.material_snapshot(material), 'name': material.name,
            'role': 'confirmed_view', 'source_material_id': origin.id,
            'provenance_hash': provenance['integrity_hash'], 'conversion_receipt_hash': digest(provenance['receipt']),
            'engine_manifest_hash': provenance['engine_manifest_hash'], 'confirmation_hash': confirmation.content_hash,
            'page_count': provenance['receipt']['page_count'], 'confirmed_pages': len(confirmation.data['snapshot']['units'])}])
    return formal, snapshots


def quote_mapping(material, source_id, source_block_id, quote, path, scope, *, source_location=None):
    if not isinstance(quote, str) or not quote.strip(): fail('projection_quote_missing', '引用文字为空，不能核对来源', path=path)
    matches = [b for b in material.data.get('blocks', []) if quote in b.get('text', '')]
    if len(matches) != 1:
        fail('projection_quote_ambiguous' if matches else 'projection_quote_missing',
            '引用在已确认副本中不唯一或未完整逐字匹配；跨区域文字须明确核对，不能自动拼接或选首个', path=path)
    block = matches[0]
    return {'scope': scope, 'path': path, 'source_material_id': source_id, 'source_block_id': source_block_id,
        'source_location': copy.deepcopy(source_location), 'formal_material_id': material.id,
        'target_block_id': block['id'], 'target_location': copy.deepcopy(block.get('location', {})), 'quote': quote}


def handoff_mappings(item, formal):
    return [quote_mapping(formal[ref['material_id']], ref['material_id'], ref['block_id'], ref['quote'],
        ref['path'], ref['scope'], source_location=ref['location']) for ref in item['source_refs']]


def flow_basis(flow):
    return {'business': {key: copy.deepcopy(flow.get(key)) for key in BASIS_KEYS},
        'steps': [{key: copy.deepcopy(step.get(key)) for key in ('id', *BASIS_KEYS)} for step in flow.get('steps', [])]}


def rewrite_basis(basis, formal, item):
    out = copy.deepcopy(basis); mappings = []
    if set(out) != {'business', 'steps'} or not isinstance(out['business'], dict) or not isinstance(out['steps'], list):
        fail('projection_integrity', '当前依据快照无法核对')
    direct = {m.id: m for m in formal.values()}
    for path, current in [('/business', out['business']), *[('/steps/' + str(i), s) for i, s in enumerate(out['steps'])]]:
        for index, cite in enumerate(current.get('citations') or []):
            material = formal.get(cite['material_id']) or direct.get(cite['material_id'])
            if material is None: fail('projection_reference_outside_scope', '当前引用不属于本次已核对来源', path=path)
            positions = [ref['location'] for ref in item['source_refs'] if
                ref['material_id'] == cite['material_id'] and ref['block_id'] == cite['block_id']]
            location = positions[0] if positions and all(p == positions[0] for p in positions) else None
            mapping = quote_mapping(material, cite['material_id'], cite['block_id'], cite['quote'],
                                    path + '/citations/' + str(index), 'current_flow', source_location=location)
            mappings.append(mapping); cite['material_id'] = material.id; cite['block_id'] = mapping['target_block_id']
        source_id = current.get('sourceId')
        if source_id in formal: current['sourceId'] = formal[source_id].id
        if current.get('citation') is not None:
            current['citation'] = '；'.join(c['block_id'] + '：' + c['quote'] for c in current.get('citations') or [])
    return out, mappings


def apply_basis(flow, basis):
    for target, values in [(flow, basis['business']), *zip(flow.get('steps', []), basis['steps'])]:
        for key in BASIS_KEYS:
            if key in target or values.get(key) is not None: target[key] = copy.deepcopy(values.get(key))


def projected_review_value(db, p, handoff, configuration, body, record_id):
    try: return _projected_review_value(db, p, handoff, configuration, body, record_id)
    except HTTPException: raise
    except (KeyError, TypeError, AttributeError, ValueError, IndexError, RecursionError):
        fail('projection_integrity', '最终修订来源或当前流程无法核对；原稿与原件保留')


def _projected_review_value(db, p, handoff, configuration, body, record_id):
    h = handoff_module(); h.configuration_cas(configuration, body)
    item = selected(handoff, p, body.candidate_index); projection = projection_value(body)
    matches = [(name, h.resolve_flow(configuration.data, name)) for name in h.names(configuration.data)
        if h.resolve_flow(configuration.data, name).get('reportSource', {}).get('handoff_id') == handoff['id']
        and h.resolve_flow(configuration.data, name).get('reportSource', {}).get('candidate_index') == body.candidate_index]
    if len(matches) != 1: fail('source_marker_invalid', '当前业务已删除或来源归属不唯一')
    name, flow = matches[0]
    if h.fact_hash(name, flow) != body.fact_hash: fail('source_fact_changed', '流程或职责已变化，请重新核对')
    original_refs = [ref.model_dump(mode='json') for ref in body.material_refs]
    formal, snapshots = collect_chain(db, p, handoff, original_refs, projection, lock=True)
    mappings = handoff_mappings(item, formal); before_basis = flow_basis(flow)
    after_basis, current_mappings = rewrite_basis(before_basis, formal, item); mappings.extend(current_mappings)
    proposed = copy.deepcopy(configuration.data); updated = h.resolve_flow(proposed, name); apply_basis(updated, after_basis)
    updated['reportSource'] = {'handoff_id': handoff['id'], 'candidate_index': body.candidate_index, 'source_review_id': record_id}
    updated['confirmedAt'] = None; updated['confirmedSignature'] = None
    for material in formal.values():
        existing = next((m for m in proposed.setdefault('materials', []) if m.get('id') == material.id), None)
        values = {'id': material.id, 'name': material.name, 'version': material.version, 'hash': material.sha256,
                  'evidence_id': material.evidence_id, 'archived': False, 'status': material.status}
        if existing is None: proposed['materials'].append(values)
        else: existing.update(values)
    h.setup().validate_configuration(proposed)
    record = h.public_record({'schema': h.SOURCE, 'id': record_id, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'report_id': handoff['report_id'], 'configuration_id': configuration.id, 'handoff_id': handoff['id'],
        'source_hash': handoff['source_hash'], 'candidate_index': body.candidate_index, 'request_id': str(body.request_id),
        'command_hash': h.command_hash(p, 'source_review', handoff['id'], body), 'fact_hash': body.fact_hash,
        'basis_hash': h.basis_hash(updated), 'material_refs': original_refs, 'materials': snapshots,
        'projection': projection, 'mapping_basis': before_basis, 'mappings': mappings,
        'accepted_revision': configuration.version + 1, 'configuration_hash': digest(proposed)})
    receipt = h.make_receipt(p, 'source_review', record, None, handoff['id'])
    h.ledger_data(record, body, receipt)
    return record, proposed, receipt


def verify_projection(db, p, proof, handoff, name=None, flow=None):
    """Cold check; False means unavailable, never permission to fall back to Word."""
    try:
        h = handoff_module(); h.checked_public(proof)
        item = selected(handoff, p, proof['candidate_index'])
        value = proof['projection']
        normalized = projection_value(value)
        if (value != normalized or proof['schema'] != h.SOURCE or proof['workspace_id'] != p.workspace_id
                or proof['handoff_id'] != handoff['id'] or proof['report_id'] != handoff['report_id']
                or proof['configuration_id'] != handoff['configuration_id'] or proof['source_hash'] != handoff['source_hash']):
            return False
        formal, snapshots = collect_chain(db, p, handoff, proof['material_refs'], normalized)
        after_basis, current = rewrite_basis(proof['mapping_basis'], formal, item)
        if (proof['materials'] != snapshots or proof['mappings'] != [*handoff_mappings(item, formal), *current]
                or proof['basis_hash'] != digest(after_basis)):
            return False
        if name is not None or flow is not None:
            if name is None or flow is None or h.fact_hash(name, flow) != proof['fact_hash'] or h.basis_hash(flow) != proof['basis_hash']:
                return False
            if flow.get('reportSource') != {'handoff_id': handoff['id'], 'candidate_index': proof['candidate_index'], 'source_review_id': proof['id']}:
                return False
        return True
    except (HTTPException, KeyError, TypeError, AttributeError, ValueError, IndexError, RecursionError):
        return False


def projection_options(db, p, handoff, candidate_index):
    item = selected(handoff, p, candidate_index)
    rows = list(db.scalars(select(SetupMaterial).where(SetupMaterial.workspace_id == p.workspace_id)))
    materials = {m.id: m for m in rows}
    originals = {m['id']: m for m in handoff['materials'] if PurePath(m['name']).suffix.lower() == '.docx'}
    jobs = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id, Job.kind == document_copy.KIND)))
    candidates = set()
    for material in rows:
        value = material.data.get('document_copy')
        if isinstance(value, dict) and value.get('source_material_id') in originals:
            candidates.add((value['source_material_id'], material.id))
    # A stripped provenance flag must remain explainable when its real Job exists.
    for job in jobs:
        if job.data.get('source_material_id') in originals and job.data.get('output_material_id') in materials:
            candidates.add((job.data['source_material_id'], job.data['output_material_id']))
    options = []
    for source_id, copy_id in sorted(candidates):
        origin = materials.get(source_id); material = materials[copy_id]; frozen = originals[source_id]
        raw_prov = material.data.get('document_copy'); provenance_hash = raw_prov.get('integrity_hash') if isinstance(raw_prov, dict) else None
        if not isinstance(provenance_hash, str) or len(provenance_hash) != 64 or any(c not in 'abcdef0123456789' for c in provenance_hash):
            provenance_hash = None
        option = {'source_material_id': source_id,
            'source_ref': {'id': source_id, 'version': origin.version if origin else frozen['version'], 'hash': origin.sha256 if origin else frozen['hash']},
            'source_name': origin.name if origin else frozen['name'],
            'copy_ref': {'id': material.id, 'version': material.version, 'hash': material.sha256}, 'copy_name': material.name,
            'provenance_hash': provenance_hash, 'ready': False, 'reason_code': None, 'reason': None,
            'page_count': None, 'confirmed_pages': 0}
        try:
            if origin is None: fail('projection_original_changed', '原 Word 已缺失，不能继续来源复核')
            h = handoff_module(); origin_identity(origin, frozen, p, require_word=True)
            h.current_materials(db, p, [option['source_ref']], ready=False)
            provenance = copy_provenance(db, p, origin, material, provenance_hash)
            option['page_count'] = provenance['receipt']['page_count']
            try:
                summary = material_ocr.material_summary(db, material)
                count = summary.get('confirmed', 0) if isinstance(summary, dict) else 0
                if type(count) is int and 0 <= count <= option['page_count']: option['confirmed_pages'] = count
            except (HTTPException, KeyError, TypeError, AttributeError, ValueError): pass
            confirmed_copy(db, p, origin, material, provenance_hash)
            relevant = {**item, 'source_refs': [r for r in item['source_refs'] if r['material_id'] == source_id]}
            handoff_mappings(relevant, {source_id: material})
            option.update(ready=True, reason_code=None, reason=None, confirmed_pages=option['page_count'])
        except HTTPException as error:
            detail = error.detail if isinstance(error.detail, dict) else {}
            option.update(reason_code=detail.get('code', 'projection_unavailable'),
                          reason=detail.get('message', '副本来源或全部页确认尚未通过，请返回原件与核页继续处理'))
        except (KeyError, TypeError, AttributeError, ValueError, RecursionError):
            option.update(reason_code='projection_integrity', reason='副本记录无法完整核对；原件和人工稿保留')
        options.append(option)
    value = {'schema': OPTIONS, 'workspace_id': p.workspace_id, 'handoff_id': handoff['id'],
             'candidate_index': candidate_index, 'options': options}
    return {**value, 'options_hash': digest(value)}
