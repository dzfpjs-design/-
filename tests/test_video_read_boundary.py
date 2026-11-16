"""Published rules and transactional, cross-entry local-video budget receipts."""
from datetime import timedelta
from uuid import uuid4
import copy
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi import HTTPException
from generic_agents.db import engine
from generic_agents.models import VideoReadReservation, MediaChannel, MediaGateway, InstanceRelease, VideoDomainBinding, ContinuousStream
from generic_agents.security import Principal
from generic_agents import video_read_boundary as boundary
from tests.test_supplement_watch import configured, explicit_no_alert_fixture
from tests.test_media import media_runtime
from tests.test_local_specialists import mocked_runtime


def actor(db, channel):
 binding=db.scalar(select(VideoDomainBinding).where(VideoDomainBinding.channel_id==channel.id))
 return Principal(binding.actor_id,channel.workspace_id,'admin','boundary-test')


def test_manual_periodic_and_supplement_units_share_failed_reserved_budget(client,media_runtime):
 _,_,_,ch,_=configured(client,type_rule={'limitEnabled':True,'limit':2})
 with Session(engine()) as db:
  channel=db.get(MediaChannel,ch['id']);gateway=db.get(MediaGateway,channel.gateway_id);p=actor(db,channel)
  # The initial real-shaped validation fixture already counts as one legacy unit.
  row=boundary.reserve(db,p,channel,gateway,'manual',4,'manual-'+str(uuid4()));db.commit()
  assert row.data['receipt']['usage_before']['used']==1
  with pytest.raises(HTTPException,match='今日读取次数'):
   boundary.reserve(db,p,channel,gateway,'automatic',4,'periodic-'+str(uuid4()))
  db.rollback()
  assert len(list(db.scalars(select(VideoReadReservation))))==2


def test_reservation_idempotency_rollback_and_original_authority_guard(client,media_runtime):
 _,_,_,ch,_=configured(client)
 with Session(engine()) as db:
  channel=db.get(MediaChannel,ch['id']);gateway=db.get(MediaGateway,channel.gateway_id);p=actor(db,channel);key='read-'+str(uuid4())
  row=boundary.reserve(db,p,channel,gateway,'manual',4,key);identity=row.id;db.rollback()
  assert db.get(VideoReadReservation,identity) is None
  row=boundary.reserve(db,p,channel,gateway,'manual',4,key);db.commit()
  assert boundary.reserve(db,p,channel,gateway,'manual',4,key).id==row.id
  with pytest.raises(HTTPException,match='不能更换'):
   boundary.reserve(db,p,channel,gateway,'manual',8,key)
  db.rollback();assert boundary.guard(db,p,row.id).id==row.id
  channel.data={**channel.data,'read_authorized':False};db.commit()
  with pytest.raises(HTTPException,match='没有当前读取授权'):boundary.guard(db,p,row.id)


@pytest.mark.parametrize('type_rule,custom',[
 ({'read':False},None),({}, {'read':False}),
 ({'readScopeEnabled':True},None),({'limitEnabled':True,'limit':True},None)])
def test_manual_read_cannot_bypass_device_veto_or_unverifiable_scope(client,media_runtime,type_rule,custom):
 _,_,_,ch,_=configured(client,type_rule=type_rule,custom=custom)
 with Session(engine()) as db:
  channel=db.get(MediaChannel,ch['id']);gateway=db.get(MediaGateway,channel.gateway_id);p=actor(db,channel)
  with pytest.raises(HTTPException):boundary.reserve(db,p,channel,gateway,'manual',4,'read-'+str(uuid4()))
  db.rollback();assert len(list(db.scalars(select(VideoReadReservation))))==1


def test_human_confirmation_does_not_create_automatic_business_permission(client,media_runtime):
 _,_,_,ch,_=configured(client,mode='执行前确认')
 with Session(engine()) as db:
  channel=db.get(MediaChannel,ch['id']);gateway=db.get(MediaGateway,channel.gateway_id);p=actor(db,channel)
  assert boundary.authority(db,p,channel,gateway,'manual')['mode']=='manual'
  with pytest.raises(HTTPException,match='未允许自动读取'):boundary.authority(db,p,channel,gateway,'automatic')


def test_duplicate_gateway_and_removed_binding_cannot_reset_managed_rules(client,media_runtime):
 _,_,_,ch,_=configured(client,type_rule={'limitEnabled':True,'limit':1})
 with Session(engine()) as db:
  channel=db.get(MediaChannel,ch['id']);gateway=db.get(MediaGateway,channel.gateway_id);p=actor(db,channel)
  alias=MediaGateway(workspace_id=p.workspace_id,status='ready',data={**gateway.data,'rtsp_origin':gateway.data['rtsp_origin'].replace('127.0.0.1','localhost')});db.add(alias);db.flush()
  duplicate=MediaChannel(workspace_id=p.workspace_id,gateway_id=alias.id,path=channel.path,data=copy.deepcopy(channel.data));db.add(duplicate);db.flush()
  assert boundary.physical(db,channel,gateway)[0]==boundary.physical(db,duplicate,alias)[0]
  with pytest.raises(HTTPException,match='复制来源'):boundary.authority(db,p,duplicate,alias,'manual')
  binding=db.scalar(select(VideoDomainBinding).where(VideoDomainBinding.channel_id==channel.id));binding.status='revoked';db.flush()
  with pytest.raises(HTTPException,match='删除关联'):boundary.authority(db,p,channel,gateway,'manual')


def test_restriction_intersection_and_continuous_duration(client,media_runtime):
 _,_,_,ch,_=configured(client,type_rule={'limitEnabled':True,'limit':8,'cooldownEnabled':True,'cooldown':2,'durationEnabled':True,'duration':3},custom={'read':True,'limitEnabled':True,'limit':4,'cooldownEnabled':True,'cooldown':5,'durationEnabled':True,'duration':1})
 with Session(engine()) as db:
  channel=db.get(MediaChannel,ch['id']);gateway=db.get(MediaGateway,channel.gateway_id);p=actor(db,channel)
  rules=boundary.authority(db,p,channel,gateway,'automatic');assert (rules['daily_limit'],rules['cooldown_minutes'],rules['max_read_seconds'])==(4,5,60)
  with pytest.raises(HTTPException,match='读取间隔'):boundary.available(db,p,channel,gateway,'manual',4,boundary.datetime.now(boundary.CHINA))
  current=boundary.datetime.now(boundary.CHINA)+timedelta(minutes=6)
  with pytest.raises(HTTPException,match='读取时长'):boundary.available(db,p,channel,gateway,'automatic',120,current)


def test_legacy_failed_continuous_connection_before_a_clip_still_counts(client,media_runtime):
 _,_,_,ch,_=configured(client,type_rule={'limitEnabled':True,'limit':2})
 with Session(engine()) as db:
  channel=db.get(MediaChannel,ch['id']);gateway=db.get(MediaGateway,channel.gateway_id);p=actor(db,channel)
  db.add(ContinuousStream(workspace_id=p.workspace_id,channel_id=channel.id,actor_id=p.user_id,status='blocked',data={'connections':1}));db.commit()
  assert boundary.usage(db,channel,gateway,boundary.datetime.now(boundary.CHINA))['used']==2
  with pytest.raises(HTTPException,match='今日读取次数'):boundary.reserve(db,p,channel,gateway,'manual',4,'read-'+str(uuid4()))


def test_actual_manual_route_device_veto_does_not_queue_or_spend(client,media_runtime):
 _,_,_,ch,install=configured(client,type_rule={'read':False})
 from generic_agents.models import StreamSession
 with Session(engine()) as db:before=len(list(db.scalars(select(StreamSession))))
 response=client.post('/api/media/channels/'+ch['id']+'/capture',json={'channel_version':ch['version'],'installation_id':install['id'],'seconds':4})
 assert response.status_code==409 and '不允许读取' in response.json()['detail']
 with Session(engine()) as db:
  assert len(list(db.scalars(select(StreamSession))))==before
  assert len(list(db.scalars(select(VideoReadReservation))))==1
 preview=client.get('/api/media/channels/'+ch['id']+'/read-boundary').json()
 assert not preview['modes']['manual']['allowed_now'] and preview['usage']['used']==1


def test_controller_api_explicit_confirmation_does_not_override_business_auto_veto(client,media_runtime):
 _,_,_,ch,install=configured(client,mode='执行前确认')
 for path,extra in [('monitors',{'interval_seconds':60,'runtime_minutes':60,'max_segments':2,'allow_automatic_reads':True}),
                    ('continuous',{'runtime_minutes':10,'max_segments':2,'allow_continuous_reads':True})]:
  result=client.post('/api/media/channels/'+ch['id']+'/'+path,json={'channel_version':ch['version'],'installation_id':install['id'],'seconds':4,**extra})
  assert result.status_code==409 and '未允许自动读取' in result.json()['detail'],result.text


def test_continuous_segments_share_one_connection_reservation(client,media_runtime,monkeypatch):
 _,_,_,ch,install=configured(client,type_rule={'limitEnabled':True,'limit':3})
 from tests.test_continuous_media import codec,start,execute,current
 codec(monkeypatch);response=start(client,ch,install,configuration={'sample_hz':1,'threshold':.5,'presence_rule':None});assert response.status_code==201,response.text;row=response.json();execute(row);saved=current(client,row['id'])
 assert saved['status']=='exhausted' and len(saved['session_ids'])==3
 with Session(engine()) as db:
  channel=db.get(MediaChannel,ch['id']);gateway=db.get(MediaGateway,channel.gateway_id)
  assert boundary.usage(db,channel,gateway,boundary.datetime.now(boundary.CHINA))['used']==2
  assert len(saved['read_reservation_ids'])==1


def test_connection_failure_before_first_frame_keeps_spent_reservation(client,media_runtime,monkeypatch):
 _,_,_,ch,install=configured(client,type_rule={'limitEnabled':True,'limit':2})
 from tests.test_continuous_media import start,execute,current
 monkeypatch.setattr('generic_agents.local_video.child',lambda *a,**kw:(_ for _ in ()).throw(ValueError('fixture connection failed')))
 response=start(client,ch,install,configuration={'sample_hz':1,'threshold':.5,'presence_rule':None});assert response.status_code==201,response.text;row=response.json();execute(row);saved=current(client,row['id'])
 assert not saved['session_ids'] and len(saved['read_reservation_ids'])==1
 with Session(engine()) as db:
  channel=db.get(MediaChannel,ch['id']);gateway=db.get(MediaGateway,channel.gateway_id)
  assert boundary.usage(db,channel,gateway,boundary.datetime.now(boundary.CHINA))['used']==2


def test_moving_receipt_to_yesterday_cannot_reset_current_budget(client,media_runtime):
 _,_,_,ch,_=configured(client,type_rule={'limitEnabled':True,'limit':1})
 with Session(engine()) as db:
  channel=db.get(MediaChannel,ch['id']);gateway=db.get(MediaGateway,channel.gateway_id)
  receipt=db.scalar(select(VideoReadReservation));receipt.created_at-=timedelta(days=1);db.commit()
  with pytest.raises(HTTPException,match='计次时间'):boundary.usage(db,channel,gateway,boundary.datetime.now(boundary.CHINA))


def test_pause_in_start_window_prevents_connection_and_new_reservation(client,media_runtime,monkeypatch):
 _,_,_,ch,install=configured(client)
 from tests.test_continuous_media import start,execute,current
 response=start(client,ch,install,configuration={'sample_hz':1,'threshold':.5,'presence_rule':None});assert response.status_code==201,response.text;row=response.json()
 real_reserve=boundary.reserve;calls=[]
 def pause_then_reserve(*args,**kwargs):
  live=current(client,row['id']);assert client.post('/api/media/continuous/'+row['id']+'/pause',json={'version':live['version']}).status_code==200
  return real_reserve(*args,**kwargs)
 monkeypatch.setattr(boundary,'reserve',pause_then_reserve)
 monkeypatch.setattr('generic_agents.local_video.child',lambda *a,**kw:calls.append('unexpected connection'))
 execute(row);assert current(client,row['id'])['status']=='paused' and not calls
 with Session(engine()) as db:assert len(list(db.scalars(select(VideoReadReservation))))==1


@pytest.mark.parametrize('policy,equipment',[
 ({'rules':{'observe':{'mode':'自动运行','limitsEnabled':'false','limit':2}}},{'types':{'camera':{'read':True}}}),
 ({'rules':{'observe':{'mode':'自动运行','scope':None}}},{'types':{'camera':{'read':True}}}),
 ({'rules':{'observe':{'mode':'自动运行'}}},{'types':[]}),
 ({'rules':{'observe':{'mode':'自动运行'}}},{'types':{'camera':{'read':True}},'devices':[]})])
def test_malformed_rule_is_explicitly_blocked_instead_of_becoming_default(policy,equipment):
 with pytest.raises(HTTPException):boundary.limits(policy,equipment,None,'automatic')


@pytest.mark.parametrize('malformed',[None,[],{'request':None},{'request':{},'authority':{},'reserved_at':'not-a-date'}])
def test_malformed_saved_receipt_is_readable_and_recoverable_without_another_read(client,media_runtime,malformed):
 _,_,_,ch,_=configured(client)
 with Session(engine()) as db:
  receipt=db.scalar(select(VideoReadReservation));identity=receipt.id;old=copy.deepcopy(receipt.data)
  receipt.data={'receipt':malformed,'receipt_hash':boundary.digest(malformed)};db.commit()
 response=client.get('/api/media/channels/'+ch['id']+'/read-boundary')
 assert response.status_code==200,response.text
 value=response.json();assert value['usage'] is None and '回执' in value['budget_error']
 assert all(not mode['allowed_now'] for mode in value['modes'].values())
 with Session(engine()) as db:
  assert len(list(db.scalars(select(VideoReadReservation))))==1
  receipt=db.get(VideoReadReservation,identity);receipt.data=old;db.commit()
 value=client.get('/api/media/channels/'+ch['id']+'/read-boundary').json()
 assert value['usage']['used']==1 and value['budget_error'] is None
