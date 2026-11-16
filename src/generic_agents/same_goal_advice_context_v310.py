"""Local UI reads of actual task configuration and original prepared texts.

Permissions are present-tense UI hints, never dispatch/consent/spending grants.
No query, document, SDK, price registration or model call is created here.
"""
import copy
from typing import Literal

from fastapi import HTTPException
from pydantic import StrictBool, StrictInt, StrictStr

from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_store_v302 as store
from . import same_goal_advice_sources_v302 as sources
from . import same_goal_advice_preparation_v304 as preparations
from . import same_goal_advice_preview_v307 as previews
from .same_goal_advice_analysis_v305 import EvaluationGenerationRequired
from . import same_goal_advice_quota_v304 as quota
from . import same_goal_advice_price_v307 as prices
from . import workflow_goal_evaluation_packet_v288 as packets
from . import business_goal_ready_sources_v288 as originals
from . import workflow_evidence_consent_wait_v199 as consent
from . import runtime_task_documents as task_documents
from .contracts import digest
from .authored_goal_binding_parse_scope_v300 import authored_goal_binding_parse_scope as binding_structure_scope
from .goals import snapshot
from .gateway import ModelBlocked
from .models import Audit, Capability, Invocation, Job, Plan
from .workflow_goal_evaluation_models_v288 import GoalEvaluationV288 as Evaluation

CONTEXT = 'same-goal-advice-context-v310'
DOCUMENTS = 'same-goal-advice-documents-v310'
need = store.need


class Actor(c.Closed):
    role: StrictStr
    is_business_actor: StrictBool
    is_data_actor: StrictBool
    is_admin: StrictBool


class Permissions(c.Closed):
    can_prepare: StrictBool
    can_authorize: StrictBool
    can_preview: StrictBool
    can_start: StrictBool
    can_review: StrictBool
    can_configure_quota: StrictBool
    can_register_price: StrictBool


class Model(c.Closed):
    name: StrictStr
    provider: StrictStr
    model: StrictStr
    release_version: StrictStr
    available: StrictBool


class DocumentSource(c.Closed):
    selection: c.DocumentSource
    title: StrictStr
    description: StrictStr
    input_key: StrictStr
    excerpt_count: StrictInt


class Quota(c.Closed):
    status: StrictStr
    reference: c.Ref | None
    limits: dict | None
    expires_at: StrictStr | None


class Price(c.Closed):
    status: StrictStr
    reference: c.Ref | None
    currency: StrictStr | None
    checked_at: StrictStr | None
    expires_at: StrictStr | None


class Block(c.Closed):
    code: StrictStr
    message: StrictStr
    section: StrictStr


class Context(c.Closed):
    schema: Literal['same-goal-advice-context-v310']
    task_id: c.ID
    task_version: StrictInt
    title: StrictStr
    status: StrictStr
    goal_hash: c.Hash
    goal_version: StrictInt
    goal_purpose: StrictStr
    mature_head_hash: c.Hash | None
    data_as_of: StrictStr | None
    generation: StrictInt | None
    freshness: StrictStr
    document_sources: list[DocumentSource]
    model_pin: c.CapabilityPin | None
    model: Model | None
    actor: Actor
    quota: Quota
    price: Price
    permissions: Permissions
    blocked_reasons: list[Block]


class Grant(c.Closed):
    audit_id: c.ID
    audit_hash: c.Hash
    actor_id: c.ID
    action: StrictStr
    model_profile_id: c.ID
    evidence_sha256: c.Hash
    version_after: StrictInt | None
    reason: StrictStr | None


class Consent(c.Closed):
    status: StrictStr
    grants: list[Grant]


class TextDocument(c.Closed):
    role: Literal['data', 'business_document']
    title: StrictStr
    text: StrictStr
    evidence_ref: c.EvidenceRef
    version: StrictInt
    source_scope: Literal['evaluation_text', 'approved_excerpt']
    as_of: StrictStr
    consent: Consent


class Documents(c.Closed):
    schema: Literal['same-goal-advice-documents-v310']
    task_id: c.ID
    plan_id: c.ID
    plan_version: StrictInt
    data_as_of: StrictStr
    model_pin: c.CapabilityPin
    model: Model | None
    documents: list[TextDocument]
    restrictions: list[StrictStr]


def _try(blocks, code, message, section, read):
    try:
        return read()
    except EvaluationGenerationRequired as error:
        blocks.append({'code': 'evaluation_generation_required', 'message': error.detail, 'section': section})
        return None
    except (HTTPException, ModelBlocked) as error:
        if isinstance(error, HTTPException) and error.status_code not in {404, 409, 422}: raise
        blocks.append({'code': code, 'message': message, 'section': section})
        return None


def _model(db, p, pin):
    rows = store._rows(db, p, Capability, Capability.id == pin['id'])
    if not rows: return None, None
    need(len(rows) == 1)
    row = rows[0]
    if (row.kind != 'foundation_model' or store._pin(row) != pin
            or row.content_hash != row.validated_hash or digest(row.data) != row.content_hash):
        return row, None
    available = False
    if row.status == 'published':
        actual = quota._published(db, p, row.id, 'foundation_model')
        need(store._pin(actual) == pin)
        from .cloud_provider_policy import provider_enabled
        available = provider_enabled(row.data.get('execution', {}).get('provider'))
    return row, {'name': row.data['name'], 'provider': row.data['execution']['provider'],
        'model': row.data['execution']['model'], 'release_version': row.release_version,
        'available': available}


def _released(db, p, selected):
    nodes = {node['id']: node for node in selected['binding']['graph']['nodes']}
    business, data = nodes['reasoning'], nodes['packet']
    need(business['kind'] == data['kind'] == 'ability'
        and business['execution']['agent_role'] == 'business' and data['execution']['agent_role'] == 'data')
    source = store._fresh(db, p, Capability, business['execution']['capability_id'])
    need(source.kind == 'tool' and source.content_hash == source.validated_hash == digest(source.data)
        == business['execution']['capability_hash']
        and source.release_version == business['execution']['release_version'])
    model = store._fresh(db, p, Capability, source.data['execution']['model_profile_id'])
    need(model.content_hash == source.data['execution']['model_profile_hash'])
    pin = store._pin(model); _actual, display = _model(db, p, pin)
    return {'source': source, 'model': model, 'model_pin': pin, 'display': display,
        'business_actor': business['actor_id'], 'data_actor': data['actor_id']}


def _approved_sources(db, p, task, selected):
    approval = task_documents.approval
    managed = approval.managed(db, p, task)
    need(managed is not None)
    # A submitted SourceSet alone is not evidence that it was approved.
    approval.guard(db, p, task, record_actor=False, lock_sources=False)
    _submission, submission, _policy = approval.current_submission(db, p, managed)
    inputs = submission['value']['graph_inputs']; result = []
    for port in task_documents.ports(selected['binding']['graph']):
        reference = inputs.get(port['key'])
        if reference is None: continue
        selection = {'kind': 'approved_source_set',
            'source_set_ref': c._parse(c.SourceSetRef, reference)}
        original = sources.resolve_documents(db, p, task, selection)
        need(original['approved_selection']['input_key'] == port['key'])
        result.append({'selection': copy.deepcopy(selection), 'title': port.get('label') or port['key'],
            'description': '原任务逐级批准的资料摘录', 'input_key': port['key'],
            'excerpt_count': len(original['entries'])})
    need(result)
    return result


def _budget(db, p, task, configured):
    value = store._policy(db, p, task, configured['scope'], configured['source'],
        configured['advice'], configured['model'])
    event = store._fresh(db, p, Audit, value['quota_policy_ref']['id'])
    occupied = store._policy_starts(db, p, task, value)
    status = 'used' if any(state['calls'] for _plan, state in occupied) else 'allocated' if occupied else 'ready'
    return {'status': status, 'reference': copy.deepcopy(value['quota_policy_ref']),
        'limits': {key: copy.deepcopy(item) for key, item in value.items() if key != 'quota_policy_ref'},
        'expires_at': event.data['expires_at']}


def _price(db, p, pin):
    value = prices.current(db, p, pin)
    return {'status': 'ready', 'reference': value['basis_ref'], 'currency': value['price']['currency'],
        'checked_at': value['price']['checked_at'], 'expires_at': value['price']['expires_at']}


@binding_structure_scope()
@sources.checked
def context(db, p, task_id):
    with db.no_autoflush:
        store._reader(db, p)
        task = store.existing._task(db, p, task_id); goal = snapshot(task); blocks = []
        selected = _try(blocks, 'configuration_unavailable', '原任务的正式业务配置无法核对',
            'configuration', lambda: store.provider.binding(db, p, task))
        if selected is None and not blocks:
            blocks.append({'code': 'configuration_unavailable', 'message': '此任务没有正式发布的同目标建议配置', 'section': 'configuration'})
        released = _try(blocks, 'model_unavailable', '原任务固定的业务能力或模型尚不可用',
            'model', lambda: _released(db, p, selected)) if selected else None
        mature = _try(blocks, 'head_unavailable', '请先完成数据评估和目标核对', 'data',
            lambda: store.existing._mature_head(db, p, task, selected)) if selected else None
        approved = _try(blocks, 'documents_unavailable', '尚无可核对的逐级批准资料', 'documents',
            lambda: _approved_sources(db, p, task, selected)) if selected else None
        configured = None
        if mature:
            configured = _try(blocks, 'preparation_unavailable', '当前目标、发布岗位或业务配置不允许新建资料准备',
                'configuration', lambda: quota._configuration(db, p, task, {
                    'expected_goal_hash': goal['hash'], 'expected_mature_head_hash': mature['head']['head_hash']}))
        policy = _try(blocks, 'quota_unavailable', '业务建议额度尚未配置、已过期或无法核对', 'quota',
            lambda: _budget(db, p, task, configured)) if configured else None
        if policy and policy['status'] != 'ready':
            blocks.append({'code': 'quota_' + policy['status'],
                'message': '本次额度已分配给原分析申请或已使用，请查看原记录；未增加调用或退还额度', 'section': 'quota'})
        model_pin = released['model_pin'] if released else None
        model = released['display'] if released else None
        if model and not model['available']:
            blocks.append({'code': 'model_unavailable', 'message': '原任务绑定的云模型当前未启用，历史结果仍可查看', 'section': 'model'})
        price = _try(blocks, 'price_unavailable', '尚无当前有效的官方价格依据；本地准备不受此限制', 'price',
            lambda: _price(db, p, model_pin)) if model and model['available'] else None
        inventory = store._inventory(db, p, task)
        opened = [value for value in inventory if store._preparation_open(value[0])]
        if opened: blocks.append({'code': 'preparation_open', 'message': '请继续或取消原资料准备操作', 'section': 'preparations'})
        actor = {'role': p.role, 'is_admin': p.role == 'admin',
            'is_business_actor': bool(released and p.user_id == released['business_actor']
                or any(prep['business_actor_id'] == p.user_id for _plan, prep, _job in inventory)),
            'is_data_actor': bool(released and p.user_id == released['data_actor']
                or any(prep['data_actor_id'] == p.user_id for _plan, prep, _job in inventory))}
        writer = p.role in {'admin', 'operator'}
        business = writer and (actor['is_admin'] or actor['is_business_actor'])
        preparer = bool(released and store._prepare_actor_allowed(p,
            released['business_actor'], released['data_actor']))
        fresh = 'waiting'
        if mature:
            evidence = store.provider._evidence(mature['row'], mature['record'], quota._clock(db))
            fresh = evidence[store.provider.VIEW]['state']
            if fresh != 'current':
                blocks.append({'code': 'data_stale', 'message': '数据已超出目标时效，仍可查看和准备历史资料；分析前需要新数据', 'section': 'data'})
        can_preview = can_start = can_review = False
        for plan, prep, job in opened:
            if business and plan.status == 'awaiting_consent' and job.status == 'succeeded' and fresh == 'current':
                local = _try(blocks, 'documents_pending', '请先完成原资料准备并核对指定模型的授权',
                    'documents', lambda: documents(db, p, task.id, plan.id))
                consented = bool(local and len(local['documents']) == 2
                    and all(item['consent']['status'] == 'authorized' for item in local['documents']))
                frame = _try(blocks, 'preview_sources_unavailable', '原资料准备与当前目标结果或发布配置不一致，尚不能生成发送预览',
                    'data', lambda: previews.frames.build_current(db, p, task, plan)) if consented else None
                can_preview = bool(consented and frame is not None and price and policy and policy['status'] == 'ready')
                if can_preview:
                    from . import same_goal_advice_execution_v308 as execution
                    chain = previews._chain(db, p, task, plan, prep)
                    if chain:
                        live_preview = _try(blocks, 'preview_pending', '原发送预览需要重新核对资料、授权或价格',
                            'preparations', lambda: execution._live_preview(db, p, task, plan, prep,
                                chain[-1][0], quota._clock(db)))
                        can_start = live_preview is not None
        # Local preparation is allowed for the first mature evaluation, but
        # this version cannot build a model-send preview from it.  Tell the
        # user before they spend time preparing and authorizing documents.
        # An existing preparation can provide a more specific diagnostic.
        if mature and mature['row'].generation == 1 and not any(
                item['code'] == 'evaluation_generation_required' for item in blocks):
            blocks.append({'code': 'initial_evaluation_preview_limited',
                'message': '当前是首次目标评价，可以在本地整理资料；现版本暂不能据此生成模型发送预览。如需分析，请由数据岗位根据新增资料追加并完成评价，再使用新评价重新整理资料。已有记录保留。',
                'section': 'data'})
        # Paid-answer review is independent of today's source, head and price.
        for plan, prep, _job in inventory:
            if writer and (actor['is_admin'] or p.user_id == prep['business_actor_id']) and plan.status == 'ready_for_review':
                from .same_goal_advice_runtime_v308 import cold_result
                ready = _try(blocks, 'review_result_unavailable', '原回答或费用回执需要核查，尚不能审核',
                    'review', lambda: cold_result(db, p, task, plan))
                can_review = ready is not None
        permissions = {'can_prepare': bool(preparer and configured and policy and policy['status'] == 'ready' and approved and not opened),
            'can_authorize': bool(writer and model and model['available']), 'can_preview': can_preview,
            'can_start': can_start, 'can_review': can_review,
            'can_configure_quota': bool(actor['is_admin'] and configured),
            'can_register_price': bool(actor['is_admin'] and model and model['available'])}
        if not preparer: blocks.append({'code': 'prepare_role_required', 'message': '当前岗位不能为此业务准备分析资料', 'section': 'role'})
        return c._parse(Context, {'schema': CONTEXT, 'task_id': task.id, 'task_version': task.version,
            'title': task.title, 'status': task.status, 'goal_hash': goal['hash'], 'goal_version': goal['version'],
            'goal_purpose': goal['spec']['purpose'], 'mature_head_hash': mature['head']['head_hash'] if mature else None,
            'data_as_of': mature['record']['cut_at'] if mature else None,
            'generation': mature['row'].generation if mature else None, 'freshness': fresh,
            'document_sources': approved or [], 'model_pin': model_pin, 'model': model, 'actor': actor,
            'quota': policy or {'status': 'unavailable', 'reference': None, 'limits': None, 'expires_at': None},
            'price': price or {'status': 'unavailable', 'reference': None, 'currency': None, 'checked_at': None, 'expires_at': None},
            'permissions': permissions, 'blocked_reasons': blocks})


def _grant_details(db, p, file, model):
    result = []
    for reference in consent._grants(db, file, model):
        event = store._fresh(db, p, Audit, reference['audit_id']); value = event.data
        need(event.object_id == file.id and event.action in {'evidence.upload', 'evidence.cloud_authorize'}
            and consent._grant_hash(event) == reference['audit_hash']
            and value.get('sha256') == file.sha256)
        version = value.get('version')
        need(version is None or type(version) is int and 1 <= version <= file.version)
        reason = value.get('reason'); need(reason is None or type(reason) is str)
        result.append({'audit_id': event.id, 'audit_hash': reference['audit_hash'], 'actor_id': event.actor_id,
            'action': event.action, 'model_profile_id': model.id, 'evidence_sha256': file.sha256,
            'version_after': version, 'reason': reason})
    need(len(result) <= sources.MAX_GRANTS and len({item['audit_id'] for item in result}) == len(result))
    return result


def _text(db, p, ref, *, role, title, scope, as_of, model, available):
    file, raw = sources.evidence.evidence_bytes(db, p, ref['evidence_id'])
    need(file.workspace_id == p.workspace_id and c._parse(c.EvidenceRef, ref) == sources._text(file, raw)
        and type(file.version) is int and file.version >= 1)
    grants = _grant_details(db, p, file, model) if model is not None else []
    return {'role': role, 'title': title, 'text': raw.decode('utf-8'), 'evidence_ref': copy.deepcopy(ref),
        'version': file.version, 'source_scope': scope, 'as_of': as_of,
        'consent': {'status': 'model_unavailable' if not available else 'authorized' if grants else 'not_authorized',
            'grants': grants}}


def _data_original(db, p, task, prep):
    reference = prep['mature_head']['evaluation_ref']
    row = store._fresh(db, p, Evaluation, reference['id']); record = row.data
    need(row.task_id == task.id and row.record_hash == reference['record_hash']
        and record['original_query_ref'] == prep['original_query_ref']
        and record['evaluation_query_ref'] == prep['evaluation_query_ref']
        and record['cut_at'] == prep['evaluation_cut']
        and row.goal_snapshot_hash == prep['scope']['goal_snapshot_hash'])
    job = store._fresh(db, p, Job, row.job_id); sdk = store._fresh(db, p, Invocation, row.invocation_id)
    cap = store._fresh(db, p, Capability, sdk.capability_id)
    output = packets.saved_result(db, p, task, job, sdk, cap, job.data['inputs'])
    need(output['evaluation_ref'] == reference and output['packet']['evidence_id'] == row.evidence_id)
    _release, _package_hash, original = originals._data(db, p, task, output['packet'], live=False)
    need(original['packet_id'] == row.id and digest(original['evaluation_record']) == digest(record))
    return c._parse(c.EvidenceRef, record['evidence_ref'])


@binding_structure_scope()
@sources.checked
def documents(db, p, task_id, plan_id):
    with db.no_autoflush:
        store._reader(db, p)
        task = store.existing._task(db, p, task_id)
        plan = store._fresh(db, p, Plan, store.existing._id(plan_id))
        need(plan.task_id == task.id)
        prep, job = store._saved_base(db, p, task, plan)
        prepared = preparations._read(db, p, task, plan, prep, job)
        need(prepared is not None and prepared['content'] is not None)
        data_ref = _data_original(db, p, task, prep)
        document_ref = prepared['evidence_ref']; need(data_ref['evidence_id'] != document_ref['evidence_id'])
        model, display = _model(db, p, prep['model_pin'])
        available = bool(display and display['available'])
        receipt = store._fresh(db, p, Audit, prepared['receipt_ref']['id'])
        need(receipt.created_at is not None and receipt.created_at.tzinfo is not None)
        items = [_text(db, p, data_ref, role='data', title='数据评估资料', scope='evaluation_text',
            as_of=prep['evaluation_cut'], model=model, available=available),
            _text(db, p, document_ref, role='business_document', title='已批准业务资料摘录', scope='approved_excerpt',
                as_of=receipt.created_at.isoformat(),
                model=model, available=available)]
        need(items[1]['text'].encode('utf-8') == prepared['content'].decode('utf-8').encode('utf-8'))
        return c._parse(Documents, {'schema': DOCUMENTS, 'task_id': task.id, 'plan_id': plan.id,
            'plan_version': plan.version, 'data_as_of': prep['evaluation_cut'], 'model_pin': prep['model_pin'],
            'model': display, 'documents': items,
            'restrictions': [] if available else ['原固定模型当前不可用；历史文字仍可查看，尚未取得发送权限']})
