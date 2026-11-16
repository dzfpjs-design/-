"""A local advice frame over authenticated current analysis sources.

No graph activation is invented. Field, capability and fact proofs remain the
original V288 readers; the content-only composer supplies no scope or rights.
The actual IDs/private pins and text here are server-only, before any separate
privacy projection, Evidence consent, preview or send gate.
"""
import copy
import hashlib

from . import same_goal_advice_analysis_v305 as analysis
from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_sources_v302 as sources
from . import business_goal_ready_frame_v288 as original
from .contracts import digest

SCHEMA = 'same-goal-advice-current-frame-v305'
PINS = 'same-goal-advice-frame-pins-v305'
_FIELD_KEYS = {'package', 'release', 'entities', 'fields', 'object_snapshots_hash', 'goal_snapshot_hash'}


@sources.checked
def build_current(db, p, task, plan):
    """Build only after the fixed resolver admits the real business actor.

    The returned source/frame is not anonymous and grants no future send,
    current-result adoption, device action or Goal acceptance permission.
    """
    with db.no_autoflush:
        resolved=analysis.resolve_current(db,p,task,plan)
        return from_resolved(db,p,task,plan,resolved)


@sources.checked
def from_resolved(db,p,task,plan,resolved):
    """Mechanical assembly after the fixed caller authenticates real sources."""
    with db.no_autoflush:
        sources.need(resolved['schema'] == analysis.SCHEMA)
        actual, prep = resolved['task'], resolved['preparation']
        source = c._parse(c.SourceRecord, resolved['source_record'])
        originals, inputs, release = resolved['original_sources'], resolved['frame_inputs'], resolved['release']
        sources.need(originals['schema'] == analysis.ORIGINALS
            and source['record_hash'] == c.record_hash(source)
            and actual.id == task.id == originals['task_id'] == prep['scope']['task_id']
            and actual.workspace_id == p.workspace_id == prep['scope']['workspace_id']
            and source['plan_id'] == plan.id == prep['scope']['analysis_plan_id']
            and originals['release_id'] == release.id == prep['scope']['release_id']
            and originals['package_id'] == actual.package_id == prep['scope']['package_id'])
        data = originals['data']; ref = source['data_evidence_ref']; data_text = data.get('text')
        document = resolved['document']; document_record = source['document']
        sources.need(document_record is not None and digest(document_record) == digest(document['record'])
            and type(document['content']) is bytes
            and document_record['evidence_ref']['sha256'] == hashlib.sha256(document['content']).hexdigest()
            and document_record['evidence_ref']['size_bytes'] == len(document['content'])
            and data['evidence_id'] == ref['evidence_id'] and data['sha256'] == ref['sha256']
            and data['packet_id'] == source['mature_head']['evaluation_ref']['id']
            and data['evaluation_record']['evaluation_id'] == data['packet_id']
            and data['evaluation_record']['record_hash'] == source['mature_head']['evaluation_ref']['record_hash']
            and type(data_text) is str and len(data_text.encode('utf-8')) == ref['size_bytes']
            and hashlib.sha256(data_text.encode('utf-8')).hexdigest() == ref['sha256'])
        specs = original._specs(inputs)
        fields = original._field_pins(db, p, actual, release, originals, live=True)
        sources.need(type(fields) is dict and set(fields) == _FIELD_KEYS
            and fields['goal_snapshot_hash'] == prep['scope']['goal_snapshot_hash'])
        capabilities, policies = original._capability_pins(db, p, actual, originals, specs,
            fields['fields'], fields['package'], live=True)
        facts, observations, conflicts = original._facts(db, p, originals, fields['fields'])
        data_source = {'source_ref': 'data_packet', 'kind': 'packet', 'source_id': data['packet_id'],
            'content_hash': ref['sha256'], 'text': data_text,
            'readability': 'readable' if type(data_text) is str else 'partial'}
        # Exact approved excerpts are readable, while their whole-document
        # completeness/recognition accuracy has not been established here.
        document_source = {'source_ref': 'document_evidence', 'kind': 'evidence',
            'source_id': document_record['evidence_ref']['evidence_id'],
            'content_hash': document_record['evidence_ref']['sha256'],
            'text': document['content'].decode('utf-8'), 'readability': 'partial'}
        content = original.compose_content(report=data['evaluation_record']['evaluation_result'],
            fields=fields['fields'], entities=fields['entities'], candidate_specs=inputs['candidate_specs'],
            capability_pins={'capabilities': capabilities, 'policies': policies}, data_source=data_source,
            document_source=document_source, facts=facts, observation_sources=observations, conflicts=conflicts)
        decision = c._parse(c.CloudDecisionInput, {'schema': c.CLOUD_INPUT,
            'decision_intent': inputs['decision_intent'], 'scope': {
                'goal_snapshot_hash': fields['goal_snapshot_hash'],
                'entity_ids': [item['id'] for item in fields['entities']]}, **content})
        c._check_decision(decision)
        pins = {'schema': PINS, **copy.deepcopy(fields), 'capabilities': copy.deepcopy(capabilities),
            'policies': copy.deepcopy(policies)}
        pins['pins_hash'] = digest(pins)
        evidence_ids = [ref['evidence_id'], document_record['evidence_ref']['evidence_id']]
        sources.need(len(set(evidence_ids)) == 2)
        result = {'schema': SCHEMA, 'source_record': source, 'decision_input': decision, 'capability_pins': pins,
            'question': inputs['question'], 'instruction': prep['instruction'], 'evidence_ids': evidence_ids,
            'model_pin': c._parse(c.CapabilityPin, prep['model_pin'])}
        original._bytes(result, original.MAX_BYTES)
        return copy.deepcopy(result)
