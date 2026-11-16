from dataclasses import replace

import pytest
from fastapi import HTTPException

from generic_agents.device_simulator_v175 import DeviceLedger
from generic_agents.uav_simulated_missions import SimulatedMissionService
from generic_agents.uav_vendor_adapter_registry import (AdapterManifest, AdapterRegistry, AdapterUnavailable,
    ProcurementSimulatorAdapter, procurement_simulator_manifest)


def test_registered_simulator_goes_through_approved_mission_and_receipt(tmp_path):
    ledger=DeviceLedger(tmp_path/'device.sqlite')
    registry=AdapterRegistry()
    descriptor=registry.register(procurement_simulator_manifest(),ProcurementSimulatorAdapter(ledger))
    implementation=registry.resolve_simulation(descriptor['adapter_id'],descriptor['fingerprint'])
    service=SimulatedMissionService(tmp_path/'mission.sqlite',implementation.ledger)
    mission=service.prepare('test-workspace','模拟事件',[117.6,24.5],[117.601,24.501])
    assert mission['status']=='awaiting_approval'
    assert ledger.device('sim-dock-01')['state']['phase']=='idle'
    with pytest.raises(HTTPException):
        service.approve_and_start('test-workspace',mission['id'],manager='admin',plan_hash='changed')
    started=service.approve_and_start('test-workspace',mission['id'],manager='admin',plan_hash=mission['plan_hash'])
    assert started['status']=='flying' and started['receipt']['simulation'] is True
    assert service.refresh('test-workspace',mission['id'])['receipt']['request_id']==started['request_id']


def test_untrusted_or_real_vendor_implementations_cannot_dispatch():
    registry=AdapterRegistry()
    manifest=AdapterManifest('vendor.flight-driver','1.2.3','Vendor','vendor-protocol','physical',
        ('read_status','prepare_mission','start_mission','read_receipt'))
    descriptor=registry.register(manifest)
    assert descriptor['executable'] is False
    with pytest.raises(AdapterUnavailable):
        registry.resolve_simulation(manifest.adapter_id,descriptor['fingerprint'])
    with pytest.raises(AdapterUnavailable):
        AdapterRegistry().register(manifest,object())
    with pytest.raises(AdapterUnavailable):
        AdapterRegistry().register(replace(manifest,environment='simulation'),object())


def test_manifest_binding_is_frozen_and_cannot_be_overwritten(tmp_path):
    registry=AdapterRegistry();manifest=procurement_simulator_manifest()
    descriptor=registry.register(manifest,ProcurementSimulatorAdapter(DeviceLedger(tmp_path/'device.sqlite')))
    descriptor['capabilities']=()
    assert registry.descriptor(manifest.adapter_id)['capabilities']
    with pytest.raises(AdapterUnavailable):
        registry.resolve_simulation(manifest.adapter_id,'0'*64)
    with pytest.raises(ValueError):
        registry.register(replace(manifest,version='2.0.0'),ProcurementSimulatorAdapter(DeviceLedger(tmp_path/'device.sqlite')))
