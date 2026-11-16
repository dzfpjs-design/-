"""Actual Python JSON numeric spelling versus JS lossless-wire validation."""
import copy
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace as NS
from uuid import UUID

from fastapi import HTTPException
import pytest
from generic_agents.contracts import digest
from generic_agents import report_full_semantic_graph_wire as wire
from tests.test_report_full_semantic_graph_revision import root_contract


def test_exact_python_numeric_types_and_codepoint_keys_survive_javascript_transport():
    node=os.environ['GA_V115_NODE'];bridge=Path(os.environ['GA_V115_WIRE_BRIDGE'])
    body={'nested':{'values':[1,1.0,-0.0,0.5,1e-7,1e+21,0.00001],
        '😀':{'minimum':1.0},'\ue000':{'value':-0.25},'line':'原始\u2028文字'},'hash':'a'*64}
    envelope=wire.canonical_wire(body)
    result=subprocess.run([node,str(bridge)],input=json.dumps(envelope,ensure_ascii=False),text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr[-2000:]
    hashes=json.loads(result.stdout)
    assert hashes=={'whole':digest(body),'unsealed':digest({k:v for k,v in body.items() if k!='hash'}),
        'nested':digest(body['nested']),'array':digest(body['nested']['values'])}
    assert '1.0' in envelope['canonical'] and '-0.0' in envelope['canonical']


def test_canonical_transport_is_bounded_and_never_encodes_nonfinite_or_unserializable(monkeypatch):
    for bad in [float('nan'),float('inf'),{1,2},'\ud800']:
        with pytest.raises(HTTPException):wire.canonical_wire({'bad':bad})
    monkeypatch.setattr(wire,'MAX_CANONICAL_BYTES',8)
    with pytest.raises(HTTPException) as caught:wire.canonical_wire({'text':'过长响应'})
    assert caught.value.status_code==413


def test_hash_adapter_uses_actual_typed_command_hash_without_preview_or_live_dependencies(monkeypatch):
    p,row,_=root_contract();command=copy.deepcopy(row.data['command']);identity=row.handoff_id
    calls=[]
    monkeypatch.setattr(wire,'_shared_handoff',lambda db,actor,identity:calls.append((db,actor,identity)))
    def forbidden(*args,**kwargs):raise AssertionError('hash-only path entered live authority')
    monkeypatch.setattr(wire.base,'preview',forbidden);monkeypatch.setattr(wire.base,'adopt',forbidden)
    body=wire.CommandHash(kind='root',command=copy.deepcopy(command))
    result=wire.command_hash(UUID(identity),body,p,'read-only-double')['value']
    typed=wire.base.Adopt.model_validate(command)
    assert result['command_hash']==wire.base.command_hash(p,identity,typed)
    assert result['command']==typed.model_dump(mode='json')
    assert len(calls)==1
