"""Real PostgreSQL candidate/grant custody; no observation or index mutation."""
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func,text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Entity,Issue,Observation,VectorCache,MqttReceipt
from generic_agents.worker import deliver_events
from generic_agents.evidence import evidence_bytes
from generic_agents.mqtt_unit_quality_v357 import diagnose
from generic_agents import mqtt_unit_revision_v357 as subject
from tests.test_mqtt_observations import prepared_mqtt,bind,save

@pytest.fixture(scope='module',autouse=True)
def tables():
    for cls in (subject.MqttUnitCandidateV357,subject.MqttUnitGrantV357,subject.MqttUnitRevocationV357):
        cls.__table__.create(engine(),checkfirst=True)


def prepare(client,fixture,unit='F'):
    binding=bind(client,fixture);receipt,raw=save(fixture,value=100,units={'t':unit});p=fixture[3]
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    with Session(engine()) as db:
        issue=diagnose(db,p,receipt.id)[0];issue_id=issue.id;db.commit()
    return p,binding['id'],receipt,raw,issue_id


def test_candidate_and_revocable_approval_preserve_original_and_never_apply(client,prepared_mqtt):
    p,binding,receipt,raw,issue_id=prepare(client,prepared_mqtt)
    request,approval,stop=map(lambda _:str(uuid4()),range(3))
    with Session(engine()) as db:
        entity=db.get(Entity,prepared_mqtt[1]['id']);entity_version=entity.version;before=entity.data.copy()
        candidate=subject.propose(db,p,issue_id,binding,2,request,'核对原单位后提出候选');cid=candidate.id
        assert candidate.data['candidate']['conversion']['rounded_value']=='37.78'
        assert candidate.data['candidate']['conversion']['exact_numerator']=='340'
        assert candidate.data['candidate']['conversion']['exact_denominator']=='9'
        original_hash=candidate.data['candidate_hash'];db.commit()
    with Session(engine()) as db:
        assert subject.propose(db,p,issue_id,binding,2,request,'核对原单位后提出候选').id==cid
        grant=subject.authorize(db,p,cid,approval,original_hash,'同意保留候选供后续核对');gid=grant.id
        assert grant.data['candidate_only'] and grant.data['execution_authorized'] is False;db.commit()
    with Session(engine()) as db:
        assert subject.authorize(db,p,cid,approval,original_hash,'同意保留候选供后续核对').id==gid
        rev=subject.revoke(db,p,gid,stop,'撤销候选授权');rid=rev.id;db.commit()
    with Session(engine()) as db:
        assert subject.revoke(db,p,gid,stop,'撤销候选授权').id==rid
        assert db.get(Issue,issue_id).status=='open'
        entity=db.get(Entity,prepared_mqtt[1]['id']);assert entity.version==entity_version and entity.data==before
        assert db.scalar(select(func.count()).select_from(Observation))==0
        assert db.scalar(select(func.count()).select_from(VectorCache))==0
        assert evidence_bytes(db,p,receipt.evidence_id)[1]==raw


def test_unknown_or_incompatible_unit_cannot_form_candidate(client,prepared_mqtt):
    p,binding,receipt,raw,issue_id=prepare(client,prepared_mqtt,unit='unrecognized')
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.preview(db,p,issue_id,binding,2)
        assert db.scalar(select(func.count()).select_from(subject.MqttUnitCandidateV357))==0


def test_candidate_is_append_only_and_request_reuse_cannot_change_meaning(client,prepared_mqtt):
    p,binding,receipt,raw,issue_id=prepare(client,prepared_mqtt);request=str(uuid4())
    with Session(engine()) as db:
        row=subject.propose(db,p,issue_id,binding,2,request,'原单位候选');identity=row.id;db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.propose(db,p,issue_id,binding,3,request,'原单位候选')
        db.rollback()
        with pytest.raises(DBAPIError):db.execute(text('UPDATE mqtt_unit_candidates_v357 SET version=version+1 WHERE id=:id'),{'id':identity})
        db.rollback()
        assert db.get(MqttReceipt,receipt.id).status=='quarantined'


def prepare_literal(client,fixture,number_lexeme):
    from generic_agents.mqtt_inbox import capture
    binding=bind(client,fixture);gateway,obj,rule,p=fixture
    raw=('{"record_id":"lexeme-r1","device_id":"device-one",'
         '"observed_at":"2026-10-10T23:00:00Z","values":{"t":'+number_lexeme+'},"units":{"t":"F"}}').encode()
    with Session(engine(),expire_on_commit=False) as db:
        receipt=capture(db,p,gateway['id'],gateway['version'],'sensor/one',raw,1);db.commit()
    deliver_events(workspace_id=p.workspace_id,event_kind='perception.sensor_packet',packet_receipt_id=receipt.id)
    with Session(engine()) as db:
        issue=diagnose(db,p,receipt.id)[0];issue_id=issue.id;db.commit()
    return p,binding['id'],receipt,raw,issue_id


@pytest.mark.parametrize('lexeme,expected',[
    ('100000000000000.01','55555555555537.78'),
    ('-40.00000000000000000001','-40.00'),
    ('32.009','0.00'),
    ('32.027','0.02'),
])
def test_original_number_lexeme_is_exact_and_half_even(client,prepared_mqtt,lexeme,expected):
    p,binding,receipt,raw,issue_id=prepare_literal(client,prepared_mqtt,lexeme)
    with Session(engine()) as db:
        result=subject.preview(db,p,issue_id,binding,2)
        assert result['original_number_lexeme']==lexeme
        assert result['conversion']['rounded_value']==expected
        assert result['conversion']['rounding']=='half_even'
        assert evidence_bytes(db,p,receipt.evidence_id)[1]==raw


def test_extreme_numeric_exponent_is_rejected_before_candidate(client,prepared_mqtt):
    p,binding,receipt,raw,issue_id=prepare_literal(client,prepared_mqtt,'1e-100')
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.preview(db,p,issue_id,binding,2)
        assert db.scalar(select(func.count()).select_from(subject.MqttUnitCandidateV357))==0
        assert evidence_bytes(db,p,receipt.evidence_id)[1]==raw


def test_other_admin_cannot_approve_owner_bound_candidate_without_delegation(client,prepared_mqtt):
    from generic_agents.models import User,Membership
    from generic_agents.security import Principal
    p,binding,receipt,raw,issue_id=prepare(client,prepared_mqtt)
    with Session(engine()) as db:
        row=subject.propose(db,p,issue_id,binding,2,str(uuid4()),'保留候选');cid=row.id;candidate_hash=row.data['candidate_hash']
        user=User(username='other_admin_'+uuid4().hex,password_hash='unused');db.add(user);db.flush()
        db.add(Membership(workspace_id=p.workspace_id,user_id=user.id,role='admin'));other=user.id;db.commit()
    with Session(engine()) as db:
        with pytest.raises(HTTPException):subject.authorize(db,Principal(other,p.workspace_id,'admin','test'),cid,str(uuid4()),candidate_hash,'审阅候选')
        db.rollback()
        assert db.scalar(select(func.count()).select_from(subject.MqttUnitGrantV357))==0
