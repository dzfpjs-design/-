"""Technical original-file checks, not observation/biology truth or data ACK.

Only an actual leased graph activation selects the Task. Caller-supplied file
paths, hashes and arbitrary workspace evidence are never accepted. The two
tools retain every original and group byte-identical evidence identities only.
"""
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import warnings
import wave

from fastapi import HTTPException
from sqlalchemy import select

from .contracts import digest
from .config import settings
from .models import Audit, Evidence, Invocation, Job, SetupMaterial, Entity, DomainInstance, InstanceRelease, Capability
from .services import scoped, validate, audit
from . import workflow_graph_scope as graph_scope

QUALITY = 'workflow_task_originals_quality_v219'
DEDUP = 'workflow_task_originals_dedup_v219'
HANDLERS = {QUALITY: 'quality', DEDUP: 'deduplicate'}
REF = 'current-task-originals-ref-v219'
REPORT = 'workflow-task-originals-report-v219'
RESULT = 'workflow-task-originals-result-v219'
PRODUCED = 'workflow.originals_quality.produced_v219'
MAX_FILES = 100
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 16_000_000
SHA = re.compile(r'^[a-f0-9]{64}$')
UUID = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')
FLAGS = {'physical_state_verified': False, 'species_identification_verified': False,
    'semantic_completeness_verified': False, 'independent_data_ack': False,
    'originals_deleted': False, 'cloud_authorized': False}


def need(ok, message='原任务、质检报告或原件来源无法核对，未采用客户提供的文件摘要', code=409):
    if not ok: raise HTTPException(code, message)


def same(left, right): return digest(left) == digest(right)


def obj(fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


def text(): return {'type': 'string'}
def ident(): return {'type': 'string', 'pattern': UUID.pattern}
def hashed(): return {'type': 'string', 'pattern': SHA.pattern}
def array(items, maximum=MAX_FILES): return {'type': 'array', 'items': items, 'maxItems': maximum}


def scope_schema():
    return obj({'schema': {'const': REF}, 'selection': {'const': 'approved_task_originals'}})


def scope_ref(): return {'schema': REF, 'selection': 'approved_task_originals'}


def issue_schema():
    return obj({'code': text(), 'severity': {'enum': ['review', 'blocked']},
        'evidence_id': {'type': ['string', 'null']}, 'field': text(), 'message': text()})


def attributes_schema():
    return obj({key: {'type': 'integer', 'minimum': 0} for key in
        ('file_count', 'hash_verified_count', 'format_verified_count', 'duplicate_group_count',
            'technical_review_required', 'integrity_blocked')})


def report_schema():
    source = obj({'kind': text(), 'id': text(), 'selection_hash': hashed()})
    file = obj({'evidence_id': ident(), 'media_type': text(),
        'expected_sha256': hashed(), 'actual_sha256': {'type': ['string', 'null']},
        'size_bytes': {'type': ['integer', 'null'], 'minimum': 0},
        'hash_verified': {'type': 'boolean'}, 'format_verified': {'type': 'boolean'},
        'format_method': text(), 'source_refs': array(source, 200)})
    group = obj({'sha256': hashed(), 'size_bytes': {'type': 'integer', 'minimum': 1},
        'evidence_ids': array(ident())})
    producer = obj({**{key: ident() for key in ('workspace_id', 'task_id', 'job_id', 'invocation_id', 'capability_id', 'actor_id')},
        'activation_id': text(), 'node_id': text(), 'capability_hash': hashed(),
        'graph_hash': hashed(), 'input_hash': hashed(), 'request_key_hash': hashed()})
    return obj({'schema': {'const': REPORT}, 'operation': {'enum': list(HANDLERS.values())},
        'producer': producer, 'source_inventory_hash': hashed(), 'entity_context_hash': hashed(),
        'quality_status': {'enum': ['pass', 'needs_review', 'blocked']}, 'attributes': attributes_schema(), 'files': array(file),
        'issues': array(issue_schema(), 500), 'duplicate_groups': array(group), 'digest_groups': array(group),
        'covered_source_kinds': array(text(), 20), 'report_hash': hashed(),
        **{key: {'const': value} for key, value in FLAGS.items()}})


def input_schema(handler=QUALITY):
    need(handler in HANDLERS, code=422)
    fields = {'scope_ref': scope_schema()}
    if handler == DEDUP: fields['quality_report'] = report_schema()
    return obj(fields)


def output_schema():
    return obj({'schema': {'const': RESULT}, 'quality_status': {'enum': ['pass', 'needs_review', 'blocked']},
        'report': report_schema(), 'attributes': attributes_schema(), 'files': report_schema()['properties']['files'],
        'issues': report_schema()['properties']['issues'],
        'duplicate_groups': report_schema()['properties']['duplicate_groups'],
        'digest_groups': report_schema()['properties']['digest_groups'],
        **{key: {'const': value} for key, value in FLAGS.items()}})


def validate_manifest(value):
    data = value.model_dump(mode='json') if hasattr(value, 'model_dump') else copy.deepcopy(value)
    handler = data.get('execution', {}).get('handler')
    need(handler in HANDLERS and data.get('kind') == 'tool' and data.get('allowed_agent_roles') == ['data']
        and same(data.get('execution'), {'adapter': 'builtin', 'handler': handler, 'side_effect': False})
        and same(data.get('input_schema'), input_schema(handler)) and same(data.get('output_schema'), output_schema())
        and data.get('data_policy', {}).get('deployment') == 'local'
        and data.get('data_policy', {}).get('cloud_allowed') is False
        and data.get('data_policy', {}).get('external_allowed') is False
        and data.get('dependencies') == [] and same(data.get('budget'), {'max_attempts': 1, 'max_concurrency': 1})
        and type(data['budget']['max_attempts']) is int
        and same(data.get('specification'), {'schema': 'workflow-originals-technical-check-contract-v219',
            'operation': HANDLERS.get(handler), 'retention': 'retain_all_originals', 'semantic_quality': 'not_verified'}),
        '原件技术质检须固定完整合同、数据角色、本地只读和保留全部原件；不能扩大为语义质检或数据ACK', 422)
    return data


def manifest(handler=QUALITY):
    """Public registration template; normal validate/publish still required."""
    from .contracts import Manifest
    need(handler in HANDLERS, code=422)
    return Manifest(kind='tool', namespace='task_originals_quality_v219', name=handler,
        version='1.0.0', owner='平台技术原件检查',
        description=('任务原件技术格式与来源完整性检查' if handler == QUALITY else '任务原件同SHA字节分组；保留全部原件')
            + '；不证明物种、业务字段语义或现场状态，不替代独立接收',
        input_schema=input_schema(handler), output_schema=output_schema(), allowed_agent_roles=['data'],
        execution={'adapter': 'builtin', 'handler': handler, 'side_effect': False},
        data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False},
        budget={'max_attempts': 1, 'max_concurrency': 1}, dependencies=[],
        specification={'schema': 'workflow-originals-technical-check-contract-v219',
            'operation': HANDLERS[handler], 'retention': 'retain_all_originals', 'semantic_quality': 'not_verified'})


def issue(code, evidence_id=None, field='', *, blocked=False):
    labels = {'unreadable_original': '原件缺失、不安全、超限或摘要不符，未当作有效原件',
        'unsupported_format': '尚无当前格式的完整技术核验依据，未仅凭文件头宣称可用',
        'invalid_format': '原件内容与声明格式不符或内容不完整',
        'empty_source': '本任务没有可核对的已批准原件，未产生成功空报告',
        'unsupported_source': '本任务还包含尚未接入此技术检查的来源，未宣称全部完成',
        'source_partial': '所选来源仅为部分解析或OCR候选，未声明整份资料完整',
        'source_changed': '原件或来源版本与任务冻结依据不一致',
        'source_limit': '原件数量或总体积超过本次安全核验范围，未截取后声称完整'}
    return {'code': code, 'severity': 'blocked' if blocked else 'review', 'evidence_id': evidence_id,
        'field': field, 'message': labels[code]}


def read_original(root, workspace_id, row, *, remaining=MAX_TOTAL_BYTES):
    """Open each path component without symlinks; hash the actual bounded bytes."""
    need(type(workspace_id) is str and UUID.fullmatch(workspace_id) is not None
        and SHA.fullmatch(row.sha256 or '') is not None
        and row.workspace_id == workspace_id and type(row.size_bytes) is int
        and 0 < row.size_bytes <= min(MAX_FILE_BYTES, remaining)
        and row.storage_key == workspace_id + '/' + row.sha256 + '.bin')
    root = Path(root)
    # Settings historically permit a relative storage root. Make only that
    # server-controlled root absolute without resolving any symlink component.
    if not root.is_absolute(): root = Path.cwd() / root
    handles = []
    try:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        handles.append(fd)
        for part in (*root.parts[1:], workspace_id):
            need(part not in {'', '.', '..'})
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            handles.append(fd)
        fd = os.open(row.sha256 + '.bin', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        handles.append(fd); before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_size == row.size_bytes)
        chunks, count, hashed_value = [], 0, hashlib.sha256()
        while True:
            chunk = os.read(fd, min(65536, row.size_bytes + 1 - count))
            if not chunk: break
            count += len(chunk); need(count <= row.size_bytes)
            hashed_value.update(chunk); chunks.append(chunk)
        after = os.fstat(fd)
        stable = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        need(stable(before) == stable(after) and count == row.size_bytes and hashed_value.hexdigest() == row.sha256)
        return b''.join(chunks)
    finally:
        for fd in reversed(handles): os.close(fd)


def format_check(content, media_type, *, parse_proof=False):
    """Valid/invalid/unknown technical format only; no model or subprocess."""
    try:
        if media_type in {'image/png', 'image/jpeg'}:
            from PIL import Image
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('error', Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(content)) as image:
                        expected = 'PNG' if media_type == 'image/png' else 'JPEG'
                        if image.format != expected or image.width * image.height > MAX_PIXELS: return 'invalid', 'bounded_image_parser'
                        image.verify()
                    # JPEG verify alone does not fully decode its pixel data.
                    with Image.open(io.BytesIO(content)) as image: image.load()
            except (ValueError, OSError, SyntaxError, Image.DecompressionBombError, Warning):
                return 'invalid', 'bounded_image_parser'
            return 'valid', 'bounded_image_parser'
        if media_type in {'audio/wav', 'audio/x-wav', 'audio/wave'}:
            with wave.open(io.BytesIO(content), 'rb') as source:
                if not (1 <= source.getnchannels() <= 8 and 1 <= source.getsampwidth() <= 4
                        and 1 <= source.getframerate() <= 192000 and 0 < source.getnframes()
                        and source.getnframes() <= source.getframerate() * 600): return 'invalid', 'bounded_pcm_reader'
                expected = source.getnframes() * source.getnchannels() * source.getsampwidth()
                if len(source.readframes(source.getnframes())) != expected: return 'invalid', 'bounded_pcm_reader'
            return 'valid', 'bounded_pcm_reader'
        if media_type in {'text/plain', 'text/markdown', 'text/csv', 'application/json', 'application/geo+json'}:
            value = content.decode('utf-8-sig')
            if not value.strip() or '\x00' in value: return 'invalid', 'bounded_utf8_reader'
            if media_type in {'application/json', 'application/geo+json'}:
                def pairs(items):
                    result = {}
                    for key, item in items:
                        if key in result: raise ValueError('duplicate_json_key')
                        result[key] = item
                    return result
                def constant(_): raise ValueError('nonfinite_json')
                json.loads(value, object_pairs_hook=pairs, parse_constant=constant)
            elif media_type == 'text/csv':
                rows = csv.reader(io.StringIO(value), strict=True); width = None; count = 0
                for row in rows:
                    count += 1
                    if count > 100000 or not row or width is not None and len(row) != width: return 'invalid', 'bounded_csv_reader'
                    width = len(row)
                if not count: return 'invalid', 'bounded_csv_reader'
            return 'valid', 'bounded_utf8_reader'
        if parse_proof and media_type in {'application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}:
            return 'valid', 'original_saved_native_parse_proof'
        return 'unknown', 'no_full_format_proof'
    except ImportError:
        return 'unknown', 'format_parser_unavailable'
    except (ValueError, OSError, EOFError, UnicodeError, csv.Error, wave.Error, SyntaxError, RecursionError, Warning):
        return 'invalid', 'format_parser_rejected'


def inspect_files(rows, *, root, workspace_id, source_issues=()):
    """Pure/file-system boundary: rows must already be authorized by inventory()."""
    problems, files, used = copy.deepcopy(list(source_issues)), [], 0
    if len(rows) > MAX_FILES:
        return [], [*problems, issue('source_limit', blocked=True)]
    if not rows: problems.append(issue('empty_source', blocked=True))
    for selected in rows:
        row = selected['row']
        result = {'evidence_id': row.id, 'media_type': row.media_type,
            'expected_sha256': row.sha256, 'actual_sha256': None, 'size_bytes': None,
            'hash_verified': False, 'format_verified': False, 'format_method': 'not_checked',
            'source_refs': copy.deepcopy(selected['refs'])}
        try:
            content = read_original(root, workspace_id, row, remaining=MAX_TOTAL_BYTES - used)
            used += len(content); actual = hashlib.sha256(content).hexdigest()
            result.update(actual_sha256=actual, size_bytes=len(content), hash_verified=True)
            status, method = format_check(content, row.media_type, parse_proof=selected.get('parse_proof') is True)
            result.update(format_verified=status == 'valid', format_method=method)
            if status != 'valid': problems.append(issue('invalid_format' if status == 'invalid' else 'unsupported_format', row.id, blocked=status == 'invalid'))
        except (HTTPException, OSError):
            problems.append(issue('unreadable_original', row.id, blocked=True))
        files.append(result)
    return files, problems


def grouped(files):
    groups = {}
    for file in files:
        if not file['hash_verified']: continue
        key = (file['actual_sha256'], file['size_bytes'])
        groups.setdefault(key, set()).add(file['evidence_id'])
    values = [{'sha256': key[0], 'size_bytes': key[1], 'evidence_ids': sorted(ids)} for key, ids in sorted(groups.items())]
    return values, [copy.deepcopy(row) for row in values if len(row['evidence_ids']) > 1]


def attributes(files, issues, duplicates):
    return {'file_count': len(files), 'hash_verified_count': sum(row['hash_verified'] for row in files),
        'format_verified_count': sum(row['format_verified'] for row in files), 'duplicate_group_count': len(duplicates),
        'technical_review_required': int(bool(issues)),
        'integrity_blocked': int(any(row['severity'] == 'blocked' for row in issues))}


def inventory(db, p, task):
    """Task frozen inputs only. No workspace-wide file enumeration or client IDs."""
    from .entity_release_compat import require_entity
    instance = scoped(db, DomainInstance, task.data['instance_id'], p)
    release = scoped(db, InstanceRelease, task.data['instance_release_id'], p)
    need(instance.id == release.instance_id)
    snapshot = release.data.get('snapshot') if type(release.data) is dict else None
    businesses = snapshot.get('businesses') if type(snapshot) is dict else None
    need(type(businesses) is list and bool(businesses)
        and all(type(value) is dict and type(value.get('id')) is str and bool(value['id'].strip())
            for value in businesses)
        and len({value['id'] for value in businesses}) == len(businesses),
        '原任务实例版本缺少有效业务快照，未核验原件；请核对已保存版本')
    business = next((value for value in businesses if value['id'] == task.data.get('business_id')), None)
    need(business is not None, '原任务业务不在其保存的实例版本快照中，未核验原件')
    entity_ids = task.data.get('entity_ids')
    need(type(entity_ids) is list and 0 < len(entity_ids) <= 1000 and len(set(entity_ids)) == len(entity_ids))
    entities = []
    for identity in entity_ids:
        entity = scoped(db, Entity, identity, p)
        definition = require_entity(db, p, instance, release, entity, lock_sources=False)
        entities.append({'id': entity.id, 'version': entity.version, 'data_hash': digest(entity.data),
            'definition_hash': digest(definition)})
    selected, problems, covered = {}, [], []

    def add(evidence_id, kind, identity, snapshot, *, expected_sha=None, parse_proof=False, name=None):
        row = scoped(db, Evidence, evidence_id, p)
        need(row.workspace_id == task.workspace_id == p.workspace_id
            and (expected_sha is None or row.sha256 == expected_sha))
        ref = {'kind': kind, 'id': identity, 'selection_hash': digest(snapshot)}
        if row.id not in selected:
            selected[row.id] = {'row': row, 'name': str(name or row.data.get('name') or '原件')[:250],
                'refs': [], 'parse_proof': False}
        item = selected[row.id]
        if ref not in item['refs']: item['refs'].append(ref)
        item['parse_proof'] = item['parse_proof'] or parse_proof
        need(len(selected) <= MAX_FILES, '本任务原件超过安全核验数量，未截取后当作完整', 422)

    groups = {'image_snapshots': 'image', 'material_snapshots': 'material',
        'audio_snapshots': 'audio', 'scene_snapshots': 'scene'}
    for key, kind in groups.items():
        values = task.data.get(key, [])
        need(type(values) is list and len(values) <= MAX_FILES)
        if not values: continue
        covered.append(kind)
        for snapshot in values:
            try:
                need(type(snapshot) is dict)
                if kind == 'image':
                    from .workflow_images import view
                    row = scoped(db, Evidence, snapshot['id'], p)
                    need(same(view(row), snapshot) and row.data.get('instance_id') == instance.id
                        and row.data.get('purpose') == 'workflow_input_image')
                    add(row.id, kind, row.id, snapshot, expected_sha=snapshot['sha256'])
                elif kind == 'material':
                    from .instance_compiler import verify_material_snapshots
                    material = scoped(db, SetupMaterial, snapshot['id'], p)
                    need(material.evidence_id == snapshot['evidence_id'] and material.sha256 == snapshot['hash'])
                    # This is the EXISTING cold full-source verification. The
                    # new checker does not replace it by a parsed status flag.
                    verify_material_snapshots(db, p, [snapshot], allow_archived=True)
                    native_full = bool(snapshot.get('business_flow_execution_sources') or snapshot.get('report_sources'))
                    add(material.evidence_id, kind, material.id, snapshot, expected_sha=snapshot['hash'],
                        parse_proof=native_full, name=material.name)
                    if material.status != 'parsed' or material.data.get('warnings'):
                        problems.append(issue('source_partial', material.evidence_id))
                elif kind == 'audio':
                    from . import workflow_audio
                    row = workflow_audio.checked_input(db, p, snapshot['id'])
                    need(row.instance_id == instance.id and same(workflow_audio.view(row), snapshot))
                    source = snapshot['snapshot']
                    add(source['evidence_id'], kind, row.id, snapshot, expected_sha=source['input_sha256'], name=source['name'])
                else:
                    from . import workflow_scenes
                    row = workflow_scenes.checked_input(db, p, snapshot['id'])
                    need(row.instance_id == instance.id and same(workflow_scenes.view(row), snapshot))
                    source = snapshot['snapshot']
                    add(source['original_evidence_id'], kind, row.id, snapshot,
                        expected_sha=source['input_sha256'], name=source['name'])
            except (HTTPException, ValueError, KeyError, TypeError, AttributeError):
                problems.append(issue('source_changed', field=key, blocked=True))
    # Other declared original modalities must not silently vanish into an
    # apparently complete image-only report. They remain explicitly unverified.
    # object_snapshots is the Task's entity context, validated above through
    # require_entity, not an additional file modality. It remains part of the
    # frozen_snapshots source hash below, alongside the actual entity hash.
    for key, value in task.data.items():
        if key.endswith('_snapshots') and key not in groups and key != 'object_snapshots' and value:
            problems.append(issue('unsupported_source', field=key))
    from . import runtime_task_documents as documents, runtime_documents as native
    from .workflow_document_packet_v207 import approved_set
    from .workflow_graph import graph_for
    graph = graph_for(release, business)
    ports = documents.ports(graph)
    if ports: covered.append('approved_document_source_set')
    for port in ports:
        try:
            reference = task.data['graph_runtime']['inputs'][port['key']]
            source_set, _ = approved_set(db, p, task, reference)
            for entry in source_set.data['entries']:
                selector = entry['selector']
                version = scoped(db, native.Version, selector['version_id'], p)
                need(version.instance_id == instance.id)
                native.checked_version(db, p, version)
                add(version.evidence_id, 'document', version.id,
                    {'source_set_hash': source_set.source_set_hash, 'selector': selector,
                        'version_origin_hash': entry['version_origin_hash']},
                    expected_sha=version.data['origin']['raw_sha256'], name=version.name,
                    parse_proof=selector['kind'] == 'native' and entry['partial'] is False)
                if entry['partial']: problems.append(issue('source_partial', version.evidence_id))
        except (HTTPException, ValueError, KeyError, TypeError, AttributeError):
            problems.append(issue('source_changed', field=port['key'], blocked=True))
    rows = [selected[key] for key in sorted(selected)]
    # Include the whole selected provenance, not permission lists or the Task
    # version, which normally changes as subsequent nodes complete.
    source_hash = digest({'selected': [{'id': item['row'].id, 'sha256': item['row'].sha256,
        'size_bytes': item['row'].size_bytes, 'media_type': item['row'].media_type,
        'refs': item['refs']} for item in rows],
        'frozen_snapshots': {key: value for key, value in task.data.items() if key.endswith('_snapshots')},
        'document_refs': {port['key']: task.data.get('graph_runtime', {}).get('inputs', {}).get(port['key']) for port in ports},
        'source_issues': problems})
    return rows, problems, sorted(covered), source_hash, digest(entities)


def checked_output(output):
    validate(output_schema(), output)
    report = output['report']
    need(report['report_hash'] == digest({k: v for k, v in report.items() if k != 'report_hash'})
        and all(same(output[key], report[key]) for key in ('quality_status', 'attributes', 'files', 'issues', 'duplicate_groups', 'digest_groups')))
    identities = [row['evidence_id'] for row in report['files']]
    need(len(identities) == len(set(identities)))
    for row in report['files']:
        need(bool(row['source_refs']) and len({digest(ref) for ref in row['source_refs']}) == len(row['source_refs']))
        if row['hash_verified']:
            need(row['actual_sha256'] == row['expected_sha256']
                and type(row['size_bytes']) is int and row['size_bytes'] > 0)
        else:
            need(row['actual_sha256'] is None and row['size_bytes'] is None and row['format_verified'] is False)
    groups, duplicates = grouped(report['files'])
    need(groups == report['digest_groups'] and duplicates == report['duplicate_groups']
        and same(report['attributes'], attributes(report['files'], report['issues'], duplicates)))
    expected = 'blocked' if any(row['severity'] == 'blocked' for row in report['issues']) else 'needs_review' if report['issues'] else 'pass'
    need(report['quality_status'] == expected and (bool(report['files']) or expected == 'blocked'))
    if expected == 'pass': need(all(row['hash_verified'] and row['format_verified'] for row in report['files']))
    return copy.deepcopy(output)


def receipt_audit_data(call, job, output):
    output = checked_output(output); producer = output['report']['producer']
    need(call.status == 'succeeded' and job.kind == graph_scope.KIND
        and producer['workspace_id'] == call.workspace_id == job.workspace_id
        and producer['invocation_id'] == call.id and producer['job_id'] == job.id
        and producer['task_id'] == job.data.get('task_id') and producer['activation_id'] == job.data.get('activation_id')
        and producer['actor_id'] == job.data.get('actor_id') and producer['capability_id'] == call.capability_id
        and producer['capability_hash'] == call.data.get('capability_hash')
        and producer['graph_hash'] == job.data.get('graph_hash')
        and call.data.get('job_id') == job.id and call.data.get('role') == 'data'
        and producer['input_hash'] == call.data.get('input_hash') == job.data.get('input_hash')
        and producer['request_key_hash'] == digest(call.idempotency_key)
        and call.idempotency_key == job.data.get('invocation_key') and same(call.data.get('output'), output))
    return {'schema': 'workflow-originals-produced-proof-v219', 'task_id': producer['task_id'],
        'job_id': job.id, 'invocation_id': call.id, 'activation_id': producer['activation_id'],
        'node_id': producer['node_id'], 'capability_id': call.capability_id,
        'capability_hash': producer['capability_hash'], 'input_hash': producer['input_hash'], 'output_hash': digest(output)}


def mark_produced(db, p, call, job, output):
    """ONLY the gateway's first real successful outcome transaction may call."""
    need(call.workspace_id == p.workspace_id and output['report']['producer']['actor_id'] == p.user_id)
    proof = receipt_audit_data(call, job, output)
    prior = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == PRODUCED, Audit.object_id == call.id)))
    need(not prior, '原质检回执已有独立记录，未重复补造来源证明')
    audit(db, p, PRODUCED, call.id, proof)


def _checked_original_report(db, p, task, report, *, operation):
    validate(report_schema(), report)
    need(report['operation'] == operation
        and report['report_hash'] == digest({k: v for k, v in report.items() if k != 'report_hash'}))
    producer = report['producer']
    need(producer['workspace_id'] == p.workspace_id == task.workspace_id and producer['task_id'] == task.id)
    call = scoped(db, Invocation, producer['invocation_id'], p)
    job = scoped(db, Job, producer['job_id'], p)
    need(call.status == 'succeeded' and job.status in {'succeeded', 'running'} and call.data.get('role') == 'data')
    original = checked_output(call.data.get('output'))
    need(same(original['report'], report))
    actual_task, _, _, graph, state, activation, node = graph_scope._frozen(db, p, task.id, producer['activation_id'])
    graph_scope._job_matches(actual_task, job, state, activation, node)
    need(node['id'] == producer['node_id'] and node['kind'] == 'ability'
        and node['actor_id'] == producer['actor_id']
        and node['execution']['agent_role'] == 'data'
        and node['execution']['adapter'] == 'builtin'
        and node['execution']['capability_id'] == call.capability_id
        and node['execution']['capability_hash'] == producer['capability_hash']
        and graph['nodes'] and state['graph_hash'] == producer['graph_hash']
        and activation['status'] == 'completed' and same(activation['output'], original))
    source_cap = scoped(db, Capability, call.capability_id, p)
    need(source_cap.content_hash == producer['capability_hash'] == digest(source_cap.data)
        and source_cap.data.get('execution', {}).get('handler') == (QUALITY if operation == 'quality' else DEDUP))
    proof = receipt_audit_data(call, job, original)
    anchors = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == PRODUCED, Audit.object_id == call.id)))
    need(len(anchors) == 1 and anchors[0].actor_id == producer['actor_id'] and anchors[0].data == proof)
    completed = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == 'workflow.graph.node_completed', Audit.object_id == task.id)))
    matched = [row for row in completed if row.data.get('activation_id') == producer['activation_id']]
    need(len(matched) == 1 and matched[0].actor_id == producer['actor_id']
        and matched[0].data == {'activation_id': producer['activation_id'], 'invocation_id': call.id})
    return report, job, graph, node


def checked_quality_report(db, p, task, report):
    report, job, _, _ = _checked_original_report(db, p, task, report, operation='quality')
    if job.status == 'running':
        from .workflow_task_context_v196 import ProducerPending
        raise ProducerPending(job.id, report['producer']['invocation_id'])
    return report


def checked_dedup_report(db, p, task, report):
    """Original dedup result AND its genuine quality input, never caller claims."""
    report, job, graph, node = _checked_original_report(db, p, task, report, operation='deduplicate')
    inputs = job.data['inputs']; validate(input_schema(DEDUP), inputs)
    previous = checked_quality_report(db, p, task, inputs['quality_report'])
    ref = graph['references'].get(node['execution']['input_bindings'].get('quality_report'))
    need(type(ref) is dict and ref.get('source') == 'output' and ref.get('kind') == 'object'
        and ref.get('field') == 'report' and ref.get('nullable') is False
        and ref.get('node_id') == previous['producer']['node_id']
        and 'quality_report' not in node['execution']['arguments']
        and report['source_inventory_hash'] == previous['source_inventory_hash']
        and report['entity_context_hash'] == previous['entity_context_hash'])
    if job.status == 'running':
        from .workflow_task_context_v196 import ProducerPending
        raise ProducerPending(job.id, report['producer']['invocation_id'])
    return report


def _bound_quality_report(db, p, task, job, inputs):
    """Both preflight and execute require the real upstream output, not a literal."""
    validate(input_schema(DEDUP), inputs)
    actual_task, _, _, graph, state, activation, node = graph_scope._frozen(
        db, p, task.id, job.data['activation_id'])
    graph_scope._job_matches(actual_task, job, state, activation, node)
    need(task.workspace_id == p.workspace_id == job.workspace_id and job.data.get('task_id') == task.id
        and node['actor_id'] == p.user_id and same(job.data['inputs'], inputs))
    ref = graph['references'].get(node['execution']['input_bindings'].get('quality_report'))
    need(type(ref) is dict and ref.get('source') == 'output' and ref.get('kind') == 'object'
        and ref.get('field') == 'report' and ref.get('nullable') is False
        and ref.get('node_id') == inputs['quality_report']['producer']['node_id']
        and 'quality_report' not in node['execution']['arguments'],
        '去重必须消费本图真实前置技术质检回执，不能用客户输入或常量代替')
    return inputs['quality_report']


def producer_wait_pin(db, p, task, job, inputs):
    """Original wait-family hook. No Invocation creation, locks, or dispatch."""
    report = _bound_quality_report(db, p, task, job, inputs)
    checked_quality_report(db, p, task, report)
    return copy.deepcopy(report['producer'])


def execute(db, p, cap, inputs, key):
    manifest = validate_manifest(cap.data); handler = manifest['execution']['handler']
    validate(input_schema(handler), inputs)
    task, job, node, scope = graph_scope.graph_context(db, p, key, cap)
    need(node['execution']['agent_role'] == 'data' and job.data['inputs'] == inputs)
    previous = checked_quality_report(db, p, task, _bound_quality_report(db, p, task, job, inputs)) if handler == DEDUP else None
    rows, source_issues, covered, source_hash, entity_hash = inventory(db, p, task)
    if previous is not None:
        need(previous['source_inventory_hash'] == source_hash and previous['entity_context_hash'] == entity_hash,
            '前置质检后原件库存或对象版本已变化，未将旧报告当作当前去重依据')
    files, issues = inspect_files(rows, root=settings().storage_dir, workspace_id=p.workspace_id, source_issues=source_issues)
    groups, duplicates = grouped(files)
    status = 'blocked' if any(row['severity'] == 'blocked' for row in issues) else 'needs_review' if issues else 'pass'
    call = db.scalar(select(Invocation).where(Invocation.workspace_id == p.workspace_id, Invocation.idempotency_key == key))
    need(call is not None and call.status == 'running' and call.capability_id == cap.id)
    producer = {'workspace_id': p.workspace_id, 'task_id': task.id, 'job_id': job.id,
        'invocation_id': call.id, 'capability_id': cap.id, 'actor_id': p.user_id,
        'activation_id': job.data['activation_id'], 'node_id': node['id'],
        'capability_hash': cap.content_hash, 'graph_hash': scope['graph_hash'],
        'input_hash': job.data['input_hash'], 'request_key_hash': digest(key)}
    report = {'schema': REPORT, 'operation': HANDLERS[handler], 'producer': producer,
        'source_inventory_hash': source_hash, 'entity_context_hash': entity_hash,
        'quality_status': status, 'attributes': attributes(files, issues, duplicates), 'files': files, 'issues': issues, 'duplicate_groups': duplicates,
        'digest_groups': groups, 'covered_source_kinds': covered, **FLAGS}
    report['report_hash'] = digest(report)
    result = {'schema': RESULT, **{key: copy.deepcopy(report[key]) for key in
        ('quality_status', 'attributes', 'files', 'issues', 'duplicate_groups', 'digest_groups')}, 'report': report, **FLAGS}
    return checked_output(result)
