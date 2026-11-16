"""Explicit, frozen multi-region source proofs; the legacy projection is untouched.

Draft persistence is private. Only a separately consented, fully confirmed review
may issue the shared proof consumed by the compiler and immutable task snapshots.
No conversion, OCR, model or provider work is performed in this module.
"""
import copy
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field

from . import report_reading_drafts as rd
from .contracts import digest
from . import report_source_projection as legacy

SCHEMA = 'report-source-projection-v2'
MODE = 'confirmed_final_view_regions_v2'


class BindingDraftRef(rd.SafeContract):
    id: UUID
    revision: int = Field(ge=1, strict=True)
    value_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)
    draft_hash: str = Field(pattern=r'^[a-f0-9]{64}$', strict=True)


def binding_module():
    from . import report_source_region_binding
    return report_source_region_binding


def fail(code, message, **extra):
    legacy.fail(code, message, **extra)


def projection_value(body):
    value = body.model_dump(mode='json') if hasattr(body, 'model_dump') else body
    if (not isinstance(value, dict) or value.get('review_mode') != MODE
            or value.get('final_view_confirmed') is not True):
        fail('region_consent_required', '请明确核对完整引文、全部区域及最终确认版本')
    # Reuse the v1 ref checks without extending its stored shape or command dump.
    refs = legacy.projection_value({'review_mode': legacy.MODE, 'final_view_confirmed': True,
                                    'projection_refs': value.get('projection_refs')})['projection_refs']
    b = binding_module()
    bindings = value.get('region_bindings')
    if not isinstance(bindings, list) or not 1 <= len(bindings) <= 1000:
        fail('region_bindings_invalid', '请先保存并逐项核对本业务的全部引用位置')
    bindings = [b.RegionBinding.model_validate(item).model_dump(mode='json') for item in bindings]
    return {'schema': SCHEMA, 'review_mode': MODE, 'final_view_confirmed': True,
            'projection_refs': refs,
            'quote_binding_draft_ref': BindingDraftRef.model_validate(value.get('quote_binding_draft_ref')).model_dump(mode='json'),
            'region_bindings': bindings}


def validate_projection_command(record, command):
    """Only an explicit v2 command may explain a v2 ledger entry."""
    try:
        projection = projection_value(command)
        context = record['binding_context']
        anchor = binding_module().DraftAnchor.model_validate(context['anchor']).model_dump(mode='json')
        return (record.get('projection') == projection and set(context) == {
            'anchor', 'configuration_revision', 'configuration_hash', 'fact_hash', 'mapping_basis_hash', 'copy_refs'}
            and context['anchor'] == anchor and anchor['handoff_id'] == record['handoff_id']
            and anchor['candidate_index'] == record['candidate_index']
            and context['configuration_revision'] == command.configuration_revision
            and context['configuration_hash'] == command.configuration_hash
            and context['fact_hash'] == command.fact_hash
            and context['mapping_basis_hash'] == digest(record['mapping_basis'])
            and context['copy_refs'] == [ref['copy_ref'] for ref in projection['projection_refs']]
            and _draft_snapshot_valid(record, projection, context))
    except (HTTPException, KeyError, TypeError, AttributeError, ValueError, RecursionError):
        return False


def _draft_snapshot_valid(record, projection, context):
    """Recompute the chosen private version's two public digests, without reread."""
    b = binding_module(); ref = projection['quote_binding_draft_ref']
    value = {key: copy.deepcopy(context[key]) for key in (
        'configuration_revision', 'configuration_hash', 'fact_hash', 'mapping_basis_hash', 'copy_refs')}
    value['bindings'] = copy.deepcopy(projection['region_bindings'])
    normalized = b.BindingDraftValue.model_validate(value).model_dump(mode='json')
    value_hash = digest(normalized)
    draft_hash = digest({'schema': b.DRAFT, 'id': ref['id'], 'workspace_id': record['workspace_id'],
        'actor_id': record['actor_id'], 'handoff_id': record['handoff_id'], 'candidate_index': record['candidate_index'],
        'revision': ref['revision'], 'anchor': context['anchor'], 'value_hash': value_hash})
    return value == normalized and value_hash == ref['value_hash'] and draft_hash == ref['draft_hash']


def _current_flow(configuration, handoff, index):
    h = legacy.handoff_module()
    matches = [(name, h.resolve_flow(configuration.data, name)) for name in h.names(configuration.data)
        if h.resolve_flow(configuration.data, name).get('reportSource', {}).get('handoff_id') == handoff['id']
        and h.resolve_flow(configuration.data, name).get('reportSource', {}).get('candidate_index') == index]
    if len(matches) != 1:
        fail('source_marker_invalid', '当前业务已删除或来源归属不唯一；核对稿保留')
    return matches[0]


def _mapping(binding, checked):
    ref = binding['reference']
    if not checked.get('first_block_id'):
        fail('region_confirmation_required', '引用区域尚未生成正式确认段落；核对稿已保留')
    return {'scope': ref['scope'], 'path': ref['path'], 'source_material_id': ref['material_id'],
            'source_block_id': ref['block_id'], 'source_location': copy.deepcopy(ref['location']),
            'formal_material_id': binding['copy_material_id'], 'target_block_id': checked['first_block_id'],
            'target_location': copy.deepcopy(checked['locations'][0]), 'quote': ref['quote'],
            'span_binding_id': checked['binding_id']}


def rewrite_basis(basis, formal, mappings):
    """Replace only the exactly scoped citation position; keep its complete quote."""
    out = copy.deepcopy(basis)
    if set(out) != {'business', 'steps'} or not isinstance(out['business'], dict) or not isinstance(out['steps'], list):
        fail('region_mapping_integrity', '流程依据快照无法核对')
    positions = {(m['scope'], m['path']): m for m in mappings}
    if len(positions) != len(mappings):
        fail('region_mapping_integrity', '引用位置重复；不能合并不同位置的相同引文')
    seen = set()
    for path, current in [('/business', out['business']), *[('/steps/' + str(i), s) for i, s in enumerate(out['steps'])]]:
        for index, cite in enumerate(current.get('citations') or []):
            key = ('current_flow', path + '/citations/' + str(index))
            mapping = positions.get(key)
            if (mapping is None or any(cite.get(k) != mapping[v] for k, v in (
                    ('material_id', 'source_material_id'), ('block_id', 'source_block_id'), ('quote', 'quote')))):
                fail('region_mapping_integrity', '引用位置或完整原文与已保存核对稿不同', path=key[1])
            cite.update(material_id=mapping['formal_material_id'], block_id=mapping['target_block_id'],
                        span_binding_id=mapping['span_binding_id'])
            seen.add(key)
        if current.get('sourceId') in formal:
            current['sourceId'] = formal[current['sourceId']].id
        if current.get('citation') is not None:
            current['citation'] = '；'.join(c['block_id'] + '：' + c['quote'] for c in current.get('citations') or [])
    if seen != {key for key in positions if key[0] == 'current_flow'}:
        fail('region_mapping_integrity', '核对稿包含不属于当前流程的引用位置')
    return out


def _saved_context(db, p, handoff_id, draft_ref):
    """Copy only the selected accepted value's proof context, never private history."""
    b = binding_module()
    row = b.checked_draft(db, p, handoff_id, draft_ref['id'], lock=True)
    view = b.draft_view(db, p, row)
    matches = [entry for entry in view['history'] if entry['revision'] == draft_ref['revision']
        and entry['value_hash'] == draft_ref['value_hash'] and entry['draft_hash'] == draft_ref['draft_hash']]
    if len(matches) != 1:
        fail('binding_draft_changed', '所选核对稿版本无法核对；请保留原选择重新读取')
    value = matches[0]['value']
    return {'anchor': copy.deepcopy(view['anchor']), **{k: copy.deepcopy(value[k]) for k in (
        'configuration_revision', 'configuration_hash', 'fact_hash', 'mapping_basis_hash', 'copy_refs')}}


def region_review_value(db, p, handoff, configuration, body, record_id):
    try:
        h = legacy.handoff_module(); h.configuration_cas(configuration, body)
        legacy.selected(handoff, p, body.candidate_index)
        projection = projection_value(body)
        name, flow = _current_flow(configuration, handoff, body.candidate_index)
        if h.fact_hash(name, flow) != body.fact_hash:
            fail('source_fact_changed', '流程或职责已变化，请重新核对；原稿保留')
        original_refs = [ref.model_dump(mode='json') for ref in body.material_refs]
        # The exact existing all-page/document-confirmation gate is deliberately first.
        formal, snapshots = legacy.collect_chain(db, p, handoff, original_refs, projection, lock=True)
        b = binding_module()
        checked = b.formal_bindings(db, p, handoff, configuration, body.candidate_index,
            projection['quote_binding_draft_ref'], projection['region_bindings'], lock=True)
        if len(checked) != len(projection['region_bindings']):
            fail('region_mapping_integrity', '核对稿未覆盖全部引用位置')
        mappings = [_mapping(binding, result) for binding, result in zip(projection['region_bindings'], checked)]
        before_basis = legacy.flow_basis(flow)
        after_basis = rewrite_basis(before_basis, formal, mappings)
        context = _saved_context(db, p, handoff['id'], projection['quote_binding_draft_ref'])
        proposed = copy.deepcopy(configuration.data)
        updated = h.resolve_flow(proposed, name); legacy.apply_basis(updated, after_basis)
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
            'projection': projection, 'binding_context': context, 'mapping_basis': before_basis, 'mappings': mappings,
            'accepted_revision': configuration.version + 1, 'configuration_hash': digest(proposed)})
        receipt = h.make_receipt(p, 'source_review', record, None, handoff['id'])
        if not validate_projection_command(record, body):
            fail('region_mapping_integrity', '核对稿与正式提交范围不同；未改变配置')
        h.ledger_data(record, body, receipt)
        return record, proposed, receipt
    except HTTPException:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, IndexError, RecursionError):
        fail('region_mapping_integrity', '跨区域引用或来源链无法核对；原稿与原件保留')


def verify_projection(db, p, proof, handoff, name=None, flow=None):
    """Recheck frozen proof/regions; later private edits cannot mutate a shared proof."""
    try:
        h = legacy.handoff_module(); h.checked_public(proof)
        legacy.selected(handoff, p, proof['candidate_index'])
        projection = projection_value(proof['projection'])
        if (projection != proof['projection'] or proof['schema'] != h.SOURCE or proof['workspace_id'] != p.workspace_id
                or any(proof[key] != handoff[key] for key in ('report_id', 'configuration_id', 'source_hash'))
                or proof['handoff_id'] != handoff['id']):
            return False
        b = binding_module()
        context = proof['binding_context']
        anchor = b.DraftAnchor.model_validate(context['anchor']).model_dump(mode='json')
        if (anchor != context['anchor'] or anchor['handoff_id'] != handoff['id']
                or anchor['candidate_index'] != proof['candidate_index']
                or context['fact_hash'] != proof['fact_hash'] or context['mapping_basis_hash'] != digest(proof['mapping_basis'])
                or context['copy_refs'] != [ref['copy_ref'] for ref in projection['projection_refs']]
                or not _draft_snapshot_valid(proof, projection, context)):
            return False
        formal, snapshots = legacy.collect_chain(db, p, handoff, proof['material_refs'], projection)
        bindings = projection['region_bindings']
        expected = b.canonical_references(db, p, handoff, None, proof['candidate_index'], frozen_basis=proof['mapping_basis'])
        if len(expected) != len(bindings): return False
        mappings = []
        for binding, reference in zip(bindings, expected):
            ref = binding['reference']
            if ref != reference: return False
            checked = b.validate_region_binding(db, p, anchor, binding, require_confirmed=True)
            allowed = formal.get(ref['material_id']) or next((m for m in formal.values() if m.id == ref['material_id']), None)
            if allowed is None or allowed.id != binding['copy_material_id']: return False
            mappings.append(_mapping(binding, checked))
        after_basis = rewrite_basis(proof['mapping_basis'], formal, mappings)
        if proof['materials'] != snapshots or proof['mappings'] != mappings or proof['basis_hash'] != digest(after_basis):
            return False
        if name is not None or flow is not None:
            if name is None or flow is None or h.fact_hash(name, flow) != proof['fact_hash'] or h.basis_hash(flow) != proof['basis_hash']:
                return False
            if flow.get('reportSource') != {'handoff_id': handoff['id'], 'candidate_index': proof['candidate_index'], 'source_review_id': proof['id']}:
                return False
        return True
    except (HTTPException, KeyError, TypeError, AttributeError, ValueError, IndexError, RecursionError):
        return False


def citation_valid(db, p, flow, scope, path, citation, *, name=None):
    """A span ID is usable only at its exact issued business/step citation path."""
    try:
        h = legacy.handoff_module(); marker = flow['reportSource']
        handoff = h.owned_record(db, p, marker['handoff_id']).data['record']
        proof = h.owned_record(db, p, marker['source_review_id'], kind='source_review').data['record']
        if (scope != 'current_flow' or name is None or proof.get('projection', {}).get('schema') != SCHEMA
                or proof['candidate_index'] != marker['candidate_index'] or not verify_projection(db, p, proof, handoff, name, flow)):
            return False
        mapping = [m for m in proof['mappings'] if m['scope'] == scope and m['path'] == path]
        if len(mapping) != 1: return False
        m = mapping[0]
        return all(citation.get(key) == m[field] for key, field in (
            ('material_id', 'formal_material_id'), ('block_id', 'target_block_id'),
            ('quote', 'quote'), ('span_binding_id', 'span_binding_id')))
    except (HTTPException, KeyError, TypeError, AttributeError, ValueError, IndexError, RecursionError):
        return False


def binding_view(db, p, handoff, proof, binding_id):
    if not verify_projection(db, p, proof, handoff):
        fail('source_review_changed', '正式引用的来源、确认版本或区域已变化；请返回核对')
    bindings = [b for b in proof['projection']['region_bindings'] if digest(b) == binding_id]
    if len(bindings) != 1:
        h = legacy.handoff_module(); h.fail('region_binding_not_found', '未找到此正式来源引用', 404)
    binding = copy.deepcopy(bindings[0])
    checked = binding_module().validate_region_binding(db, p, proof['binding_context']['anchor'], binding, require_confirmed=True)
    from . import material_ocr as ocr
    from .models import SetupMaterial
    from .services import scoped
    material = scoped(db, SetupMaterial, binding['copy_material_id'], p)
    anchor = next(a for a in proof['binding_context']['anchor']['copies'] if a['copy_material_id'] == material.id)
    run = ocr.checked_run(db, p, material, anchor['run_id'])
    unit = ocr.checked_unit(db, p, material, run, checked['unit_id'])
    version = next(v for v in ocr.checked_versions(db, p, run, unit) if v.id == checked['revision_id'])
    selected = {span['region_id'] for span in binding['spans']}
    regions = [{'id': region['id'], 'text': region['text'], 'box': [str(v) for v in region['box']]}
               for region in version.data['regions'] if region['id'] in selected]
    value = {'schema': 'report-source-region-binding-view-v1', 'workspace_id': p.workspace_id,
        'handoff_id': handoff['id'], 'source_review_id': proof['id'], 'record_hash': proof['record_hash'],
        'binding_id': binding_id, 'binding': binding, 'locations': checked['locations'],
        'first_block_id': checked['first_block_id'], 'material_name': material.name,
        'run_id': run.id, 'confirmation_id': material.data['ocr_confirmation_id'],
        'unit': {'id': unit.id, 'kind': 'pdf_page', 'page': checked['page'], 'title': 'PDF 第 ' + str(checked['page']) + ' 页',
                 'width': unit.data['width'], 'height': unit.data['height'], 'image_sha256': checked['image_sha256']},
        'regions': regions}
    return {**value, 'view_hash': digest(value)}
