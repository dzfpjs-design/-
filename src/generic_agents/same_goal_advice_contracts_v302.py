"""Closed same-Task advice snapshots, never source or dispatch authority.

Preparation and execution are separate immutable Plan.data objects. Mutable
status, jobs and human reviews belong to the owner's sibling ledger. This
module performs no IO, clock read, model call or dynamic adapter loading.
"""
import copy
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, TypeAdapter, field_validator

from . import business_decision_contracts_v274 as base
from . import business_decision_contracts_v284 as opinions
from .contracts import Manifest, digest
from .workflow_goal_evaluation_contracts_v288 import QueryRef

ADAPTER = 'same_goal_advice_v302'
CONTRACT = 'same-goal-advice-contract-v302'
PREPARATION = 'same-goal-advice-preparation-v302'
PLAN = EXECUTION = 'same-goal-advice-execution-v302'
SOURCE = 'same-goal-advice-source-v302'
PREVIEW = 'same-goal-advice-preview-v302'
INPUT = 'same-goal-advice-input-v302'
CLOUD_INPUT = 'same-goal-advice-cloud-input-v302'
RESULT = 'same-goal-advice-result-v302'
PREPARE_KIND = 'prepare_same_goal_advice_v302'
SEND_KIND = 'generate_same_goal_advice_v302'
APPROVAL = 'same-goal-advice-approval-v302'
FLAGS = ('can_dispatch', 'permissions_verified', 'source_authenticity_verified',
    'current_outcome_verified', 'goal_completion_verified', 'data_reception_verified')
Closed, ID, Hash, Text = base.Closed, base.ID, base.Hash, base.Text
Version = Annotated[StrictStr, Field(pattern=r'^\d+\.\d+\.\d+$')]
Positive = Annotated[StrictInt, Field(ge=1)]


def need(condition, message='同目标建议固定合同、来源引用或快照摘要不一致'):
    if not condition: raise ValueError(message)


def _parse(model, raw):
    base._json(raw)
    return model.model_validate(copy.deepcopy(raw)).model_dump(mode='json')


def record_hash(raw):
    return digest({key: value for key, value in raw.items() if key != 'record_hash'})


class Ref(Closed):
    id: ID
    record_hash: Hash


class CapabilityPin(Closed):
    id: ID
    content_hash: Hash
    release_version: Version


class SourceSetRef(Closed):
    source_set_id: ID
    source_set_hash: Hash


class DocumentPacketRef(Closed):
    id: ID
    version: Positive
    evidence_id: ID
    sha256: Hash
    source_set_id: ID
    source_set_hash: Hash

    @field_validator('version')
    @classmethod
    def original_version(cls, value):
        need(value == 1); return value


class SelectedSet(Closed):
    kind: Literal['approved_source_set']
    source_set_ref: SourceSetRef


class SelectedPacket(Closed):
    kind: Literal['document_packet_v207']
    packet: DocumentPacketRef


DocumentSource = SourceRef = Annotated[SelectedSet | SelectedPacket, Field(discriminator='kind')]


class Prepare(Closed):
    request_id: ID
    expected_goal_hash: Hash
    expected_mature_head_hash: Hash
    document_source: DocumentSource | None = None
    reason: Text


class Start(Closed):
    request_id: ID
    expected_plan_version: Positive
    expected_goal_hash: Hash
    expected_mature_head_hash: Hash
    preview_hash: Hash
    cloud_authorized: Literal[True]
    reason: Text

    @field_validator('cloud_authorized', mode='before')
    @classmethod
    def explicit_boolean(cls, value):
        need(type(value) is bool and value is True); return value


class Review(Closed):
    request_id: ID
    expected_plan_version: Positive
    result_hash: Hash
    decision: Literal['accepted', 'rejected', 'needs_revision']
    reason: Text


class Cancel(Closed):
    request_id: ID
    expected_plan_version: Positive
    reason: Text


class ExecutionScope(Closed):
    workspace_id: ID
    task_id: ID
    analysis_plan_id: ID
    instance_id: ID
    release_id: ID
    package_id: ID
    business_id: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    goal_version: Positive
    goal_hash: Hash
    goal_snapshot_hash: Hash
    native_goal_context_hash: Hash
    source_binding_hash: Hash


class MatureHeadRef(Closed):
    head_hash: Hash
    evaluation_ref: Ref
    goal_check_job_id: ID
    memory_id: ID
    report_hash: Hash


class Budget(Closed):
    quota_policy_ref: Ref
    provider: Literal['qwen', 'deepseek']
    max_tokens: Annotated[StrictInt, Field(ge=128, le=8192)]
    max_request_bytes: Annotated[StrictInt, Field(ge=1, le=50000)]
    max_evidence_items: Annotated[StrictInt, Field(ge=1, le=2)]
    max_cost_minor_units: Positive
    currency: Literal['CNY', 'USD']
    max_actual_calls: Literal[1]
    reserved_calls: Literal[1]
    reserved_steps: Literal[1]
    goal_max_tool_calls: Positive
    goal_max_steps: Positive

    @field_validator('max_actual_calls', 'reserved_calls', 'reserved_steps', mode='before')
    @classmethod
    def exact_one(cls, value):
        need(type(value) is int and value == 1); return value


class EvidenceRef(Closed):
    evidence_id: ID
    sha256: Hash
    size_bytes: Annotated[StrictInt, Field(ge=1, le=20000)]
    media_type: Literal['text/plain']


class EvidencePin(EvidenceRef):
    version: Positive
    model_profile_id: ID


class ConsentPin(Closed):
    evidence_id: ID
    model_profile_id: ID
    audit_id: ID
    audit_hash: Hash


class ApprovedSelection(SourceSetRef):
    submission_id: ID
    submission_hash: Hash
    input_key: Annotated[StrictStr, Field(min_length=1, max_length=200)]


class DocumentRecord(Closed):
    selection: DocumentSource
    approved_selection: ApprovedSelection
    evidence_ref: EvidenceRef
    receipt_ref: Ref


class SourceRecord(Closed):
    schema: Literal['same-goal-advice-source-v302']
    plan_id: ID
    mature_head: MatureHeadRef
    original_query_ref: QueryRef
    evaluation_query_ref: QueryRef
    evaluation_cut: StrictStr
    evaluation_generation: Annotated[StrictInt, Field(ge=2)]
    data_evidence_ref: EvidenceRef
    document: DocumentRecord | None
    record_hash: Hash


class PreparationSnapshot(Closed):
    schema: Literal['same-goal-advice-preparation-v302']
    scope: ExecutionScope
    request: Prepare
    request_hash: Hash
    requested_by: ID
    data_actor_id: ID
    business_actor_id: ID
    original_query_ref: QueryRef
    mature_head: MatureHeadRef
    evaluation_query_ref: QueryRef
    evaluation_cut: StrictStr
    source_business_capability_ref: CapabilityPin
    advice_capability_ref: CapabilityPin
    model_pin: CapabilityPin
    instruction: Annotated[StrictStr, Field(min_length=1, max_length=4000, pattern=r'\S')]
    budget: Budget
    record_hash: Hash


class CloudScope(Closed):
    # Strict subset of V288's scope: no Task/Plan/Job/activation identity.
    goal_snapshot_hash: Hash
    entity_ids: list[ID] = Field(min_length=1, max_length=30)


class CloudDecisionInput(Closed):
    schema: Literal['same-goal-advice-cloud-input-v302']
    decision_intent: base.Intent
    scope: CloudScope
    requirements: list[base.Requirement] = Field(min_length=1, max_length=30)
    sources: list[base.Source] = Field(min_length=1, max_length=32)
    capabilities: list[base.Capability] = Field(min_length=1, max_length=24)
    candidates: list[base.Candidate] = Field(min_length=1, max_length=16)
    facts: list[base.Fact] = Field(max_length=128)
    known_conflicts: list[base.Conflict] = Field(max_length=32)


class TextPart(Closed):
    type: Literal['text']
    text: Annotated[StrictStr, Field(min_length=1, max_length=30000)]


class CloudRequest(Closed):
    question: Annotated[StrictStr, Field(min_length=1, max_length=4000, pattern=r'\S')]
    registered_instruction: Annotated[StrictStr, Field(min_length=1, max_length=4000, pattern=r'\S')]
    decision_input: CloudDecisionInput
    evidence_parts: list[TextPart] = Field(min_length=1, max_length=4)
    input_hash: Hash


class PreviewRecord(Closed):
    schema: Literal['same-goal-advice-preview-v302']
    plan_id: ID
    preparation_snapshot_hash: Hash
    source_hash: Hash
    advice_capability_ref: CapabilityPin
    model_pin: CapabilityPin
    budget: Budget
    evidence_pins: list[EvidencePin] = Field(min_length=1, max_length=2)
    consent_pins: list[ConsentPin] = Field(min_length=1, max_length=2)
    cloud_request: CloudRequest
    cloud_request_hash: Hash
    model_options_hash: Hash
    preview_hash: Hash


class ExecutionSnapshot(Closed):
    schema: Literal['same-goal-advice-execution-v302']
    scope: ExecutionScope
    preparation_snapshot_hash: Hash
    source_record: SourceRecord
    preview: PreviewRecord
    start_request: Start
    start_request_hash: Hash
    record_hash: Hash


PlanRecord = ExecutionSnapshot


class InvocationInput(Closed):
    schema: Literal['same-goal-advice-input-v302']
    plan_ref: Ref
    preview_hash: Hash


class AdviceResult(Closed):
    schema: Literal['same-goal-advice-result-v302']
    plan_ref: Ref
    preview_hash: Hash
    evaluation_ref: Ref
    input_hash: Hash
    model_value: opinions.ModelValue
    review_state: Literal['ready_for_review']
    can_dispatch: Literal[False]
    permissions_verified: Literal[False]
    source_authenticity_verified: Literal[False]
    current_outcome_verified: Literal[False]
    goal_completion_verified: Literal[False]
    data_reception_verified: Literal[False]


def input_schema(): return copy.deepcopy(InvocationInput.model_json_schema())
def output_schema(): return copy.deepcopy(AdviceResult.model_json_schema())
def model_value_schema(): return copy.deepcopy(opinions.ModelValue.model_json_schema())


def build_manifest(source_manifest, source_ref):
    """Server-only compiler input: actual released cap bytes and registry pin.

    This consistency check cannot authenticate publication or its Task binding.
    No client endpoint may accept these arguments as authority.
    """
    source = Manifest.model_validate(source_manifest).model_dump(mode='json')
    pin = _parse(CapabilityPin, source_ref)
    execution, spec = source['execution'], source['specification']
    need(pin['content_hash'] == digest(source) and pin['release_version'] == source['version']
        and source['kind'] == 'tool' and source['allowed_agent_roles'] == ['business']
        and set(execution) == {'adapter', 'model_profile_id', 'model_profile_hash', 'side_effect'}
        and execution['adapter'] == 'business_goal_ready_v288' and execution['side_effect'] is False
        and set(spec) == {'schema', 'instruction'} and spec['schema'] == 'business-goal-ready-contract-v288'
        and type(spec['instruction']) is str and bool(spec['instruction'].strip())
        and len(spec['instruction']) <= 4000 and source['data_policy'].get('cloud_allowed') is True
        and type(source['budget'].get('max_attempts')) is int and source['budget']['max_attempts'] == 1)
    TypeAdapter(ID).validate_python(execution['model_profile_id'])
    TypeAdapter(Hash).validate_python(execution['model_profile_hash'])
    need(source['dependencies'] == [execution['model_profile_id']] and pin['id'] != execution['model_profile_id'])
    required = source['input_schema'].get('required')
    need(type(required) is list and {'data_packet', 'document_packet'} <= set(required))
    result = {**source, 'namespace': 'same_goal_advice', 'name': 'advice_' + pin['id'].replace('-', ''),
        'version': '1.0.0', 'description': '同一正式目标的独立业务建议，仅供人工审核',
        'input_schema': input_schema(), 'output_schema': output_schema(),
        'execution': {**execution, 'adapter': ADAPTER},
        'dependencies': [pin['id'], execution['model_profile_id']],
        'specification': {'schema': CONTRACT, 'source_business_capability_ref': pin,
            'instruction': spec['instruction'], 'document_required': True}}
    return Manifest.model_validate(result).model_dump(mode='json')


def validate_manifest(manifest, *, source_manifest, source_ref):
    expected = build_manifest(source_manifest, source_ref)
    actual = Manifest.model_validate(manifest).model_dump(mode='json')
    need(base._json(actual) == base._json(expected)); return actual


def _manifest(manifest):
    data = Manifest.model_validate(manifest).model_dump(mode='json')
    e, s = data['execution'], data['specification']
    need(data['kind'] == 'tool' and data['allowed_agent_roles'] == ['business']
        and set(e) == {'adapter', 'model_profile_id', 'model_profile_hash', 'side_effect'}
        and e['adapter'] == ADAPTER and e['side_effect'] is False
        and set(s) == {'schema', 'source_business_capability_ref', 'instruction', 'document_required'}
        and s['schema'] == CONTRACT and s['document_required'] is True
        and type(s['instruction']) is str and 0 < len(s['instruction'].strip()) <= 4000
        and base._json(data['input_schema']) == base._json(input_schema())
        and base._json(data['output_schema']) == base._json(output_schema())
        and data['data_policy'].get('cloud_allowed') is True
        and type(data['budget'].get('max_attempts')) is int and data['budget']['max_attempts'] == 1)
    pin = _parse(CapabilityPin, s['source_business_capability_ref'])
    need(data['dependencies'] == [pin['id'], e['model_profile_id']])
    return data


def _cut(value):
    at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(at.tzinfo is not None and at.utcoffset() is not None)


def _check_preparation_model(raw, *, manifest, model):
    value = _parse(model, raw); cap = _manifest(manifest)
    need(value['record_hash'] == record_hash(value) and value['request_hash'] == digest(value['request'])
        and value['request']['expected_goal_hash'] == value['scope']['goal_hash']
        and value['request']['expected_mature_head_hash'] == value['mature_head']['head_hash']
        and value['source_business_capability_ref'] == cap['specification']['source_business_capability_ref']
        and value['advice_capability_ref']['content_hash'] == digest(cap)
        and value['advice_capability_ref']['release_version'] == cap['version']
        and value['model_pin']['id'] == cap['execution']['model_profile_id']
        and value['model_pin']['content_hash'] == cap['execution']['model_profile_hash']
        and value['instruction'] == cap['specification']['instruction']
        and value['original_query_ref']['query_id'] != value['evaluation_query_ref']['query_id'])
    if cap['specification']['document_required']: need(value['request']['document_source'] is not None)
    _cut(value['evaluation_cut']); return value


def check_preparation(raw, *, manifest):
    return _check_preparation_model(raw, manifest=manifest, model=PreparationSnapshot)


def _check_source_model(raw, *, preparation, manifest, preparation_checker):
    prep = preparation_checker(preparation, manifest=manifest); cap = _manifest(manifest)
    value = _parse(SourceRecord, raw)
    need(value['record_hash'] == record_hash(value) and value['plan_id'] == prep['scope']['analysis_plan_id']
        and all(value[key] == prep[key] for key in ('mature_head', 'original_query_ref', 'evaluation_query_ref', 'evaluation_cut')))
    document = value['document']; selection = prep['request']['document_source']
    need((document is None) == (selection is None))
    if cap['specification']['document_required']: need(document is not None)
    if document:
        need(document['selection'] == selection)
        expected = selection.get('source_set_ref', selection.get('packet'))
        approval = document['approved_selection']
        need(all(approval[key] == expected[key] for key in ('source_set_id', 'source_set_hash'))
            and value['data_evidence_ref']['evidence_id'] != document['evidence_ref']['evidence_id'])
        if selection['kind'] == 'document_packet_v207':
            need(all(document['evidence_ref'][key] == selection['packet'][key] for key in ('evidence_id', 'sha256'))
                and document['receipt_ref']['id'] == selection['packet']['id'])
    return value


def check_source(raw, *, preparation, manifest):
    return _check_source_model(raw, preparation=preparation, manifest=manifest,
        preparation_checker=check_preparation)


def check_cloud_request(raw, *, forbidden_identifiers):
    """Schema/privacy consistency only; runtime derives the actual alias map."""
    value = _parse(CloudRequest, raw)
    need(value['input_hash'] == digest(value['decision_input']))
    # No execution IDs or mapping field is structurally accepted. Also refuse
    # originals hidden in free text; the caller supplies actual private IDs.
    forbidden = list(forbidden_identifiers)
    need(bool(forbidden) and all(type(item) is str and bool(item) for item in forbidden))
    encoded = base._json(value).lower()
    need(all(item.lower() not in encoded for item in forbidden))
    _check_decision(value['decision_input'])
    return value


def _check_decision(value):
    """Use the existing pure domain primitives without inventing an activation."""
    entities=value['scope']['entity_ids'];need(len(entities)==len(set(entities)))
    requirements=base._index(value['requirements'],'requirement_ref')
    sources=base._index(value['sources'],'source_ref');caps=base._index(value['capabilities'],'capability_ref')
    candidates=base._index(value['candidates'],'candidate_ref');facts=base._index(value['facts'],'fact_ref')
    base._index(value['known_conflicts'],'conflict_ref')
    need(len({(row['kind'],row['source_id']) for row in sources.values()})==len(sources)
        and len({row['capability_id'] for row in caps.values()})==len(caps))
    for requirement in requirements.values():
        need(requirement['entity_id'] in entities);base._window(requirement['time_scope'])
        if requirement['time_mode']=='not_required':
            need(requirement['required_kind'] in {'document_statement','observed_or_recorded'}
                and requirement['semantics']=='statement' and base._window(requirement['time_scope'])==(None,None))
    for cap in caps.values():
        source=sources.get(cap['source_ref'])
        need(source is not None and source['kind']=='capability' and source['source_id']==cap['capability_id']
            and source['content_hash']==cap['capability_hash'])
        for ports in (cap['output_ports'],cap['information_targets']):
            base._index(ports,'port_ref')
            for port in ports:
                pointer=port['pointer'];need(not pointer or pointer.startswith('/') and all(
                    '~' not in part.replace('~0','').replace('~1','') for part in pointer.split('/')))
        need(not cap['information_targets'] or cap['action_kind']=='information_acquisition')
    for candidate in candidates.values():
        base._refs(candidate['capability_refs'],caps);base._index(candidate['claims'],'claim_ref');mapped=[]
        for link in candidate['requirement_mappings']:
            need(link['requirement_ref'] in requirements and link['capability_ref'] in candidate['capability_refs'])
            cap=caps[link['capability_ref']];ports=cap['output_ports'] if link['delivery']=='output' else cap['information_targets']
            port=base._index(ports,'port_ref').get(link['port_ref']);requirement=requirements[link['requirement_ref']]
            need(port is not None and all(port[key]==requirement[key] for key in ('field','unit','value_type')))
            mapped.append((link['requirement_ref'],link['capability_ref'],link['delivery'],link['port_ref']))
        need(len(mapped)==len(set(mapped)))
    for fact in facts.values():
        need(fact['entity_id'] in entities);base._window(fact['time_scope']);seen=set()
        for citation in fact['source_refs']:
            source=sources.get(citation['source_ref']);need(source is not None and citation['source_ref'] not in seen)
            seen.add(citation['source_ref'])
            if source['text'] is None:need(all(citation[key] is None for key in ('quote_start','quote_end','quote')))
            else:
                start,end=citation['quote_start'],citation['quote_end']
                need(type(start) is int and type(end) is int and 0<=start<end<=len(source['text'])
                    and source['text'][start:end]==citation['quote'])
        if fact['kind']=='observation':need(any(sources[item['source_ref']]['kind']=='observation' for item in fact['source_refs']))
        if fact['kind']=='capability_declaration':need(any(sources[item['source_ref']]['kind']=='capability' for item in fact['source_refs']))
    for conflict in value['known_conflicts']:base._conflict(conflict,facts)


def _check_preview_model(raw, *, preparation, source, manifest, model, preparation_checker, source_checker):
    prep = preparation_checker(preparation, manifest=manifest)
    sources = source_checker(source, preparation=prep, manifest=manifest)
    value = _parse(model, raw)
    from .business_uuid_projection_v307 import project_text
    instruction=project_text(prep['instruction'],namespace_id=prep['scope']['analysis_plan_id'],
        salt='same-goal-advice:'+prep['record_hash'])
    need(value['preview_hash'] == digest({key:item for key,item in value.items() if key != 'preview_hash'})
        and value['plan_id'] == prep['scope']['analysis_plan_id']
        and value['preparation_snapshot_hash'] == prep['record_hash'] and value['source_hash'] == sources['record_hash']
        and all(value[key] == prep[key] for key in ('advice_capability_ref', 'model_pin', 'budget'))
        and value['cloud_request_hash'] == digest(value['cloud_request'])
        and value['cloud_request']['input_hash'] == digest(value['cloud_request']['decision_input'])
        and value['cloud_request']['registered_instruction'] == instruction
        and value['cloud_request']['decision_input']['scope']['goal_snapshot_hash'] == prep['scope']['goal_snapshot_hash'])
    refs = [sources['data_evidence_ref']] + ([sources['document']['evidence_ref']] if sources['document'] else [])
    pins, grants = value['evidence_pins'], value['consent_pins']
    need(len(refs) == len(pins) == len(grants) <= value['budget']['max_evidence_items'])
    need(len({pin['evidence_id'] for pin in pins}) == len(pins) and len({grant['audit_id'] for grant in grants}) == len(grants))
    for ref, pin, grant in zip(refs, pins, grants):
        need(all(pin[key] == ref[key] for key in ref)
            and grant['evidence_id'] == pin['evidence_id'] and grant['model_profile_id'] == pin['model_profile_id'] == prep['model_pin']['id'])
    private=[prep['scope'][key] for key in ('workspace_id','task_id','analysis_plan_id','instance_id','release_id','package_id')]
    private.extend(prep[key] for key in ('requested_by','data_actor_id','business_actor_id'))
    check_cloud_request(value['cloud_request'],forbidden_identifiers=private)
    need(len(base._json(value['cloud_request']).encode('utf-8'))<=value['budget']['max_request_bytes'])
    return value


def check_preview(raw, *, preparation, source, manifest):
    return _check_preview_model(raw, preparation=preparation, source=source, manifest=manifest,
        model=PreviewRecord, preparation_checker=check_preparation, source_checker=check_source)


def _check_plan_model(raw, *, preparation, manifest, model, preparation_checker, preview_checker):
    prep = preparation_checker(preparation, manifest=manifest); value = _parse(model, raw)
    need(value['record_hash'] == record_hash(value) and value['scope'] == prep['scope']
        and value['preparation_snapshot_hash'] == prep['record_hash']
        and value['start_request_hash'] == digest(value['start_request'])
        and value['start_request']['expected_goal_hash'] == prep['scope']['goal_hash']
        and value['start_request']['expected_mature_head_hash'] == prep['mature_head']['head_hash']
        and value['start_request']['preview_hash'] == value['preview']['preview_hash'])
    preview_checker(value['preview'], preparation=prep, source=value['source_record'], manifest=manifest)
    return value


def check_plan(raw, *, preparation, manifest):
    return _check_plan_model(raw, preparation=preparation, manifest=manifest, model=ExecutionSnapshot,
        preparation_checker=check_preparation, preview_checker=check_preview)


def _seal(unsigned, checker, **kwargs):
    need(type(unsigned) is dict and 'record_hash' not in unsigned)
    value = copy.deepcopy(unsigned); value['record_hash'] = record_hash(value)
    return checker(value, **kwargs)


def seal_preparation(unsigned, *, manifest): return _seal(unsigned, check_preparation, manifest=manifest)
def seal_source(unsigned, *, preparation, manifest): return _seal(unsigned, check_source, preparation=preparation, manifest=manifest)
def seal_plan(unsigned, *, preparation, manifest): return _seal(unsigned, check_plan, preparation=preparation, manifest=manifest)


def seal_preview(unsigned, *, preparation, source, manifest):
    need(type(unsigned) is dict and 'preview_hash' not in unsigned)
    value = copy.deepcopy(unsigned); value['preview_hash'] = digest(value)
    return check_preview(value, preparation=preparation, source=source, manifest=manifest)


def _model_value_refs(cloud_input, raw):
    inputs=_parse(CloudDecisionInput,cloud_input);_check_decision(inputs)
    value=_parse(opinions.ModelValue,raw)
    candidates=base._index(inputs['candidates'],'candidate_ref');sources=base._index(inputs['sources'],'source_ref')
    facts=base._index(inputs['facts'],'fact_ref')
    need([row['candidate_ref'] for row in value['candidate_reviews']]==list(candidates)
        and [row['source_ref'] for row in value['source_reviews']]==list(sources))
    for row in value['candidate_reviews']:base._refs(row['supporting_fact_refs'],facts)
    conflicts=base._index(value['conflicts'],'conflict_ref')
    for original in inputs['known_conflicts']:need(conflicts.get(original['conflict_ref'])==original)
    for conflict in value['conflicts']:base._conflict(conflict,facts)
    for index,left in enumerate(inputs['facts']):
        for right in inputs['facts'][index+1:]:
            if base._comparable(left,right) and not base._same_value(left['value'],right['value']):
                need(any({left['fact_ref'],right['fact_ref']}<=set(conflict['fact_refs']) for conflict in value['conflicts']))
    recommendation=value['recommendation'];base._refs(recommendation['supporting_fact_refs'],facts)
    need(recommendation['candidate_ref'] is None or recommendation['candidate_ref'] in candidates)
    return inputs,value


def check_model_value(cloud_input,raw):
    inputs,value=_model_value_refs(cloud_input,raw)
    from .same_goal_advice_source_scope_v309 import provided_scopes
    provided_scopes(inputs,value)
    return value


def check_model_value_original_v302(cloud_input,raw):
    """Existing READY receipts retain their explicit original strict rule."""
    inputs,value=_model_value_refs(cloud_input,raw)
    sources=base._index(inputs['sources'],'source_ref')
    for row in value['source_reviews']:
        need(row['assessment']!='reviewed' or sources[row['source_ref']]['readability']=='readable')
    return value


def derive_result(snapshot, model_value, *, preparation, manifest):
    plan=check_plan(snapshot,preparation=preparation,manifest=manifest);preview=plan['preview']
    opinion=check_model_value(preview['cloud_request']['decision_input'],model_value)
    return _parse(AdviceResult,{'schema':RESULT,'plan_ref':{'id':plan['scope']['analysis_plan_id'],
        'record_hash':plan['record_hash']},'preview_hash':preview['preview_hash'],
        'evaluation_ref':plan['source_record']['mature_head']['evaluation_ref'],
        'input_hash':preview['cloud_request']['input_hash'],'model_value':opinion,
        'review_state':'ready_for_review',**{key:False for key in FLAGS}})


def check_result(raw, *, snapshot, preparation, manifest):
    need(type(raw) is dict and all(type(raw.get(key)) is bool and raw[key] is False for key in FLAGS))
    value=_parse(AdviceResult,raw)
    expected=derive_result(snapshot,value['model_value'],preparation=preparation,manifest=manifest)
    need(base._json(value)==base._json(expected));return value
