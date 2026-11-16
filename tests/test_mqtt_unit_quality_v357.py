"""Original MQTT receipt and data acknowledgement; not a conversion or repair."""
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Issue,IssueCheck,Observation,MqttReceipt,Memory
from generic_agents.security import Principal
from generic_agents.evidence import evidence_bytes
from generic_agents.worker import deliver_events
from generic_agents.mqtt_unit_quality_v357 import diagnose,KIND
from tests.test_mqtt_inbox import gateway,save,packet


def test_received_conflict_becomes_one_original_backed_issue_without_editing_original(gateway):
    raw=packet(units={'t':'F'});receipt=save(gateway,raw);p=gateway[0]
    with Session(engine()) as db:
        with pytest.raises(HTTPException):diagnose(db,p,receipt.id)
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    with Session(engine()) as db:
        original=db.get(MqttReceipt,receipt.id);version=original.version;status=original.status
        found=diagnose(db,p,receipt.id);assert len(found)==1;identity=found[0].id;db.commit()
    with Session(engine()) as db:
        issue=db.get(Issue,identity);proof=issue.data['proof']
        assert issue.kind==KIND and issue.status=='open' and issue.entity_id is None
        assert proof['original_value']==39.5 and proof['reported_unit']=='F' and proof['expected_unit']=='C'
        assert proof['object_verified'] is False and proof['conversion_applied'] is False
        assert evidence_bytes(db,p,receipt.evidence_id)[1]==raw
        current=db.get(MqttReceipt,receipt.id);assert current.version==version and current.status==status
        assert db.scalar(select(func.count()).select_from(Observation))==0
        assert db.scalar(select(func.count()).select_from(Memory).where(Memory.data['type'].as_string()=='sensor_original'))==1
        assert diagnose(db,p,receipt.id)[0].id==identity;db.commit()
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(Issue).where(Issue.kind==KIND))==1
        assert db.scalar(select(func.count()).select_from(IssueCheck).where(IssueCheck.issue_id==identity))==1


def test_missing_receipt_or_unreadable_original_never_creates_issue(gateway):
    from generic_agents.config import settings
    from generic_agents.models import Evidence
    p=gateway[0];receipt=save(gateway,packet(units={'t':'F'}))
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    with Session(engine()) as db:
        original=db.get(Evidence,receipt.evidence_id);(settings().storage_dir/original.storage_key).write_bytes(b'broken')
        with pytest.raises(HTTPException):diagnose(db,p,receipt.id)
        db.rollback()
    with Session(engine()) as db:assert db.scalar(select(func.count()).select_from(Issue).where(Issue.kind==KIND))==0


def test_cross_workspace_and_viewer_cannot_diagnose(gateway):
    p=gateway[0];receipt=save(gateway,packet(units={'t':'F'}))
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    with Session(engine()) as db:
        with pytest.raises(HTTPException):diagnose(db,Principal(p.user_id,p.workspace_id,'viewer','test'),receipt.id)
        with pytest.raises(HTTPException):diagnose(db,Principal(p.user_id,str(uuid4()),'admin','test'),receipt.id)


def test_lost_independent_detection_audit_never_creates_replacement(gateway):
    from generic_agents.models import Audit
    p=gateway[0];receipt=save(gateway,packet(units={'t':'F'}))
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    with Session(engine()) as db:
        issue=diagnose(db,p,receipt.id)[0];identity=issue.id;db.commit()
    with Session(engine()) as db:
        proof=db.scalar(select(Audit).where(Audit.object_id==identity,Audit.action=='quality.mqtt_unit_conflict_detected_v357'))
        db.delete(proof);db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):diagnose(db,p,receipt.id)
        assert db.scalar(select(func.count()).select_from(Issue).where(Issue.kind==KIND))==1


def test_revoked_membership_is_not_replaced_by_stale_principal(gateway):
    from generic_agents.models import Membership
    p=gateway[0];receipt=save(gateway,packet(units={'t':'F'}))
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    with Session(engine()) as db:
        membership=db.scalar(select(Membership).where(Membership.workspace_id==p.workspace_id,Membership.user_id==p.user_id))
        membership.role='viewer';db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException) as error:diagnose(db,p,receipt.id)
        assert error.value.status_code==403
        assert db.scalar(select(func.count()).select_from(Issue).where(Issue.kind==KIND))==0


def test_existing_closed_issue_is_preserved_not_reopened_or_duplicated(gateway):
    p=gateway[0];receipt=save(gateway,packet(units={'t':'F'}))
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    with Session(engine()) as db:
        issue=diagnose(db,p,receipt.id)[0];identity=issue.id;db.commit()
    with Session(engine()) as db:
        issue=db.get(Issue,identity);issue.status='resolved';issue.version+=1;db.commit()
    with Session(engine()) as db:
        issue=diagnose(db,p,receipt.id)[0]
        assert issue.id==identity and issue.status=='resolved'
        assert db.scalar(select(func.count()).select_from(Issue).where(Issue.kind==KIND))==1


def test_issue_preserves_exact_original_number_lexeme(gateway):
    p=gateway[0]
    raw=b'{"record_id":"number-1","device_id":"sensor-1","observed_at":"2026-10-10T23:00:00Z","values":{"t":100000000000000.01},"units":{"t":"F"}}'
    receipt=save(gateway,raw)
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    with Session(engine()) as db:
        issue=diagnose(db,p,receipt.id,mode='automatic')[0];proof=issue.data['proof'];identity=issue.id
        assert proof['original_number_lexeme']=='100000000000000.01'
        assert proof['original_number_lexeme_complete'] and proof['original_value_is_approximate']
        db.commit()
    with Session(engine()) as db:
        check=db.scalar(select(IssueCheck).where(IssueCheck.issue_id==identity))
        assert check.data['detection_mode']=='automatic'
        assert evidence_bytes(db,p,receipt.evidence_id)[1]==raw


def test_oversized_original_number_is_not_silently_truncated(gateway):
    p=gateway[0];lexeme='1.'+'2'*100
    raw=('{"record_id":"number-2","device_id":"sensor-1","observed_at":"2026-10-10T23:00:00Z",'
        '"values":{"t":'+lexeme+'},"units":{"t":"F"}}').encode()
    receipt=save(gateway,raw)
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    with Session(engine()) as db:
        issue=diagnose(db,p,receipt.id)[0];proof=issue.data['proof']
        assert proof['original_number_lexeme'] is None and not proof['original_number_lexeme_complete']
        assert proof['original_value_is_approximate'] and evidence_bytes(db,p,receipt.evidence_id)[1]==raw


def test_competing_diagnostic_does_not_block_and_same_original_can_resume(gateway):
    import time
    from sqlalchemy import text
    p=gateway[0];receipt=save(gateway,packet(units={'t':'F'}))
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    key='mqtt-unit-quality-v357:'+p.workspace_id+':'+receipt.id
    with Session(engine()) as holder:
        holder.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),{'key':key})
        with Session(engine()) as blocked:
            begin=time.monotonic()
            with pytest.raises(HTTPException):diagnose(blocked,p,receipt.id)
            assert time.monotonic()-begin<2
            assert db_no_issue(blocked)
        holder.rollback()
    with Session(engine()) as db:
        assert len(diagnose(db,p,receipt.id,mode='automatic'))==1
        db.commit()


def db_no_issue(db):
    return db.scalar(select(func.count()).select_from(Issue).where(Issue.kind==KIND))==0
