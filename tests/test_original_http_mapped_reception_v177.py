"""Domain/wire compatibility with real mapping and ledger validators.

SQL rows, original parsed Evidence and dispatch identity are public synthetic IO,
not actual PG/device/live-source authorization. No Observation/child is needed:
the normal ingest calls this proof before attaching either one. Root executes.
"""
import copy
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from generic_agents import original_acquisition_reception_v127 as subject
from generic_agents import http_observation_exchange_v177 as ledger
from generic_agents import http_observation_mapping_v177 as protocol
from generic_agents import evidence
from generic_agents.http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
from generic_agents.models import Acquisition, Audit, Capability, Entity, Job
from tests.test_http_observation_exchange_v177 import fixture_row, packet, uid, P, F, TYPE
from tests.test_report_saved_map_design_consumer_v176 import matches_statement, equal_statement


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('Pure request compatibility cannot contact DB, device or process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


class StrictIO:
    def __init__(self, row, job, package):
        self.row=row;self.job=job;self.package=package;self.rows=[row];self.anchors=[];self.reads=[]
    def add(self, row):
        assert type(row) is Audit
        row.id=uid(800+len(self.anchors));self.anchors.append(row)
    def scalars(self, statement):
        reverse=select(Exchange).where(Exchange.workspace_id==P.workspace_id,Exchange.acquisition_id==self.row.acquisition_id)
        by_acq=select(Audit).where(Audit.workspace_id==P.workspace_id,Audit.action==ledger.ACTION,
            Audit.data['acquisition_id'].as_string()==self.row.acquisition_id)
        by_ids=ledger._authority_query(P,self.row.id).where(Audit.id.in_([event.id for event in self.anchors]))
        for expected,values in [(reverse,self.rows),(by_acq,self.anchors),(by_ids,self.anchors)]:
            if matches_statement(statement,expected):
                equal_statement(statement,expected);self.reads.append('inventory');return list(values)
        raise AssertionError('Unexpected scoped request inventory query')
    def scalar(self, statement):
        expected=select(Job).where(Job.id==self.job.id,Job.workspace_id==P.workspace_id)
        if matches_statement(statement,expected):equal_statement(statement,expected);return self.job
        issued=select(Audit.id).where(Audit.workspace_id==P.workspace_id,
            Audit.object_id==self.row.source_id,Audit.action=='device.gateway_source_issued_v270').limit(1)
        if matches_statement(statement,issued):
            equal_statement(statement,issued);return getattr(self,'issued_source_audit_id',None)
        expected=select(Capability).where(Capability.id==self.package.id,Capability.workspace_id==P.workspace_id)
        equal_statement(statement,expected);return self.package
    def execute(self, statement):
        inventory=select(func.array_agg(Audit.id)).where(Audit.workspace_id==P.workspace_id,
            Audit.action==ledger.ACTION,Audit.object_id==self.row.id).scalar_subquery()
        expected=select(Exchange,inventory.label('captured_audit_ids')).where(
            Exchange.workspace_id==P.workspace_id,Exchange.id==self.row.id).execution_options(populate_existing=True)
        equal_statement(statement,expected);self.reads.append('actual-ledger')
        return NS(first=lambda:(copy.deepcopy(self.row),[event.id for event in self.anchors]))
    def commit(self):raise AssertionError('Historical compatibility must not commit or acquire a new device action')
    def flush(self):raise AssertionError('Historical compatibility must not flush')


@pytest.fixture
def scene(monkeypatch, request):
    row=fixture_row();old=row.data['freeze']['mapping'];scope=copy.deepcopy(old['mapping']['scope'])
    schemas={TYPE:{'type':'object','properties':{F:{'type':'number','unit':'°C'}},'additionalProperties':False}}
    package=Capability(id=scope['package_id'],workspace_id=P.workspace_id,kind='domain_package',
        namespace='public_synthetic',status='draft',data={'specification':{'entity_schemas':schemas}})
    package.content_hash=package.validated_hash=protocol.digest(package.data);scope['package_hash']=package.content_hash
    configured={**copy.deepcopy(old['mapping']),'scope':scope}
    source_data={'kind':'source','allowed_agent_roles':['perception'],
        'execution':{'adapter':'http','side_effect':True,'endpoint':'http://fixture/acquisitions',
            'receipt_endpoint':'http://fixture/acquisitions/receipts'},
        'data_policy':{'simulation':True,'test_only':True,'cloud_allowed':False,'deployment':'local'},
        'specification':{'simulation_environment_id':scope['environment_id'],'http_observation_mapping_v177':configured,
            'observation_capability':{'fields':[F],'modalities':['numeric'],'actions':['observe']}},
        # This is intentionally RAW supplier schema, not domain field IDs.
        'input_schema':{'type':'object','required':['fields'],'properties':{
            'fields':{'type':'array','items':{'enum':['temperature_c']}}}},
        'output_schema':{'type':'object','required':['schema','status'],
            'properties':{'schema':{'const':protocol.SUPPLIER},'status':{'enum':['accepted','completed','completed_no_data']}}}}
    if getattr(request,'param',None)=='managed':
        from generic_agents import device_gateway_sources_v270 as managed_sources
        from tests.test_device_gateway_sources_v270 import recipe
        binding=recipe.__wrapped__(monkeypatch)
        binding['mapping']=copy.deepcopy(configured)
        source_data=managed_sources.manifest(binding)
    source=Capability(id=row.source_id,workspace_id=P.workspace_id,kind='source',status='draft',
        namespace=source_data.get('namespace'),name=source_data.get('name'),
        release_version=source_data.get('version'),data=source_data)
    source.content_hash=source.validated_hash=protocol.digest(source.data)
    current=protocol.freeze_mapping(source.data,{**scope,'source_id':source.id},schemas,configured['links'])
    domain=copy.deepcopy(row.data['freeze']['domain_frozen']['request'])
    pair=protocol.to_supplier_request(domain,current)
    row.data['freeze']={**row.data['freeze'],'mapping':current,**pair}
    row.data['freeze_hash']=protocol.digest(row.data['freeze'])
    job=Job(id=row.dispatch_job_id,workspace_id=P.workspace_id,kind='dispatch_acquisition',status='running',
        data={'actor_id':P.user_id,'acquisition_id':row.acquisition_id})
    db=StrictIO(row,job,package)
    ledger._record(db,P,row,'create',None)
    previous=protocol.digest(ledger.snapshot(row));row.version=2;row.status='awaiting_receipt'
    ledger._record(db,P,row,'awaiting_receipt',previous)
    raw=packet(row);projection=protocol.from_supplier_observation(raw,current,pair['domain_frozen'],pair['wire_frozen'])
    origin={'schema':'http-observation-receipt-origin-v177','operation':'dispatch_post','job_id':job.id,
        'lease_token':row.data['freeze']['dispatch_identity']['lease_token'],'actor_id':P.user_id,
        'exchange_id':row.id,'exchange_version':2,'original_request_key':domain['request_key'],
        'wire_request_hash':pair['wire_frozen']['request_hash'],'read_enqueue_audit_id':None}
    receipt={'raw':raw,'raw_hash':protocol.digest(raw),'projection':projection,'projection_hash':protocol.digest(projection),
        'evidence_id':uid(780),'sha256':protocol.digest(raw),'receipt_origin':origin}
    previous=protocol.digest(ledger.snapshot(row));row.version=3;row.status='received'
    row.data={**row.data,'receipt_history':[receipt],'latest_receipt_hash':receipt['raw_hash'],'projection':projection}
    ledger._record(db,P,row,'receipt.saved',previous)
    content=protocol.canonical(raw).encode('utf-8')
    ev=NS(media_type='application/json',sha256=protocol.digest(raw),size_bytes=len(content),data={**ledger.MARK,
        'purpose':'parsed_http_observation_receipt_v177','native_http_bytes':False,'exchange_id':row.id,
        'receipt_origin':copy.deepcopy(origin)})
    def evidence_io(actual_db,p,identity):
        assert actual_db is db and p.workspace_id==P.workspace_id and identity==receipt['evidence_id']
        return ev,content
    monkeypatch.setattr(evidence,'evidence_bytes',evidence_io)
    entity=Entity(id=row.data['freeze']['parent_pins']['entity_id'],workspace_id=P.workspace_id,
        namespace=package.namespace,entity_type=TYPE,schema_capability_id=package.id,external_id=domain['target_external_id'])
    acq=Acquisition(id=row.acquisition_id,workspace_id=P.workspace_id,capability_id=source.id,entity_id=entity.id,
        idempotency_key=domain['request_key'],data={'actor_id':P.user_id,'http_observation_exchange_id_v177':row.id,
            'request_hash':pair['wire_frozen']['request_hash']})
    return NS(db=db,row=row,source=source,package=package,entity=entity,acq=acq,domain=domain,
        receipt=receipt,evidence=ev)


def check(case):return subject._mapped_dispatch_request(case.db,P,case.acq,case.source,case.entity,case.domain)


def test_original_domain_and_actual_wire_are_distinct_and_cold_ledger_allows_pre_ingest_proof(scene):
    case=scene;before=protocol.canonical(ledger.snapshot(case.row))
    assert case.row.observation_id is None and case.db.job.status=='running'
    assert protocol.digest(case.domain)!=case.acq.data['request_hash']
    result=check(case)
    assert result=={'schema':'http-mapped-request-proof-v177','exchange_id':case.row.id,
        'mapping_freeze_hash':case.row.data['freeze']['mapping']['freeze_hash'],
        'domain_request_hash':protocol.digest(case.domain),'wire_request_hash':case.acq.data['request_hash'],
        'wire_body_sha256':case.row.data['freeze']['wire_frozen']['body_sha256']}
    assert 'actual-ledger' in case.db.reads and protocol.canonical(ledger.snapshot(case.row))==before
    assert case.domain['fields']==[F] and case.row.data['freeze']['wire_frozen']['request']['fields']==['temperature_c']


@pytest.mark.parametrize('fault',['marker','record','audit','double_record_audit','source_config','request_hash',
    'domain_fields','body','source_hash','package_hash','raw_evidence','actor','target'])
def test_mapping_cannot_fall_back_on_missing_inventory_changed_original_or_schema(fault,scene):
    case=scene
    if fault=='marker':case.acq.data.pop('http_observation_exchange_id_v177')
    elif fault=='record':case.db.rows=[]
    elif fault=='audit':case.db.anchors=[]
    elif fault=='double_record_audit':case.db.rows=[];case.db.anchors=[]
    elif fault=='source_config':case.source.data['specification'].pop('http_observation_mapping_v177')
    elif fault=='request_hash':case.acq.data['request_hash']=protocol.digest(case.domain)
    elif fault=='domain_fields':case.domain['fields']=['temperature_c']
    elif fault=='body':case.row.data['freeze']['wire_frozen']['body']+=' '
    elif fault=='source_hash':case.source.validated_hash='c'*64
    elif fault=='package_hash':case.package.validated_hash='c'*64
    elif fault=='raw_evidence':case.evidence.sha256='c'*64
    elif fault=='actor':case.acq.data['actor_id']=uid(999)
    else:case.entity.external_id='another-target'
    with pytest.raises((HTTPException,ValueError)):check(case)


def test_input_schema_is_checked_against_wire_not_domain_and_is_not_widened(scene):
    case=scene
    from generic_agents.services import validate
    with pytest.raises(HTTPException) as error:validate(case.source.data['input_schema'],case.domain)
    assert error.value.status_code==422
    validate(case.source.data['input_schema'],case.row.data['freeze']['wire_frozen']['request'])
    assert check(case)['wire_request_hash']==case.acq.data['request_hash']


def test_true_legacy_has_no_new_family_proof_and_no_ledger_read(scene):
    case=scene;case.db.rows=[];case.db.anchors=[]
    case.acq.data.pop('http_observation_exchange_id_v177')
    case.source.data['specification'].pop('http_observation_mapping_v177')
    case.acq.data['request_hash']=protocol.digest(case.domain)
    assert check(case) is None and case.db.reads==['inventory','inventory']
