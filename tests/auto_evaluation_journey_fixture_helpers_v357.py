"""Real API setup helpers for disposable PostgreSQL goal-flow tests only.

These helpers register declared software simulation sources and bounded policy;
no sensor is activated, no observation is created, no cloud call is invoked.
"""
import json
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from uuid import uuid4
from tests.test_authored_service_workflow_pg_v270 import good
from tests.test_device_gateway_source_pg_v270 import OBJECTS,WEATHER_FIELDS


def setup_simulated_scope(publication):
    s=publication.scene;client=s.client;release=publication.release
    instance=next(x for x in good(client.get('/api/instances')) if x['id']==release['instance_id'])
    good(client.post('/api/instances/'+instance['id']+'/activate',json={'version':instance['version'],
        'release_id':release['id'],'business_ids':release['available_business_ids'],'confirm_workflow_scope':True}))
    environment=good(client.post('/api/workspace/simulation-v176/environments',json={'request_id':str(uuid4()),
        'instance_id':instance['id'],'release_id':release['id'],'note':'隔离软件模拟，不代表实际设备或现场情况。',
        'confirm_simulation':True}),201)
    device=good(client.get('/api/sensing/devices/'+s.grant['sensor_device_id']))
    if device['status']=='review':device=good(client.post('/api/sensing/devices/'+device['id']+'/approve',json={
        'expected_version':device['version'],'reason':'仅登记隔离模拟设备身份。'}))
    assert device['status']=='enabled'
    root='/api/workspace/device-gateways/'+s.connection_id
    connection=good(client.get(root));grant=next(x for x in connection['grants'] if x['id']==s.grant['id']);assert grant['effective']
    object_type=next(x for x in release['snapshot']['object_types'] if x['name']==OBJECTS[0]['name'])
    fields={x['label']:x['key'] for x in object_type['fields']}
    links={wire:{'field_key':fields[label],'unit':unit} for wire,(label,unit) in WEATHER_FIELDS.items()}
    body={'request_id':str(uuid4()),'expected_version':connection['version'],'grant_id':grant['id'],
        'grant_expected_version':grant['version'],'environment_id':environment['id'],'entity_type':object_type['key'],
        'links':links,'extractor_id':publication.extractor['id'],
        'extractor_hash':publication.extractor['content_hash'],'confirm_simulation':True,
        'reason':'登记隔离模拟来源，不触发采集。'}
    source=good(client.post(root+'/sources',json=body))
    assert source['simulation'] and not source['field_verified'] and not source['observation_created']
    entity=good(client.post('/api/instances/'+instance['id']+'/objects',json={'request_id':str(uuid4()),
        'release_id':release['id'],'object_type':object_type['key'],'attributes':{fields['名称']:'隔离模拟观察点'},
        'reference':'goal-fixture-'+uuid4().hex}),201)
    # Use the simulator's actual discovered/granted identity. A made-up asset
    # identifier is rejected by the original eight-field supplier protocol.
    from generic_agents.device_acquisition_adapter_v176 import FIELDS,normalize_request
    external_id=grant['device_external_id']
    assert external_id=='sim-weather-01' and set(links)<=FIELDS[external_id]
    request_key='fixture-'+uuid4().hex
    dto={'request_key':request_key,'target_external_id':'isolated-simulation-target',
        'fields':['temperature_c','humidity_percent'],'max_duration_seconds':2,
        'asset_external_id':external_id,'action':'observe','max_cost_minor':0,'currency':'CNY'}
    normalized,_,_=normalize_request(json.dumps(dto,ensure_ascii=False).encode(),request_key)
    assert normalized.model_dump()==dto
    now=datetime.now(timezone.utc);end=now+timedelta(hours=24)
    asset=good(client.post('/api/sensing/assets',json={'source_id':source['capability']['id'],
        'external_id':external_id,'title':'隔离模拟数据源',
        'specification':{'coverage_entity_ids':[entity['id']],'fields':[fields[k] for k in ('气温','相对湿度','风速','气压')],
          'modalities':['numeric'],'actions':['observe'],'latency_seconds':2,'quote_ceiling_minor':0,'currency':'CNY',
          'health':'healthy','health_observed_at':now.isoformat(),'health_valid_until':end.isoformat()},
        'reason':'登记单对象、只读、零费用模拟接口，不启动采集。'}),201)
    good(client.post('/api/sensing/assets/'+asset['id']+'/device',json={'expected_version':asset['version'],
        'device_id':device['id'],'device_expected_version':device['version'],
        'reason':'关联模拟设备身份。'}))
    required=[fields['气温'],fields['相对湿度']]
    policy=good(client.post('/api/monitor-policies',json={'title':'隔离单对象观测策略','entity_ids':[entity['id']],
        'source_ids':[source['capability']['id']],'fields':required,'sensor_asset_ids':[asset['id']],
        'selection_requirement':{'allowed_modalities':['numeric'],'allowed_actions':['observe'],'max_attempts':1,'max_total_cost_minor':0},
        'timezone':'Asia/Shanghai','max_age_seconds':publication.preview['binding']['preparation_policy']['max_age_seconds'],'max_acquisitions_per_day':1,'max_duration_seconds':2,
        'valid_until':end.isoformat()}),201)
    approved=good(client.post('/api/monitor-policies/'+policy['id']+'/approve',json={
        'expected_version':policy['version'],'reason':'只允许单对象、零费用、单次软件模拟观测。'}))
    control=good(client.get('/api/sensing/monitor-policies/'+policy['id']+'/scheduling-control',params={'instance_id':instance['id']}))
    pin={'policy_id':policy['id'],'approved_hash':control['approved_hash']}
    good(client.post('/api/sensing/monitor-policies/'+policy['id']+'/scheduling-control?instance_id='+instance['id'],json={
        'schema':'monitor-scheduling-command-v253','request_id':str(uuid4()),'instance_id':instance['id'],
        'expected_version':control['control']['version'] if control['control'] else 0,
        'expected_policy_version':control['policy_version'],'approved_hash':control['approved_hash'],
        'mode':'goal_only','reason':'只响应原任务，不启用周期采样。'}),201)
    return SimpleNamespace(scene=s,release=release,entity=entity,fields=fields,required=required,
        environment=environment,source=source,asset=asset,policy=approved,policy_pin=pin)
