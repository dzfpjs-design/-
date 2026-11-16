"""Real bounded bytes + synthetic authority seams, NOT leased-worker proof."""
import copy
import hashlib
import io
import json
from types import SimpleNamespace as NS
from uuid import uuid4
import wave

import pytest
from fastapi import HTTPException

from generic_agents import workflow_data_quality_v219 as q
from generic_agents.contracts import digest


def uid(): return str(uuid4())


def original(root, workspace, content=b'public original', *, media='text/plain', identity=None):
    hashed = hashlib.sha256(content).hexdigest()
    folder = root / workspace; folder.mkdir(parents=True, exist_ok=True)
    (folder / (hashed + '.bin')).write_bytes(content)
    row = NS(id=identity or uid(), workspace_id=workspace, sha256=hashed,
        size_bytes=len(content), storage_key=workspace + '/' + hashed + '.bin', media_type=media)
    return {'row': row, 'name': 'public', 'refs': [{'kind': 'image', 'id': row.id, 'selection_hash': digest(row.id)}]}


@pytest.mark.parametrize('handler', [q.QUALITY, q.DEDUP])
def test_registration_template_is_local_technical_only_and_closed(handler):
    data = q.manifest(handler).model_dump(mode='json')
    assert q.validate_manifest(data) == data
    for path, value in [('roles', ['business']), ('side_effect', True), ('side_effect', 0), ('cloud', True),
            ('schema', {'type': 'object'}), ('specification', {})]:
        broken = copy.deepcopy(data)
        if path == 'roles': broken['allowed_agent_roles'] = value
        elif path == 'cloud': broken['data_policy']['cloud_allowed'] = value
        elif path == 'schema': broken['input_schema'] = value
        elif path == 'side_effect': broken['execution'][path] = value
        else: broken[path] = value
        with pytest.raises(HTTPException): q.validate_manifest(broken)


def test_real_bytes_hash_groups_only_identical_originals_without_selecting_or_deleting(tmp_path):
    root = tmp_path.resolve(); workspace = uid()
    a = original(root, workspace, b'alpha'); b = original(root, workspace, b'alpha')
    c = original(root, workspace, b'beta')
    rows = [a, b, c]; before = copy.deepcopy([r['row'].__dict__ for r in rows])
    files, issues = q.inspect_files(rows, root=root, workspace_id=workspace)
    assert not issues and all(f['hash_verified'] and f['format_verified'] for f in files)
    groups, duplicates = q.grouped(files)
    assert len(groups) == 2 and len(duplicates) == 1
    assert duplicates[0]['evidence_ids'] == sorted([a['row'].id, b['row'].id])
    assert before == [r['row'].__dict__ for r in rows]
    assert all((root / r['row'].storage_key).read_bytes() for r in rows)
    # A repeated reference to the SAME Evidence is not a second original.
    assert q.grouped([files[0], copy.deepcopy(files[0])])[1] == []


@pytest.mark.parametrize('fault', ['missing', 'changed_bytes', 'symlink_file', 'symlink_workspace',
    'traversal_key', 'foreign_workspace', 'wrong_size', 'boolean_size', 'oversized'])
def test_bad_original_never_becomes_success_or_duplicate(tmp_path, fault):
    root = tmp_path.resolve(); workspace = uid(); selected = original(root, workspace)
    row = selected['row']; target = root / row.storage_key
    if fault == 'missing': target.unlink()
    elif fault == 'changed_bytes': target.write_bytes(b'changed bytes')
    elif fault == 'symlink_file':
        other = root / 'other'; other.write_bytes(target.read_bytes()); target.unlink(); target.symlink_to(other)
    elif fault == 'symlink_workspace':
        (root / workspace).rename(root / 'elsewhere'); (root / workspace).symlink_to(root / 'elsewhere', target_is_directory=True)
    elif fault == 'traversal_key': row.storage_key = '../' + row.storage_key
    elif fault == 'foreign_workspace': row.workspace_id = uid()
    elif fault == 'wrong_size': row.size_bytes += 1
    elif fault == 'boolean_size': row.size_bytes = True
    else: row.size_bytes = q.MAX_FILE_BYTES + 1
    files, issues = q.inspect_files([selected], root=root, workspace_id=workspace)
    assert len(files) == 1 and not files[0]['hash_verified'] and files[0]['actual_sha256'] is None
    assert [i['code'] for i in issues] == ['unreadable_original']
    assert issues[0]['severity'] == 'blocked' and q.grouped(files) == ([], [])


def test_actual_hash_not_claimed_hash_or_client_duplicate_key_drives_grouping(tmp_path):
    root = tmp_path.resolve(); workspace = uid()
    a = original(root, workspace, b'alpha'); b = original(root, workspace, b'beta')
    (root / b['row'].storage_key).write_bytes(b'alpha')
    files, issues = q.inspect_files([a, b], root=root, workspace_id=workspace)
    assert issues and files[0]['hash_verified'] and not files[1]['hash_verified']
    assert q.grouped(files)[1] == []


def test_quantity_and_total_bytes_limit_fail_closed_without_false_empty_pass(tmp_path, monkeypatch):
    root = tmp_path.resolve(); workspace = uid(); a = original(root, workspace, b'alpha')
    files, issues = q.inspect_files([a] * (q.MAX_FILES + 1), root=root, workspace_id=workspace)
    assert files == [] and issues[0]['code'] == 'source_limit' and issues[0]['severity'] == 'blocked'
    monkeypatch.setattr(q, 'MAX_TOTAL_BYTES', 4)
    files, issues = q.inspect_files([a], root=root, workspace_id=workspace)
    assert not files[0]['hash_verified'] and issues[0]['severity'] == 'blocked'
    files, issues = q.inspect_files([], root=root, workspace_id=workspace)
    assert files == [] and issues[0]['code'] == 'empty_source'


@pytest.mark.parametrize('raw,media,expected', [
    (b'{"count":1}', 'application/json', 'valid'),
    (b'{"count":1,"count":2}', 'application/json', 'invalid'),
    (b'{"x":NaN}', 'application/json', 'invalid'),
    (b'{"x":', 'application/json', 'invalid'),
    (b'a,b\n1,2\n', 'text/csv', 'valid'),
    (b'a,b\n1\n', 'text/csv', 'invalid'),
    (b'\xff', 'text/plain', 'invalid'),
    (b'\0binary', 'text/plain', 'invalid'),
    (b'%PDF-1.7\n', 'application/pdf', 'unknown'),
    (b'PK\x03\x04', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'unknown'),
    (b'\0\0\0\x18ftypmp42', 'video/mp4', 'unknown')])
def test_format_requires_actual_parser_or_saved_cold_proof_not_magic_headers(raw, media, expected):
    assert q.format_check(raw, media)[0] == expected


def test_real_image_and_wav_decode_and_truncation_are_checked():
    from PIL import Image
    image = io.BytesIO(); Image.new('RGB', (8, 8), 'green').save(image, format='PNG')
    assert q.format_check(image.getvalue(), 'image/png')[0] == 'valid'
    assert q.format_check(image.getvalue()[:20], 'image/png')[0] == 'invalid'
    assert q.format_check(image.getvalue(), 'image/jpeg')[0] == 'invalid'
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(8000); out.writeframes(b'\0\0' * 80)
    assert q.format_check(audio.getvalue(), 'audio/wav')[0] == 'valid'
    assert q.format_check(audio.getvalue()[:-5], 'audio/wav')[0] == 'invalid'


def outcome(tmp_path):
    workspace = uid(); data = original(tmp_path.resolve(), workspace, b'original')
    files, issues = q.inspect_files([data], root=tmp_path.resolve(), workspace_id=workspace)
    groups, duplicates = q.grouped(files)
    producer = {key: uid() for key in ('workspace_id', 'task_id', 'job_id', 'invocation_id', 'capability_id', 'actor_id')}
    producer.update(workspace_id=workspace, activation_id='original#1', node_id='original',
        capability_hash='a'*64, graph_hash='b'*64, input_hash=digest({'scope_ref': q.scope_ref()}), request_key_hash=digest('original-key'))
    report = {'schema': q.REPORT, 'operation': 'quality', 'producer': producer,
        'source_inventory_hash': 'c'*64, 'entity_context_hash': 'd'*64,
        'quality_status': 'pass', 'attributes': q.attributes(files, issues, duplicates), 'files': files,
        'issues': issues, 'duplicate_groups': duplicates, 'digest_groups': groups, 'covered_source_kinds': ['image'], **q.FLAGS}
    report['report_hash'] = digest(report)
    return {'schema': q.RESULT, 'report': report, **{k: copy.deepcopy(report[k]) for k in
        ('quality_status', 'attributes', 'files', 'issues', 'duplicate_groups', 'digest_groups')}, **q.FLAGS}


@pytest.mark.parametrize('fault', ['numeric_summary', 'modified_sha', 'false_status', 'promoted_semantics'])
def test_platform_metrics_are_recomputed_not_self_sealed_success(tmp_path, fault):
    output = outcome(tmp_path); assert q.checked_output(output) == output
    if fault == 'numeric_summary':
        output['attributes']['file_count'] = 99; output['report']['attributes']['file_count'] = 99
    elif fault == 'modified_sha': output['report']['files'][0]['actual_sha256'] = '0'*64
    elif fault == 'false_status': output['quality_status'] = output['report']['quality_status'] = 'needs_review'
    else: output['semantic_completeness_verified'] = True
    output['report']['report_hash'] = digest({k:v for k,v in output['report'].items() if k != 'report_hash'})
    with pytest.raises(HTTPException): q.checked_output(output)


def test_no_valid_manifest_can_substitute_for_actual_scope_lease(tmp_path, monkeypatch):
    cap = NS(data=q.manifest().model_dump(mode='json'))
    def denied(*args): raise HTTPException(403, 'No actual lease/Task authority')
    monkeypatch.setattr(q.graph_scope, 'graph_context', denied)
    monkeypatch.setattr(q, 'inventory', lambda *args: pytest.fail('No file lookup before real authority'))
    with pytest.raises(HTTPException) as error: q.execute(None, None, cap, {'scope_ref':q.scope_ref()}, 'unowned')
    assert error.value.status_code == 403


def test_produced_audit_checks_real_output_and_does_not_allow_client_hash_substitution(tmp_path):
    output = outcome(tmp_path); producer = output['report']['producer']
    call = NS(id=producer['invocation_id'], workspace_id=producer['workspace_id'], status='succeeded',
        capability_id=producer['capability_id'], idempotency_key='original-key', data={
            'capability_hash': producer['capability_hash'], 'input_hash':producer['input_hash'],
            'job_id':producer['job_id'], 'role':'data', 'output':output})
    job = NS(id=producer['job_id'], workspace_id=producer['workspace_id'], kind=q.graph_scope.KIND,
        data={'task_id':producer['task_id'], 'activation_id':producer['activation_id'],
            'actor_id':producer['actor_id'], 'input_hash':producer['input_hash'],
            'graph_hash':producer['graph_hash'], 'invocation_key':'original-key'})
    proof = q.receipt_audit_data(call,job,output)
    assert proof['output_hash'] == digest(output)
    call.data['output'] = copy.deepcopy(output); call.data['output']['issues'] = [q.issue('source_partial')]
    with pytest.raises(HTTPException): q.receipt_audit_data(call,job,output)
