"""Task-aware historical reference provenance and separate current-use gates."""
import copy
from contextlib import contextmanager
from dataclasses import dataclass, field
from sqlalchemy import select, or_
from . import models as m, runtime_documents as native
from . import layered_case_origin_v209 as accepted
from . import layered_cases_v209 as cases
from .services import scoped
from .contracts import digest
from .layered_case_use_models_v210 import LayeredCaseUseV210 as Use
from .layered_case_use_models_v210 import LayeredCaseUseCommandV210 as Command

KEY = 'case_use_origin_v210'
GENERATED = 'layered_case_use.generated_v210'
need = accepted.need
_WALK = 'layered_case_use_verification_v210'


@dataclass
class Walk:
    stack: list[str] = field(default_factory=list)
    nodes: set[str] = field(default_factory=set)
    memo: dict = field(default_factory=dict)

    @contextmanager
    def entering(self, identity):
        need(identity not in self.stack, '案例来源出现循环引用；未跳过原错误')
        need(len(self.stack) < 16 and (identity in self.nodes or len(self.nodes) < 128),
            '案例历史超过16层或128个采用节点；请核对来源链，未跳过原证明')
        self.stack.append(identity); self.nodes.add(identity)
        try: yield self
        finally: self.stack.pop()


@contextmanager
def verification_scope(db):
    old = db.info.get(_WALK)
    if old is not None:
        yield old; return
    walk = Walk(); db.info[_WALK] = walk
    try: yield walk
    finally: db.info.pop(_WALK, None)


def approved_case(db, p, identity):
    row = scoped(db, cases.Case, identity, p)
    head, _ = cases.inventory(db, p, row)
    need(row.status == 'approved', '请明确选择已独立批准的案例', 422)
    state = cases.checked(row)
    commands = list(db.scalars(select(cases.Command).where(cases.Command.workspace_id == p.workspace_id,
        cases.Command.case_id == row.id)))
    by_id = {item.id: item for item in commands}; final = by_id[state['command_ids'][-1]]
    receipt = cases.checked_command(db, p, final)
    body, _, _ = cases.parse(final.exact_body.encode(), 'review')
    need(final.operation == 'review' and body.decision == 'approve'
        and receipt['result']['status'] == 'approved' and body.revision_id == head.id
        and body.revision_hash == head.revision_hash and final.actor_id != row.author_id)
    frozen = receipt['result']['case']
    reference = {'id': row.id, 'version': row.version, 'revision_id': head.id,
        'revision_hash': head.revision_hash, 'source_fingerprint': row.source_fingerprint,
        'business_name': head.data['source']['business_name'], 'author': copy.deepcopy(frozen['author']),
        'value': copy.deepcopy(head.data['value'])}
    pin = {'case_id': row.id, 'case_version': row.version, 'revision_id': head.id,
        'revision_hash': head.revision_hash, 'source_fingerprint': row.source_fingerprint,
        'approval_command_id': final.id, 'approval_command_hash': digest(final.data),
        'approval_body_sha256': final.body_sha256, 'case_author_id': row.author_id}
    return row, pin, reference


def target_pin(db, p, task):
    from .runtime_task_documents import task_context
    release, graph, context = task_context(db, p, task)
    need(release.data['snapshot'].get('semantic_runtime_plan', {}).get('schema') == 'instance-authored-workflow-runtime-v208',
        '当前案例参考需要明确人工编制的运行资料业务图', 422)
    business = next(item for item in release.data['snapshot']['businesses'] if item['id'] == task.data['business_id'])
    target = {'task_id': task.id, 'title': task.title, 'business_name': business['name'],
        'instance_id': task.data['instance_id'], 'release_id': release.id}
    pin = {'task_id': task.id, 'owner_id': task.owner_id, 'creation_hash': task.data.get('creation_hash'),
        'context_hash': digest(accepted.a.context(task)), 'release_id': release.id,
        'configuration_hash': release.configuration_hash, 'business_id': business['id'],
        'business_name': business['name'], 'instance_id': task.data['instance_id'], 'package_id': task.package_id}
    return target, pin, graph


def marker(row):
    return {'schema': 'layered-case-use-entry-v210', 'use_id': row.id, 'binding_hash': row.binding_hash,
        'request_id': row.creation_request_id, 'upload_request_id': row.data['binding']['upload_body']['request_id'],
        'target_task_id': row.target_task_id, 'version_id': row.document_version_id,
        'case_id': row.source_case_id, 'revision_id': row.data['binding']['source_pin']['revision_id'],
        'revision_hash': row.data['binding']['source_pin']['revision_hash']}


def generation_proof(row):
    binding = row.data['binding']
    return {'use_id': row.id, 'target_task_id': row.target_task_id, 'source_case_id': row.source_case_id,
        'version_id': row.document_version_id, 'evidence_id': binding['document']['evidence_id'],
        'job_id': binding['document']['job_id'],
        'binding_hash': row.binding_hash, 'request_id': row.creation_request_id,
        'upload_request_id': binding['upload_body']['request_id'], 'raw_sha256': binding['upload_body']['file_sha256']}


def checked_use(db, p, row, *, walk=None):
    with verification_scope(db) as shared:
        walk = walk or shared
        with walk.entering(row.id):
            from . import layered_case_uses_v210 as uses
            # Only a completely checked immutable proof is memoized, within
            # this explicit scope and transaction. Entering checks cycles even
            # for a cached node. A changed row/version invalidates this key.
            row_value = uses.checked(row)
            cache_key = (db.get_transaction(), row.id, row.version, row.binding_hash, digest(row_value))
            if cache_key in walk.memo: return copy.deepcopy(walk.memo[cache_key])
            uses.inventory(db, p, row, verify_origin=False)
            binding = row.data['binding']
            need(row.workspace_id == p.workspace_id and row.binding_hash == digest(binding)
                and binding['schema'] == 'layered-case-use-binding-v210'
                and binding['use_id'] == row.id and binding['author_id'] == row.author_id
                and binding['target_pin']['task_id'] == row.target_task_id
                and binding['source_pin']['case_id'] == row.source_case_id
                and binding['document']['version_id'] == row.document_version_id)
            task = scoped(db, m.Task, row.target_task_id, p)
            _, actual_target, _ = target_pin(db, p, task)
            need(actual_target == binding['target_pin'] and task.owner_id == row.author_id)
            source, source_pin, reference = approved_case(db, p, row.source_case_id)
            need(source.task_id != task.id and source_pin == binding['source_pin']
                and reference == binding['case'], '原已批准案例或固定修订已变化，未借用当前内容')
            version = scoped(db, native.Version, row.document_version_id, p)
            raw = native.checked_version(db, p, version)
            evidence = scoped(db, m.Evidence, version.evidence_id, p)
            job = scoped(db, m.Job, binding['document']['job_id'], p)
            expected_raw = uses.reference_bytes(binding['case'], binding['target'], binding['applicable_reason'])
            need(raw == expected_raw and version.document_id == binding['document']['document_id']
                and version.evidence_id == binding['document']['evidence_id'] and version.actor_id == row.author_id
                and version.instance_id == task.data['instance_id'] and version.release_id == task.data['instance_release_id']
                and version.data.get(KEY) == evidence.data.get(KEY) == marker(row)
                and version.data['origin']['request_hash'] == digest(binding['upload_body'])
                and version.data['origin']['raw_sha256'] == binding['upload_body']['file_sha256']
                and version.name == binding['upload_body']['file_name'] == uses.file_name(binding['case'])
                and version.data['origin']['job_id'] == job.id and job.kind == 'parse_runtime_document'
                and job.idempotency_key == 'runtime-document-parse:' + version.id
                and job.data.get('runtime_document_id') == version.id
                and binding['upload_result']['id'] == version.document_id
                and binding['upload_result']['latest_version_id'] == binding['upload_result']['latest']['id'] == version.id
                and binding['upload_result']['latest']['document_id'] == version.document_id
                and binding['upload_result']['latest']['raw_sha256'] == binding['upload_body']['file_sha256'])
            # Explicit native retry may legitimately change the original Job's
            # current actor; its separate AUTH/command audits retain authority.
            native.authorization(db, p, job, row.author_id)
            audits = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.action == native.COMMAND, m.Audit.object_id == binding['upload_body']['request_id'])))
            need(len(audits) == 1 and audits[0].actor_id == row.author_id
                and audits[0].data == {'scope': 'upload:' + task.data['instance_id'],
                    'body': binding['upload_body'], 'request_hash': digest(binding['upload_body']),
                    'result': binding['upload_result']})
            uses.audit_event(db, p, GENERATED, row.id, generation_proof(row), row.author_id)
            walk.memo[cache_key] = copy.deepcopy(binding)
            return copy.deepcopy(binding)


def family_use(db, p, selector):
    if selector.get('kind') not in {'native', 'ocr'}: return None
    version = scoped(db, native.Version, selector['version_id'], p)
    evidence = scoped(db, m.Evidence, version.evidence_id, p)
    rows = list(db.scalars(select(Use).where(Use.workspace_id == p.workspace_id,
        Use.document_version_id == version.id)))
    events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == GENERATED, m.Audit.data['version_id'].as_string() == version.id)))
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.data['state']['document_version_id'].as_string() == version.id)))
    if not rows and not events and not commands and KEY not in version.data and KEY not in evidence.data: return None
    need(len(rows) == len(events) == 1, '案例参考原件存在采用痕迹但Use或生成库存缺失；未降为普通资料')
    need(selector['kind'] == 'native', '案例生成TXT只能按其真实本地原文解析采用，不能改为其他模态来源')
    row = rows[0]
    need(events[0].object_id == row.id and commands, '案例参考的原成功命令库存缺失；未借用普通上传')
    return row


def history_entry(db, p, task, selector, entry):
    row = family_use(db, p, selector)
    if row is None: return entry
    need(task is not None and row.target_task_id == task.id,
        '案例参考只能用于其原目标任务；未跨任务借用采用授权')
    checked_use(db, p, row)
    return {**entry, KEY: marker(row)}


def checked_coverage(db, p, task, entries):
    from . import layered_case_uses_v210 as uses
    groups = {}
    for entry in entries:
        row = family_use(db, p, entry['selector'])
        if row is None:
            need(KEY not in entry, '案例参考标记缺少原采用库存'); continue
        need(task is not None and row.target_task_id == task.id and entry.get(KEY) == marker(row))
        groups.setdefault(row.id, (row, []))[1].append(entry)
    proofs = []
    for row, selected in groups.values():
        checked_use(db, p, row)
        attempt_ids = {entry['selector']['attempt_id'] for entry in selected}
        hashes = {entry['selector']['parse_hash'] for entry in selected}
        need(len(attempt_ids) == len(hashes) == 1, '同一案例参考不能拼接不同解析版本')
        ready = uses.native_reference(db, p, row, attempt_id=next(iter(attempt_ids)), parse_hash=next(iter(hashes)))
        need(ready['ready'] and sorted(digest(entry['selector']) for entry in selected)
            == sorted(digest(selector) for selector in ready['selectors']),
            '案例参考须完整采用全部五项值及限制；不可遗漏、重复或重叠摘选')
        proofs.append({'use_id': row.id, 'binding_hash': row.binding_hash, 'version_id': row.document_version_id,
            'source_set_entry_hash': digest(selected)})
    return sorted(proofs, key=lambda item: item['use_id'])


def current_entries(db, p, task, entries, *, lock_sources=True):
    proofs = checked_coverage(db, p, task, entries)
    for proof in proofs:
        query = select(Use).where(Use.workspace_id == p.workspace_id, Use.id == proof['use_id']).execution_options(populate_existing=True)
        if lock_sources: query = query.with_for_update(read=True)
        row = db.scalar(query); need(row is not None)
        need(row.status == 'active', '案例参考已撤回，不能用于新的审批或执行；原历史保留')
        managed = accepted.a.managed(db, p, task)
        need(managed is not None)
        _, _, spec = accepted.a.current_submission(db, p, managed) if managed.data.get('current_submission_id') else (
            None, None, accepted.a.checked(scoped(db, accepted.a.PolicyVersion, managed.policy_version_id, p))['spec'])
        policy = accepted.a.current_policy(db, p, managed.policy_id, lock_sources=lock_sources)
        _, current = accepted.a.policy_value(db, p, policy, current=True)
        need(current['scope_id'] == spec['scope_id'], '原目标业务组织范围已改变，不能取得新的采用权限')
        accepted.a.ancestors(db, p, spec['scope_id'], lock_sources=lock_sources)
    return proofs


def current_inputs(db, p, task, graph, values, *, lock_sources=True):
    from . import runtime_task_documents as sources
    proofs = []
    for port in sources.ports(graph):
        if port['key'] not in values: continue  # original validate_inputs owns deferred semantics
        reference = values[port['key']]
        row = scoped(db, sources.SourceSet, reference['source_set_id'], p)
        need(row.task_id == task.id and row.source_set_hash == reference['source_set_hash'])
        sources.checked_set(db, p, row, task=task, input_key=port['key'])
        proofs.extend(current_entries(db, p, task, row.data['entries'], lock_sources=lock_sources))
    return proofs
