"""Real validators with one strict SQL-read boundary; no database or paid call.

The second read refreshes the *same* Capability ORM identity, as SQLAlchemy's
populate_existing row-lock read does. Comparing it with its previously returned
alias would falsely accept a changed model; only the registered reasoner pin is
an independent expectation. Root executes; this file does not prove concurrency
or adoption persistence against PostgreSQL.
"""
import copy
import socket
import subprocess
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BindParameter

from generic_agents import authored_workflow_store_v208 as store
from generic_agents import business_reasoning_v197 as reasoning, workflow_document_packet_v207 as packet
from generic_agents import models as m
from generic_agents.contracts import Manifest, digest


def identity(number): return '20800000-0000-4000-8000-' + format(number, '012x')


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def deny(*_args, **_kwargs): pytest.fail('model-lock contract must not connect, dispatch or spawn')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'getaddrinfo', deny)
    monkeypatch.setattr(subprocess, 'Popen', deny)
    monkeypatch.setattr(reasoning.transport, 'prepare', deny)


def capability(number, data):
    return m.Capability(id=identity(number), workspace_id=identity(90), kind=data['kind'],
        namespace=data['namespace'], name=data['name'], release_version=data['version'], status='published',
        version=1, data=copy.deepcopy(data), content_hash=digest(data), validated_hash=digest(data))


def profiles():
    model_data = Manifest(kind='foundation_model', namespace='authored_model_lock_v208', name='public_model',
        version='1.0.0', owner='公开边界测试', description='只有登记合同，不读取凭证或执行模型',
        allowed_agent_roles=['business'], input_schema={'type': 'object'}, output_schema={'type': 'object'},
        execution={'provider': 'qwen', 'model': 'public_profile_one', 'secret_ref': 'PUBLIC_TEST_NEVER_READ', 'max_tokens': 1024},
        data_policy={'cloud_allowed': True}, budget={'max_attempts': 1, 'max_concurrency': 1},
        dependencies=[], specification={}).model_dump(mode='json')
    model = capability(3, model_data)
    reasoner_data = Manifest(kind='tool', namespace='authored_model_lock_v208', name='public_reasoner',
        version='1.0.0', owner='公开边界测试', description='真实197登记合同，仍无执行或发送许可',
        allowed_agent_roles=['business'], input_schema=reasoning.input_schema(), output_schema=reasoning.output_schema(),
        execution={'adapter': reasoning.ADAPTER, 'model_profile_id': model.id, 'model_profile_hash': model.content_hash,
            'side_effect': False}, dependencies=[model.id], data_policy={'cloud_allowed': True},
        budget={'max_attempts': 1, 'max_concurrency': 1},
        specification={'schema': reasoning.CONTRACT, 'instruction': '比较实际证据与待核候选，不替人批准'}).model_dump(mode='json')
    packet_data = Manifest(kind='tool', namespace='authored_model_lock_v208', name='public_packet', version='1.0.0',
        owner='公开边界测试', description='真实207本地资料包合同', allowed_agent_roles=['data'],
        input_schema=packet.input_schema(), output_schema=packet.output_schema(),
        execution={'adapter': packet.ADAPTER, 'side_effect': True}, dependencies=[], data_policy={'cloud_allowed': False},
        budget={'max_attempts': 1, 'max_concurrency': 1}, specification={
            'schema': 'workflow-document-packet-contract-v207', 'operation': 'build_approved_document_packet'}).model_dump(mode='json')
    return capability(1, packet_data), capability(2, reasoner_data), model


class ReadBoundary:
    """Only exact Capability ID/workspace SELECTs are implemented, not a DB."""
    def __init__(self, rows, *, refresh_model):
        self.rows = {row.id: row for row in rows}; self.model = rows[2]
        self.refresh_model = refresh_model; self.reads = []; self.model_before_lock = None
        self.model_lock_reads = 0; self.write_calls = []
        self.created = {name: 0 for name in ('Adoption', 'Command', 'SetupRevision', 'Job', 'Invocation', 'CloudCall')}

    def scalar(self, statement):
        descriptions = statement.column_descriptions
        assert len(descriptions) == 1 and descriptions[0]['entity'] is m.Capability
        params = statement.compile().params
        # Full Capability reads include the real typed-payload owner/envelope
        # projection's json_build_object constants. Keep those exact, separate
        # from the two actual authorization predicates; do not guess their
        # generated parameter numbering or accept arbitrary extra predicates.
        projection = select(m.Capability).compile().params
        assert set(params) == set(projection) | {'id_1', 'workspace_id_1'}
        assert {key: value for key, value in params.items() if key in projection} == projection
        criteria = statement._where_criteria
        assert len(criteria) == 2
        observed = {}
        for criterion in criteria:
            assert criterion.operator is operators.eq and isinstance(criterion.right, BindParameter)
            assert criterion.left.table is m.Capability.__table__
            assert criterion.left.name in {'id', 'workspace_id'} and criterion.left.name not in observed
            observed[criterion.left.name] = criterion.right.value
        assert observed == {'id': params['id_1'], 'workspace_id': identity(90)}
        assert params['workspace_id_1'] == identity(90)
        selected = params['id_1']; assert selected in self.rows
        locked = statement._for_update_arg is not None
        refreshed = statement.get_execution_options().get('populate_existing') is True
        self.reads.append((selected, locked, refreshed))
        if selected == self.model.id:
            if not locked: self.model_before_lock = self.model
            else:
                assert refreshed and self.model_before_lock is self.model
                self.model_lock_reads += 1
                if self.refresh_model:
                    changed = copy.deepcopy(self.model.data); changed['execution']['model'] = 'public_profile_two'
                    self.model.data = changed
                    self.model.content_hash = self.model.validated_hash = digest(changed)
                    self.model.version += 1
        return self.rows[selected]

    def write(self, *args, **kwargs):
        self.write_calls.append((args, kwargs))
        pytest.fail('read-only pin validation attempted a mutation')
    add = add_all = execute = flush = commit = delete = write


def subject(refresh_model):
    caps = profiles(); db = ReadBoundary(caps, refresh_model=refresh_model)
    p = NS(workspace_id=identity(90), user_id=identity(91), role='operator')
    body = NS(packet_capability_id=caps[0].id, packet_capability_hash=caps[0].content_hash,
        reasoning=NS(capability_id=caps[1].id, capability_hash=caps[1].content_hash))
    return db, p, body, caps


def assert_read_only(db):
    assert db.model_lock_reads == 1 and not db.write_calls
    assert db.created == {'Adoption': 0, 'Command': 0, 'SetupRevision': 0, 'Job': 0, 'Invocation': 0, 'CloudCall': 0}


def test_locked_same_orm_model_refresh_rejects_original_reasoner_pin_without_any_write():
    db, p, body, caps = subject(True)
    registered_pin = caps[1].data['execution']['model_profile_hash']
    reasoner_before = copy.deepcopy(caps[1].data)
    with pytest.raises(HTTPException) as rejected: store._pins(db, p, body, lock=True)
    assert rejected.value.status_code == 409
    assert db.model_before_lock is caps[2] and db.rows[caps[2].id] is caps[2]
    assert caps[2].status == 'published' and caps[2].validated_hash == caps[2].content_hash == digest(caps[2].data)
    assert caps[2].content_hash != registered_pin and caps[1].data == reasoner_before
    assert_read_only(db)


def test_unchanged_locked_profile_passes_real_manifest_and_role_checks_without_any_write():
    db, p, body, caps = subject(False); before = [copy.deepcopy(row.data) for row in caps]
    pins = store._pins(db, p, body, lock=True)
    assert pins['data']['adapter'] == packet.ADAPTER and pins['data']['capability_hash'] == caps[0].content_hash
    assert pins['business']['adapter'] == reasoning.ADAPTER and pins['business']['capability_hash'] == caps[1].content_hash
    assert caps[2].content_hash == caps[1].data['execution']['model_profile_hash']
    assert [row.data for row in caps] == before
    assert_read_only(db)
