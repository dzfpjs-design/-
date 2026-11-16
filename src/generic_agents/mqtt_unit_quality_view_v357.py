"""Read-only original-backed operator view; never repairs or closes an issue."""
import re
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import digest
from .evidence import evidence_bytes
from .models import Audit,IssueCheck,MqttGateway
from .mqtt_unit_quality_v357 import KIND,_source
from .mqtt_unit_number_v357 import original_number
from .services import scoped


def view_issue(db,p,issue,gateway):
    if issue.workspace_id!=p.workspace_id or issue.kind!=KIND or issue.entity_id is not None:
        raise HTTPException(409,'原质量问题无法核对')
    data=issue.data;proof=data.get('proof') if isinstance(data,dict) else None
    if not isinstance(proof,dict) or data.get('proof_hash')!=digest(proof) or proof.get('gateway_id')!=gateway.id:
        raise HTTPException(409,'原质量问题证明无法核对')
    receipt,event,report,mapping,values,units=_source(db,p,proof.get('receipt_id'))
    wire=proof.get('source_field');spec=mapping.get(wire)
    if not isinstance(spec,dict) or spec.get('attribute')!=proof.get('mapped_field'):
        raise HTTPException(409,'原测量映射无法核对')
    value=values.get(wire);unit=units.get(wire)
    safe_unit=unit if isinstance(unit,str) and 0<len(unit)<=40 and not re.search(r'[\x00-\x1f\x7f]',unit) else None
    _,raw=evidence_bytes(db,p,receipt.evidence_id)
    lexeme=original_number(raw,receipt.data['config_snapshot']['fields'].get('values_key','values'),wire)
    expected={'schema':'mqtt-unit-conflict-issue-v357','receipt_id':receipt.id,'event_id':event.id,
        'evidence_id':receipt.evidence_id,'input_sha256':receipt.input_sha256,
        'config_hash':receipt.data['config_hash'],'gateway_id':gateway.id,'source_field':wire,
        'mapped_field':spec['attribute'],'original_value':value,'original_value_is_approximate':True,
        'original_number_lexeme':lexeme if len(lexeme)<=80 else None,
        'original_number_lexeme_complete':len(lexeme)<=80,'reported_unit':safe_unit,
        'expected_unit':spec['unit'],'object_verified':False,'conversion_applied':False}
    if proof!=expected or 'unit_conflict:'+spec['attribute'] not in (receipt.data.get('decoded') or {}).get('issues',[]):
        raise HTTPException(409,'原报文与单位问题不一致')
    checks=list(db.scalars(select(IssueCheck).where(IssueCheck.workspace_id==p.workspace_id,
        IssueCheck.issue_id==issue.id,IssueCheck.data['operation'].as_string()=='detected').limit(2)))
    audits=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.object_id==issue.id,
        Audit.action=='quality.mqtt_unit_conflict_detected_v357').limit(2)))
    if (len(checks)!=1 or len(audits)!=1 or checks[0].actor_id!=audits[0].actor_id
        or checks[0].data.get('source_hash')!=receipt.input_sha256
        or checks[0].data.get('diagnosis')!=data.get('diagnosis')
        or audits[0].data.get('proof_hash')!=data['proof_hash']
        or audits[0].data.get('event_id')!=event.id
        or audits[0].data.get('detection_mode') not in {'manual','automatic'}
        or checks[0].data.get('detection_mode')!=audits[0].data.get('detection_mode')):
        raise HTTPException(409,'单位问题独立检测记录无法核对')
    decoded=receipt.data['decoded']
    return {'reference':issue.id,'source_name':gateway.name,'device_name':decoded.get('device_external_id'),
        'reported_time':decoded.get('observed_at'),'field_name':proof['mapped_field'],
        'original_value_text':proof['original_number_lexeme'],
        'original_value_complete':proof['original_number_lexeme_complete'],
        'reported_unit':proof['reported_unit'],'expected_unit':proof['expected_unit'],
        'status':'needs_review' if issue.status=='open' else 'history_requires_verification',
        'reason':'原报文已保留；单位修订、独立收件和索引复核尚未接通，不能换算或结案。',
        'original_receipt_reference':receipt.id,'can_convert':False,'can_close':False}
