"""Server-owned current destinations; pure, no devices or live acceptance."""
import copy
from types import SimpleNamespace as NS
import pytest
from fastapi import HTTPException
from generic_agents import instance_domain as domain
from generic_agents.models import InstanceRelease,DomainInstance
from test_entity_release_compat_pure import World,forbid_runtime

@pytest.fixture
def world(monkeypatch):
    w=World(monkeypatch);monkeypatch.setattr(domain,'scoped',w.db.scoped);return w

def test_current_context_keeps_original_source_and_selects_active_release(world):
    before=copy.deepcopy(world.entity.__dict__)
    instance,release,proof=domain.current_context(world.db,world.p,world.entity)
    assert instance is world.instance and release is world.r2
    assert proof['source_release_id']==world.r1.id and proof['target_release_id']==world.r2.id
    assert world.entity.__dict__==before

@pytest.mark.parametrize('change',['paused','foreign_namespace','ambiguous','old_explicit','missing_active'])
def test_current_context_cannot_authorize_incompatible_or_old_explicit_destination(world,change):
    if change=='paused':world.instance.status='paused'
    elif change=='foreign_namespace':world.entity.namespace='other'
    elif change=='ambiguous':
        duplicate=copy.deepcopy(world.r1);duplicate.id='duplicate';world.db.rows[InstanceRelease][duplicate.id]=duplicate
    elif change=='missing_active':world.instance.active_release_id=None
    with pytest.raises(HTTPException):domain.current_context(world.db,world.p,world.entity,release_id=world.r1.id if change=='old_explicit' else None)

def test_generic_entity_keeps_absence_of_managed_destination(world):
    world.db.rows[InstanceRelease].clear()
    assert domain.current_context(world.db,world.p,world.entity) is None
    assert domain.resolve_target(world.db,world.p,world.entity,None,None) is None
    with pytest.raises(HTTPException):domain.resolve_target(world.db,world.p,world.entity,'guessed','guessed')

def test_same_label_field_definition_change_does_not_grant_current_destination(world):
    kind=world.r2.data['snapshot']['object_types'][0];kind['fields'][1]['unit']='吨'
    world.reseal(world.r2)
    with pytest.raises(HTTPException):domain.current_context(world.db,world.p,world.entity)

def test_explicit_new_business_destination_does_not_retarget_old_authorization(world):
    snapshot=world.r2.data['snapshot'];snapshot.update(businesses=[{'id':'business-2','name':'人工复核','available':True,'flow':{'steps':[]}}],materials=[])
    world.instance.data={'name':'实例','enabled_business_ids':['business-2']};world.reseal(world.r2)
    selected=domain.resolve_target(world.db,world.p,world.entity,world.r2.id,'business-2')
    assert selected['instance_release_id']==world.r2.id
    with pytest.raises(HTTPException):domain.resolve_target(world.db,world.p,world.entity,world.r1.id,'business-2')

@pytest.mark.parametrize('expected,allowed',[(None,False),('release-1',False),('release-2',True)])
def test_cross_release_new_actions_require_explicit_current_destination(world,expected,allowed):
    if allowed:domain.require_destination_version(world.r2,[world.entity],expected)
    else:
        with pytest.raises(HTTPException):domain.require_destination_version(world.r2,[world.entity],expected)

def test_legacy_same_package_request_preserves_old_unmarked_shape(world):
    domain.require_destination_version(world.r1,[world.entity],None)
    from generic_agents.workspace_api import InstanceReportedObservation
    from generic_agents.contracts import ObservationCreate
    payload={'source_id':'source','source_record_id':'record','entity_id':world.entity.id,'observed_at':'2026-10-16T00:00:00+00:00','business_day':'2026-10-16','extractor_id':'rule','startup_mode':'manual'}
    old=ObservationCreate(**payload);new=InstanceReportedObservation(**payload,release_id='release-2')
    assert new.model_dump(exclude={'release_id'})==old.model_dump()

def test_explicit_mqtt_binding_keeps_source_package_and_pins_new_destination(world,monkeypatch):
    from generic_agents import mqtt_observations as mqtt,mqtt_inbox
    monkeypatch.setattr(mqtt,'scoped',world.db.scoped);monkeypatch.setattr(mqtt,'published',world.db.published)
    monkeypatch.setattr(mqtt_inbox,'validate_configuration',lambda cfg:None)
    world.instance.data={'name':'实例'}
    def lock_instance(query):
        assert query.column_descriptions[0]['entity'] is DomainInstance
        return world.instance
    world.db.scalar=lock_instance
    calls=[];extractor=NS(id='numeric-rule',data={'description':'明确选择的规则'})
    def checked(db,p,key,fields,**options):calls.append(options);return extractor
    monkeypatch.setattr(mqtt,'checked_extractor',checked)
    key=world.r1.data['snapshot']['object_types'][0]['fields'][1]['key']
    gateway=NS(data={'topics':{'station/one':'device-one'},'mapping':{'value':{'attribute':key,'unit':'株'}}})
    original=copy.deepcopy(world.entity.__dict__)
    obj,selected,binding=mqtt.prepare_binding_data(world.db,world.p,gateway,'station/one',world.entity.id,extractor.id,
        instance_release_id=world.r2.id)
    assert obj is world.entity and selected is extractor
    assert binding['package_id']==world.r1.package_id and binding['instance']=={'id':world.instance.id,'release_id':world.r2.id}
    assert calls[0]['package_id']==world.r1.package_id and world.entity.__dict__==original


def test_unmanaged_fallback_does_not_accept_foreign_workspace_entity(world):
    world.db.rows[InstanceRelease].clear();world.entity.workspace_id='other-workspace'
    with pytest.raises(HTTPException):domain.current_context(world.db,world.p,world.entity)


def test_managed_local_upload_freezes_server_expected_destination_under_lock(world):
    value=domain.local_observation_context(world.db,world.p,world.instance.id,world.entity,{'id':world.instance.id,'release_id':world.r2.id})
    assert value['release_id']==world.r2.id and value['entity_source_package_id']==world.r1.package_id
    assert len(value['entity_definition_hash'])==64
    assert ('DomainInstance',world.instance.id,True) in world.db.reads

@pytest.mark.parametrize('expected',[None,{'id':'instance-a','release_id':'release-1'},{'id':'other-instance','release_id':'release-2'}])
def test_managed_local_upload_cannot_rebind_untagged_or_delayed_request(world,expected):
    with pytest.raises(HTTPException):domain.local_observation_context(world.db,world.p,world.instance.id,world.entity,expected)

@pytest.fixture
def upload(world,monkeypatch):
    from datetime import datetime,timezone
    from generic_agents import managed_local_ingest as ingest,local_observations,state_refresh
    from generic_agents.contracts import ObservationCreate,digest
    from generic_agents.models import Capability
    source=NS(id='local-upload',workspace_id=world.p.workspace_id,namespace='platform-local',kind='source',status='published',data={'specification':{'managed_instance':world.instance.id,'timezone':'UTC'},'execution':{'adapter':'upload'},'data_policy':{'cloud_allowed':False,'external_allowed':False}})
    source.content_hash=digest(source.data);source.validated_hash=source.content_hash;world.db.rows[Capability][source.id]=source
    monkeypatch.setattr(ingest,'scoped',world.db.scoped);monkeypatch.setattr(ingest,'published',world.db.published)
    cap=NS(id='image-model',data={'allowed_agent_roles':['perception'],'execution':{'adapter':'local_specialist'}})
    original=NS(sha256='a'*64,created_at=datetime(2026,10,16,tzinfo=timezone.utc))
    monkeypatch.setattr(local_observations,'checked_cap',lambda *a:cap);monkeypatch.setattr(local_observations,'file_inputs',lambda *a:original)
    monkeypatch.setattr(ingest,'audit',lambda *a:None);monkeypatch.setattr(state_refresh,'source_changed',lambda *a:None)
    world.p.role='admin';world.db.added=[];world.db.existing=None;world.db.commits=0
    world.db.execute=lambda *a:None;world.db.scalar=lambda *a:world.db.existing
    def add(row):world.db.added.append(row)
    def flush():
        for index,row in enumerate(world.db.added):
            if row.id is None:row.id='row-'+str(index)
            if row.version is None:row.version=1
    def commit():world.db.commits+=1
    world.db.add=add;world.db.flush=flush;world.db.commit=commit
    body=ObservationCreate(source_id=source.id,source_record_id='local-record',entity_id=world.entity.id,extractor_id=cap.id,evidence_ids=['file-one'],observed_at=None,business_day='2026-10-16',startup_mode='manual')
    return ingest,body,world


def test_managed_upload_saves_original_and_queues_real_local_job_without_rewriting_entity(upload):
    ingest,body,w=upload;before=copy.deepcopy(w.entity.__dict__)
    result=ingest.ingest(body,w.p,w.db,expected_local_context={'id':w.instance.id,'release_id':w.r2.id})
    from generic_agents.models import Observation,Job
    rows=[r for r in w.db.added if isinstance(r,Observation)];jobs=[r for r in w.db.added if isinstance(r,Job)]
    assert len(rows)==len(jobs)==1 and w.db.commits==1
    assert rows[0].data['original_request']==body.model_dump() and rows[0].data['local_instance']['release_id']==w.r2.id
    assert jobs[0].kind=='process_local_observation' and jobs[0].data['observation_id']==rows[0].id
    assert w.entity.__dict__==before
    w.db.existing=rows[0];count=len(w.db.added)
    assert ingest.ingest(body,w.p,w.db,expected_local_context={'id':w.instance.id,'release_id':w.r2.id})['id']==result['id']
    assert len(w.db.added)==count


def test_managed_upload_nonce_does_not_move_a_saved_original_to_new_destination(upload):
    ingest,body,w=upload
    from generic_agents.contracts import digest
    w.db.existing=NS(data={'input_hash':digest(body.model_dump()),'local_instance':{'id':w.instance.id,'release_id':w.r1.id}})
    with pytest.raises(HTTPException):ingest.ingest(body,w.p,w.db,expected_local_context={'id':w.instance.id,'release_id':w.r2.id})
    assert w.db.added==[] and w.db.commits==0
