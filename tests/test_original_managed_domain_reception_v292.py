"""Actual V177 mapping/ledger and V270 manifest, with explicit authority IO.

validate_source's issuance/member/recipe/cipher receipt authority is a supplied
IO boundary here, not real PG or device permission. The original domain/wire
request validators, immutable ledger checks and input schema validation run.
No source, capability, receipt, Observation or acquisition is issued or changed.
"""
import copy
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import device_gateway_sources_v270 as sources
from generic_agents import http_observation_exchange_v177 as ledger
from generic_agents import http_observation_mapping_v177 as protocol
from generic_agents.services import validate
from tests.test_original_http_mapped_reception_v177 import (
    scene, check, no_external_execution, P,
)


@pytest.fixture
def authority(scene, monkeypatch):
    case=scene; scope=case.row.data['freeze']['mapping']['mapping']['scope']
    context={'capability':case.source,
        'binding':copy.deepcopy(case.source.data['specification'][sources.BINDING_KEY]),
        'environment':NS(id=scope['environment_id']), 'instance':NS(id=scope['instance_id']),
        'release':NS(id=scope['release_id'],instance_id=scope['instance_id'],package_id=scope['package_id']),
        'package':case.package}
    calls=[]
    def receipt(actual_db,principal,capability,*,purpose):
        assert actual_db is case.db and principal is P and capability is case.source
        assert purpose=='receipt'
        calls.append(purpose)
        return context
    monkeypatch.setattr(sources,'validate_source',receipt)
    return NS(case=case,context=context,calls=calls)


@pytest.mark.parametrize('scene',['managed'],indirect=True)
def test_only_issued_managed_domain_contract_uses_logical_fields_and_preserves_wire(authority):
    case=authority.case
    before=(copy.deepcopy(case.source.data),protocol.canonical(ledger.snapshot(case.row)))
    assert case.source.data==sources.manifest(authority.context['binding'])
    validate(case.source.data['input_schema'],case.domain)
    with pytest.raises(HTTPException) as raw_error:
        validate(case.source.data['input_schema'],case.row.data['freeze']['wire_frozen']['request'])
    assert raw_error.value.status_code==422
    proof=check(case)
    assert authority.calls==['receipt']
    assert proof['domain_request_hash']==protocol.digest(case.domain)
    assert proof['wire_request_hash']==case.acq.data['request_hash']
    assert before==(case.source.data,protocol.canonical(ledger.snapshot(case.row)))
    assert case.row.observation_id is None


@pytest.mark.parametrize('scene',['managed'],indirect=True)
def test_managed_marker_cannot_replace_original_issuance_or_fall_back_to_wire(authority,monkeypatch):
    refused=HTTPException(409,'explicit independent issued-source receipt proof missing')
    def unavailable(*args,**kwargs):
        assert kwargs=={'purpose':'receipt'}
        raise refused
    monkeypatch.setattr(sources,'validate_source',unavailable)
    with pytest.raises(HTTPException) as error:check(authority.case)
    assert error.value is refused


def test_independent_issuance_cannot_downgrade_to_unmanaged_when_all_family_markers_are_absent(scene,monkeypatch):
    # This original unmanaged fixture has a coherent raw schema and ledger.
    # Supplying only the independent issuance inventory must route it into the
    # strict receipt-authority check; that check rejects the copied manifest.
    scene.db.issued_source_audit_id='00000000-0000-4000-8000-000000009998'
    assert scene.source.namespace != sources.NAMESPACE and not sources.applies(scene.source.data)
    calls=[]
    refused=HTTPException(409,'explicit independent issuance cannot authorize a copied manifest')
    def unavailable(actual_db,principal,capability,*,purpose):
        assert actual_db is scene.db and principal is P and capability is scene.source
        assert purpose=='receipt';calls.append(purpose)
        raise refused
    monkeypatch.setattr(sources,'validate_source',unavailable)
    with pytest.raises(HTTPException) as error:check(scene)
    assert error.value is refused and calls==['receipt']


@pytest.mark.parametrize('scene',['managed'],indirect=True)
@pytest.mark.parametrize('fault',['source_id','source_hash','mapping','environment','instance','release','package'])
def test_receipt_authority_must_match_the_exact_original_source_and_exchange_scope(authority,fault):
    context=authority.context
    if fault in {'source_id','source_hash'}:
        context['capability']=copy.deepcopy(context['capability'])
        if fault=='source_id':context['capability'].id='00000000-0000-4000-8000-000000009999'
        else:context['capability'].content_hash='f'*64
    elif fault=='mapping':context['binding']['mapping']['links']['temperature_c']['field_key']='another_domain_field'
    elif fault=='package':
        context['package']=copy.deepcopy(context['package']);context['package'].content_hash='f'*64
    else:context[fault]=copy.deepcopy(context[fault]);context[fault].id='00000000-0000-4000-8000-000000009999'
    with pytest.raises(HTTPException):check(authority.case)


@pytest.mark.parametrize('scene',['managed'],indirect=True)
@pytest.mark.parametrize('fault',['domain_fields','wire_body'])
def test_managed_contract_does_not_weaken_original_mapping_or_frozen_wire(authority,fault):
    case=authority.case
    if fault=='domain_fields':case.domain['fields']=['temperature_c']
    else:case.row.data['freeze']['wire_frozen']['body']+=' '
    with pytest.raises((HTTPException,ValueError)):check(case)
