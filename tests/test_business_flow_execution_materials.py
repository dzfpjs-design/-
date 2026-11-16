"""The legacy material gate changes only after actual full-source authority."""
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from generic_agents import instance_compiler as compiler
from generic_agents import business_flow_execution_source as source
from generic_agents import business_flow_execution_adoption as adoption
from generic_agents.contracts import digest


def material():
    return {'id':'original','version':2,'hash':'a'*64,'evidence_id':'saved_evidence',
        'status':'needs_input','archived':False,'blocks_hash':digest([]),
        'business_flow_execution_sources':[{'adoption_id':'actual_origin'}]}


def test_full_source_status_requires_origin_actual_bytes_and_exact_parse_generation(monkeypatch):
    read=[]
    monkeypatch.setattr(compiler,'evidence_bytes',lambda db,p,identity:read.append(identity) or (None,b''))
    frozen=material();row=SimpleNamespace(id='original',version=2,sha256='a'*64,
        evidence_id='saved_evidence',status='needs_input',archived=False,data={'blocks':[]})
    monkeypatch.setattr(compiler,'scoped',lambda *args:row)
    seen=[]
    monkeypatch.setattr(adoption,'checked_origin',lambda db,p,origin:seen.append(origin) or {'source':'actual_package'})
    def current(db,p,package):
        assert package=='actual_package'
        return [{k:frozen[k] for k in ('id','version','hash','evidence_id')}]
    monkeypatch.setattr(source,'current_materials',current)
    compiler.verify_material_snapshots(None,None,[frozen]);assert seen==frozen['business_flow_execution_sources']
    assert read==['saved_evidence']
    row.version=3
    with pytest.raises(HTTPException):compiler.verify_material_snapshots(None,None,[frozen])
    row.version=2;row.archived=True
    with pytest.raises(HTTPException):compiler.verify_material_snapshots(None,None,[frozen])


def test_legacy_needs_input_or_forged_empty_origin_remains_blocked(monkeypatch):
    frozen=material();row=SimpleNamespace(id='original',version=2,sha256='a'*64,
        evidence_id='saved_evidence',status='needs_input',archived=False,data={'blocks':[]})
    monkeypatch.setattr(compiler,'scoped',lambda *args:row)
    legacy={k:v for k,v in frozen.items() if k!='business_flow_execution_sources'}
    with pytest.raises(HTTPException):compiler.verify_material_snapshots(None,None,[legacy])
    frozen['business_flow_execution_sources']=[]
    with pytest.raises(HTTPException):compiler.verify_material_snapshots(None,None,[frozen])


def test_source_marker_does_not_override_damaged_or_other_material(monkeypatch):
    frozen=material()
    monkeypatch.setattr(adoption,'checked_origin',lambda *args:{'source':'actual_package'})
    monkeypatch.setattr(source,'current_materials',lambda *args:[{'id':'another_material','version':2,'hash':'a'*64,'evidence_id':'saved_evidence'}])
    with pytest.raises(HTTPException):source.verify_material_snapshot(None,None,frozen)
