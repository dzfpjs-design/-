"""Actual OCR validators with synthetic typed rows and explicit read-IO adapters.

No PostgreSQL, real report, OCR model, source authority or filesystem durability
is proved here. SQL inventories and immutable image reads are the only IO fakes;
the old/public and new/batched head, engine, authorization, target, receipt,
image, region and complete version-chain checks actually execute.
"""
import copy
import hashlib
import io
import math
import socket
import subprocess
from types import SimpleNamespace as NS
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, Tuple

from generic_agents import material_ocr as o
from generic_agents import material_ocr_recognition as rec
from generic_agents import material_ocr_runtime as runtime
from generic_agents import report_full_reading_source as full
from generic_agents.contracts import digest
from generic_agents.models import SetupMaterial, MaterialOcrRun as Run, MaterialOcrUnit as Unit
from generic_agents.models import MaterialOcrVersion as Version, MaterialOcrRequest as Request, Job


def uid(label):
    return str(uuid5(NAMESPACE_URL, 'public-ocr-freeze-batch:' + str(label)))


def evaluate(expression, row):
    if isinstance(expression, BooleanClauseList):
        values = [evaluate(child, row) for child in expression.clauses]
        assert expression.operator is operators.and_
        return all(values)
    assert isinstance(expression, BinaryExpression), type(expression)
    left = expression.left
    value = (tuple(getattr(row, item.key) for item in left.clauses)
        if isinstance(left, Tuple) else getattr(row, left.key))
    right = expression.right.value
    if expression.operator is operators.eq: return value == right
    if expression.operator is operators.in_op: return value in right
    raise AssertionError('unexpected SQL boundary: ' + str(expression.operator))


class Inventory:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def _read(self, statement, mode):
        descriptions = statement.column_descriptions
        assert len(descriptions) == 1
        model = descriptions[0]['entity']
        assert descriptions[0]['expr'] is model, 'must load genuine full typed ORM rows'
        self.calls.append((mode, model, statement))
        values = [row for row in self.rows if isinstance(row, model)
            and all(evaluate(clause, row) for clause in statement._where_criteria)]
        if statement._order_by_clauses:
            values.sort(key=lambda row: tuple(getattr(row, col.key) for col in statement._order_by_clauses))
        return values

    def scalar(self, statement):
        values = self._read(statement, 'scalar')
        return values[0] if values else None

    def scalars(self, statement):
        return self._read(statement, 'scalars')


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('static synthetic batch test cannot execute external IO')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def scene(monkeypatch, count=3, *, profile=True, manual=True):
    p = NS(workspace_id=uid('workspace'), user_id=uid('actor'), role='operator')
    material = SetupMaterial(id=uid('material'), workspace_id=p.workspace_id,
        name='synthetic.pdf', version=2, sha256=digest('synthetic original'), evidence_id=uid('pdf'), data={})
    renderer_manifest = {'id': 'synthetic-renderer', 'manifest_hash': 'd' * 64}
    renderer = {'id': 'synthetic-renderer', 'manifest_hash': 'd' * 64,
        'manifest': renderer_manifest, 'summary_hash': digest(renderer_manifest)}
    low_manifest = {'id': runtime.LOW_MEMORY_ENGINE_ID,
        'manifest_hash': runtime.LOW_MEMORY_MANIFEST_HASH, 'synthetic_test': True}
    low = {'id': runtime.LOW_MEMORY_ENGINE_ID, 'manifest_hash': runtime.LOW_MEMORY_MANIFEST_HASH,
        'manifest': low_manifest, 'summary_hash': digest(low_manifest)}
    engine = low if profile else renderer
    descriptors = [{'index': n, 'kind': 'pdf_page', 'page': n, 'body_index': None,
        'has_native_text': False, 'has_image': True, 'has_annotations': False} for n in range(1, count + 1)]
    coverage = {'contract_version': 1, 'source_sha256': material.sha256,
        'status': 'needs_ocr', 'unit_count': count, 'units': descriptors, 'gaps': [], 'reason': None}
    coverage['hash'] = digest(coverage)
    run = Run(id=uid('run'), workspace_id=p.workspace_id, material_id=material.id,
        generation=2, version=2, status='review', data={'scope': {'workspace_id': p.workspace_id,
            'material_id': material.id, 'evidence_id': material.evidence_id, 'source_sha256': material.sha256,
            'name': material.name}, 'epoch': 2, 'prepared': count, 'coverage': coverage,
            'engine': renderer, 'native_blocks': [], 'native_blocks_hash': digest([])})
    units, rows, images, image_reads = [], [material, run], {}, []
    image = io.BytesIO()
    Image.new('RGB', (96, 60), 'white').save(image, format='PNG')
    image_content = image.getvalue(); image_hash = hashlib.sha256(image_content).hexdigest()
    for n, descriptor in enumerate(descriptors, 1):
        unit = Unit(id=uid(('unit', n)), workspace_id=p.workspace_id, material_id=material.id,
            run_id=run.id, unit_index=n, version=1, status='review', evidence_id=uid(('image', n)),
            data={'descriptor': descriptor, 'image_sha256': image_hash, 'width': 96, 'height': 60,
                'render': {'page': n, 'width': 96, 'height': 60, 'receipt': {
                    'input_sha256': material.sha256, 'output_sha256': image_hash,
                    'manifest_hash': renderer['manifest_hash'], 'summary_hash': renderer['summary_hash'],
                    'manifest': renderer['manifest']}}})
        units.append(unit); rows.append(unit)
        images[unit.evidence_id] = (NS(media_type='image/png', sha256=image_hash), image_content)
    auth_request = None
    if profile:
        request_id = uid('recognition request')
        body = {'request_id': request_id, 'run_id': run.id, 'engine_id': low['id'],
            'engine_manifest_hash': low['manifest_hash'], 'expected_material_version': 1,
            'expected_run_version': 1}
        body_hash = o._request_hash('resume_with_profile', None, body)
        auth = {'schema': rec.AUTH_SCHEMA, 'workspace_id': p.workspace_id, 'material_id': material.id,
            'run_id': run.id, 'generation': run.generation, 'source_sha256': material.sha256,
            'renderer_manifest_hash': renderer['manifest_hash'], 'actor_id': p.user_id,
            'request_id': request_id, 'request_hash': body_hash, 'epoch': 2, 'prepared_at': count,
            'rawless_unit_ids': [u.id for u in units], 'engine': low}
        auth['authorization_hash'] = digest(auth)
        data = {'body': body, 'target': None, 'result': {'run_id': run.id,
            'material_version': 2, 'run_version': 2, 'recognition_authorization': auth}}
        data['record_hash'] = digest(data)
        auth_request = Request(id=uid('auth row'), workspace_id=p.workspace_id, material_id=material.id,
            actor_id=p.user_id, request_id=request_id, action='resume_with_profile', state='saved',
            body_hash=body_hash, data=data)
        rows.append(auth_request)
        run.data = {**run.data, 'recognition_authorizations': [auth],
            'future_recognition_authorization': auth['authorization_hash'], 'future_recognition_engine': low}
        for unit in units:
            unit.data = {**unit.data, 'recognition_engine': low,
                'recognition_authorization': auth['authorization_hash']}
    versions, jobs, manual_requests = [], [], []
    for n, unit in enumerate(units, 1):
        target = {'actor_id': p.user_id, 'workspace_id': p.workspace_id, 'material_id': material.id,
            'run_id': run.id, 'generation': run.generation, 'epoch': 2, 'source_sha256': material.sha256,
            'engine_manifest_hash': renderer['manifest_hash'], 'unit_id': unit.id, 'image_sha256': image_hash}
        target.update(rec.target_fields(run, unit))
        job = Job(id=uid(('job', n)), workspace_id=p.workspace_id, kind='recognize_material_ocr_unit',
            status='succeeded', data={'target': target, 'target_hash': digest(target)})
        jobs.append(job); rows.append(job)
        original_regions = [{'id': 'r1', 'text': '真实合成页' + str(n), 'box': [1.0, -0.0, 70, 10], 'score': .9}]
        regions = [{**copy.deepcopy(r), 'source_region_id': None} for r in original_regions]
        data = {'parent_hash': None, 'plain_text': regions[0]['text'], 'regions': regions,
            'image_sha256': image_hash, 'engine_manifest_hash': engine['manifest_hash'], 'job_id': job.id,
            'job_target_hash': digest(target), 'actor_id': p.user_id,
            'engine_result': {'regions': original_regions}, 'receipt': {'input_sha256': image_hash,
                'manifest_hash': engine['manifest_hash'], 'result_sha256': digest(original_regions),
                'summary_hash': engine['summary_hash'], 'manifest': engine['manifest']}}
        if profile: data.update(recognition_contract=rec.CONTRACT,
            recognition_authorization=unit.data['recognition_authorization'])
        revision = Version(id=uid(('raw', n)), workspace_id=p.workspace_id, run_id=run.id,
            unit_id=unit.id, number=1, kind='raw', data=data)
        revision.content_hash = o.version_hash(revision)
        versions.append(revision); rows.append(revision)
        unit.data = {**unit.data, 'raw_id': revision.id, 'last_hash': revision.content_hash}
        if manual and n == 1:
            for number, kind in ((2, 'edited'), (3, 'confirmed')):
                request_id = uid(('manual', number))
                manual_data = {'parent_hash': revision.content_hash, 'plain_text': '逐字核对' + str(number),
                    'regions': [{'id': 'edited', 'text': '逐字核对' + str(number),
                        'box': [1.0, -0.0, 70, 10], 'score': .9, 'source_region_id': 'r1'}],
                    'image_sha256': image_hash, 'engine_manifest_hash': engine['manifest_hash'],
                    'actor_id': p.user_id, 'request_id': request_id}
                if profile: manual_data.update(recognition_contract=rec.CONTRACT,
                    recognition_authorization=unit.data['recognition_authorization'])
                revision = Version(id=uid(('manual version', number)), workspace_id=p.workspace_id,
                    run_id=run.id, unit_id=unit.id, number=number, kind=kind, data=manual_data)
                revision.content_hash = o.version_hash(revision)
                versions.append(revision); rows.append(revision)
                body = {'request_id': request_id, 'reason': '合成版本原请求', 'value': number}
                request_data = {'target': unit.id, 'body': body,
                    'result': {'revision_id': revision.id, 'revision_hash': revision.content_hash}}
                request_data['record_hash'] = digest(request_data)
                request = Request(id=uid(('manual request', number)), workspace_id=p.workspace_id,
                    material_id=material.id, actor_id=p.user_id, request_id=request_id,
                    action='save' if kind == 'edited' else 'confirm', state='saved',
                    body_hash=o._request_hash('save' if kind == 'edited' else 'confirm', unit.id, body), data=request_data)
                manual_requests.append(request); rows.append(request)
                unit.data = {**unit.data, 'draft_id' if kind == 'edited' else 'confirmed_id': revision.id,
                    'last_hash': revision.content_hash}
            unit.status = 'confirmed'
        o.seal(unit)
    o.seal(run)
    db = Inventory(rows)

    def image_read(database, principal, identity):
        assert database is db and principal is p
        image_reads.append(identity)
        if identity not in images: o.fail('synthetic image missing at IO boundary')
        evidence, content = images[identity]
        if hashlib.sha256(content).hexdigest() != evidence.sha256: o.fail('synthetic image changed')
        return evidence, content

    monkeypatch.setattr(o, 'evidence_bytes', image_read)
    return NS(db=db, p=p, material=material, run=run, units=units, versions=versions,
        jobs=jobs, manual_requests=manual_requests, auth_request=auth_request,
        images=images, image_reads=image_reads)


def legacy(c):
    rec.check_run(c.db, c.run)
    return [(o.checked_unit(c.db, c.p, c.material, c.run, u.id),
        o.checked_versions(c.db, c.p, c.run, u)) for u in c.units]


def batched(c):
    recognition = rec._FreezeRecognition(c.db, c.p, c.run)
    recognition.check_run()
    return list(o._freeze_units(c.db, c.p, c.material, c.run, c.units, recognition))


def projection(values):
    return [[full._page(unit, row) for row in rows] for unit, rows in values]


@pytest.mark.parametrize('profile', [False, True])
def test_batch_is_literal_equivalent_to_old_raw_edited_confirmed_chain(monkeypatch, profile):
    c = scene(monkeypatch, profile=profile)
    before = copy.deepcopy([(row.id, row.data) for row in c.db.rows])
    old = projection(legacy(c)); fresh = projection(batched(c))
    assert fresh == old
    assert fresh[0][0]['regions'][0]['box'][:2] == ['1.0', '-0.0']
    assert len(c.image_reads) == len(c.units) * 2
    assert [(row.id, row.data) for row in c.db.rows] == before


def test_current_rows_are_read_in_bounded_batches_not_once_per_page(monkeypatch):
    c = scene(monkeypatch, count=65)
    assert len(batched(c)) == 65
    counts = {model: sum(item[1] is model for item in c.db.calls)
        for model in (Unit, Version, Job, Request)}
    assert counts == {Unit: 0, Version: math.ceil(65 / 32), Job: math.ceil(65 / 32), Request: 2}
    assert c.image_reads == [u.evidence_id for u in c.units]
    assert all(item[2]._limit_clause is None for item in c.db.calls)


def test_public_validators_still_independently_read_each_invocation(monkeypatch):
    c = scene(monkeypatch)
    legacy(c); first = len(c.db.calls)
    legacy(c)
    assert len(c.db.calls) == 2 * first
    assert sum(model is Unit for _, model, _ in c.db.calls) == 2 * len(c.units)


@pytest.mark.parametrize('missing', ['version', 'job', 'manual_request', 'authorization'])
def test_missing_inventory_row_rejects_instead_of_using_old_success(monkeypatch, missing):
    c = scene(monkeypatch)
    assert batched(c)
    row = {'version': c.versions[0], 'job': c.jobs[0],
        'manual_request': c.manual_requests[0], 'authorization': c.auth_request}[missing]
    c.db.rows.remove(row)
    with pytest.raises(HTTPException): batched(c)


@pytest.mark.parametrize('duplicate', ['version', 'job', 'manual_request', 'authorization'])
def test_duplicate_batch_inventory_is_never_arbitrarily_selected(monkeypatch, duplicate):
    c = scene(monkeypatch)
    row = {'version': c.versions[0], 'job': c.jobs[0],
        'manual_request': c.manual_requests[0], 'authorization': c.auth_request}[duplicate]
    c.db.rows.append(row)
    with pytest.raises(HTTPException): batched(c)


@pytest.mark.parametrize('damage', ['head', 'parent', 'version_hash', 'image_hash', 'engine',
    'plain_text', 'regions', 'job_kind', 'target', 'receipt', 'engine_result', 'image_bytes',
    'missing_image', 'cross_run', 'unknown_kind', 'manual_unselected'])
def test_same_original_integrity_failures_remain_closed(monkeypatch, damage):
    c = scene(monkeypatch)
    raw = c.versions[0]; unit = c.units[0]; job = c.jobs[0]
    if damage == 'head': unit.data['head_hash'] = '0' * 64
    elif damage == 'parent': raw.data['parent_hash'] = '0' * 64
    elif damage == 'version_hash': raw.content_hash = '0' * 64
    elif damage == 'image_hash': raw.data['image_sha256'] = '0' * 64
    elif damage == 'engine': unit.data['recognition_engine']['summary_hash'] = '0' * 64; o.seal(unit)
    elif damage == 'plain_text': raw.data['plain_text'] = 'not the original regions'
    elif damage == 'regions': raw.data['regions'][0]['box'][2] = 1000
    elif damage == 'job_kind': job.kind = 'other_job'
    elif damage == 'target':
        job.data['target']['unit_id'] = c.units[1].id
        job.data['target_hash'] = digest(job.data['target']); raw.data['job_target_hash'] = job.data['target_hash']
    elif damage == 'receipt': raw.data['receipt']['result_sha256'] = '0' * 64
    elif damage == 'engine_result': raw.data['engine_result']['regions'][0]['text'] = 'changed original'
    elif damage == 'image_bytes':
        evidence, _ = c.images[unit.evidence_id]; c.images[unit.evidence_id] = (evidence, b'changed PNG')
    elif damage == 'missing_image': del c.images[unit.evidence_id]
    elif damage == 'cross_run': raw.run_id = uid('different run')
    elif damage == 'unknown_kind': raw.kind = 'hidden_kind'
    elif damage == 'manual_unselected': c.versions[1].data['parent_hash'] = '0' * 64
    # Reseal the altered row where useful: even matching self hashes are not
    # substitutes for actual source/target/receipt and parent relationships.
    if damage not in {'head', 'version_hash', 'manual_unselected'}:
        raw.content_hash = o.version_hash(raw)
    with pytest.raises(HTTPException): legacy(c)
    with pytest.raises(HTTPException): batched(c)


def test_new_freeze_rereads_original_authorization_and_does_not_cache_db(monkeypatch):
    c = scene(monkeypatch)
    assert batched(c)
    before = len(c.db.calls)
    c.auth_request.data['body']['engine_id'] = 'not the saved engine'
    with pytest.raises(HTTPException): batched(c)
    assert len(c.db.calls) > before


def test_run_authorizations_are_read_once_but_every_reference_is_checked(monkeypatch):
    c = scene(monkeypatch)
    context = rec._FreezeRecognition(c.db, c.p, c.run)
    before = len(c.db.calls)
    context.check_run()
    for unit in c.units:
        context.effective_engine(unit)
        context.check_target(unit, next(job for job in c.jobs
            if job.data['target']['unit_id'] == unit.id).data['target'])
    assert len(c.db.calls) == before
    assert sum(model is Request for _, model, _ in c.db.calls) == 1
    with pytest.raises(HTTPException): context.authorization('0' * 64)


def test_nonselected_manual_version_keeps_parent_and_saved_request_checks(monkeypatch):
    c = scene(monkeypatch)
    edited = c.versions[1]
    edited.data['parent_hash'] = '0' * 64
    edited.content_hash = o.version_hash(edited)
    request = c.manual_requests[0]
    request.data['result']['revision_hash'] = edited.content_hash
    request.data['record_hash'] = digest({k: v for k, v in request.data.items() if k != 'record_hash'})
    # A later selection of raw text is not permission to ignore damaged edits.
    with pytest.raises(HTTPException): projection(batched(c))


def test_checked_run_still_checks_current_source_before_loading_batch_inventory(monkeypatch):
    c = scene(monkeypatch)
    calls = []
    def unavailable(database, principal, material, run):
        assert (database, principal, material, run) == (c.db, c.p, c.material, c.run)
        calls.append('fresh source')
        raise HTTPException(409, 'actual source unavailable at explicit IO boundary')
    monkeypatch.setattr(o, 'source', unavailable)
    for _ in range(2):
        with pytest.raises(HTTPException):
            o._checked_run_for_freeze(c.db, c.p, c.material, c.run.id)
    assert calls == ['fresh source', 'fresh source']
    assert not any(model is Request for _, model, _ in c.db.calls)


@pytest.mark.parametrize('scope', ['run', 'workspace', 'actor', 'session'])
def test_batch_refuses_context_from_another_scope(monkeypatch, scope):
    c = scene(monkeypatch)
    context = rec._FreezeRecognition(c.db, c.p, c.run)
    db, p, run = c.db, c.p, c.run
    if scope == 'run': run = copy.copy(c.run)
    elif scope == 'workspace': p = NS(workspace_id=uid('other workspace'), user_id=c.p.user_id)
    elif scope == 'actor': p = NS(workspace_id=c.p.workspace_id, user_id=uid('other reader'))
    elif scope == 'session': db = Inventory(c.db.rows)
    with pytest.raises(HTTPException):
        list(o._freeze_units(db, p, c.material, run, c.units, context))
    outsider = NS(workspace_id=uid('other workspace'))
    with pytest.raises(HTTPException):
        rec._FreezeRecognition(c.db, outsider, c.run)
