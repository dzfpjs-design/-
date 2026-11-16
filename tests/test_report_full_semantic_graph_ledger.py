"""Root-run real graph adoption ledger regressions, with one shared source seed.

Five tampered commands are independently resealed in one test. Actual accepted
SetupRevision rows are never edited; no source/permission/adoption gate is stubbed.
"""
import copy
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import SetupRevision
from generic_agents import report_full_semantic_graph_adoption as graph
from generic_agents.report_full_semantic_graph_models import FullSemanticGraphAdoption
from tests.test_full_semantic_graph_journey_v114 import (
    graph_seed, graph_command, configured, tool,
    v7_scene, reading_scene, local_runtime, full_source_scene,
)
from tests.test_instance_workspace import save
from tests.test_report_evidence_pipeline import reply


@pytest.fixture
def ledger_seed(graph_seed):
    seed = graph_seed; client = seed['client']; view, member, _ = configured(seed)
    configuration = copy.deepcopy(view['configuration'])
    name = seed['record']['design']['result']['businesses'][0]['name']
    configuration['automation']['policies'][name]['rules']['data'] = {'mode': '自动运行'}
    save(client, configuration, review_automation=True)
    current = reply(client.get('/api/setup/draft'))
    cap = tool(client, agent='business')
    command = graph_command(seed, member, current['configuration'], cap)
    path = '/api/setup/full-semantic-graphs/' + seed['record']['id']
    preview = reply(client.post(path + '/preview', json=command))
    body = {**command, 'schema': 'report-full-semantic-graph-command-v3',
        'request_id': str(uuid4()), 'preview_hash': preview['preview_hash'], 'consent': True,
        'semantic_mode': 'workspace_workflow_design'}
    accepted = reply(client.post(path + '/adopt', json=body))
    return {**seed, 'graph_path': path, 'graph_body': body, 'graph_acceptance': accepted}


def reseal_command(row, data):
    """Recompute all self-reported hashes, including the changed command origin."""
    owner = type('Owner', (), {'workspace_id': row.workspace_id, 'user_id': row.actor_id})()
    record = data['record']; body = graph.Adopt.model_validate(data['command'])
    data['command']['preview_hash'] = graph.make_preview(owner, row.handoff_id, body, record['binding'])['preview_hash']
    body = graph.Adopt.model_validate(data['command'])
    record['command_hash'] = graph.command_hash(owner, row.handoff_id, body)
    record['preview_hash'] = body.preview_hash
    record['origin'] = graph.origin_value(row.id, record['binding'], command_hash=record['command_hash'])
    record = graph.seal({key: value for key, value in record.items() if key != 'record_hash'}, 'record_hash')
    data['record'] = record
    data['receipt'] = graph.seal({key: record[key] for key in ('id', 'workspace_id', 'actor_id',
        'handoff_id', 'request_id', 'command_hash', 'preview_hash', 'accepted_revision',
        'configuration_hash', 'record_hash')}, 'receipt_hash')
    return graph.seal({key: value for key, value in data.items() if key != 'ledger_hash'}, 'ledger_hash')


def test_real_graph_ledger_rejects_five_resealed_command_changes_without_rewriting_accepted_history(ledger_seed):
    seed = ledger_seed; client = seed['client']; accepted = seed['graph_acceptance']
    identity = accepted['receipt']['id']; p = seed['scene']['p']
    recovery = seed['graph_path'] + '/requests/' + seed['graph_body']['request_id']
    with Session(engine()) as db:
        row = db.get(FullSemanticGraphAdoption, identity); original = copy.deepcopy(row.data)
        assert graph.checked(db, p, row) == original
        query = select(SetupRevision.data).where(SetupRevision.workspace_id == p.workspace_id,
            SetupRevision.draft_id == row.configuration_id,
            SetupRevision.version.in_([seed['graph_body']['configuration_revision'],
                accepted['receipt']['accepted_revision']])).order_by(SetupRevision.version)
        history_hashes = [digest(value) for value in db.scalars(query)]
        assert len(history_hashes) == 2
        for mutation in ('graph', 'owners', 'role', 'external', 'action'):
            forged = copy.deepcopy(original)
            command = forged['command']
            if mutation == 'graph':
                # The actual binding remains unchanged and valid, while the
                # alleged consent command points to a different control graph.
                command['graph']['id'] += '_other'
            elif mutation == 'owners':
                command['owner_bindings'][0]['reason'] += ' 被替换的同意说明'
            elif mutation == 'role':
                role = next(iter(command['role_bindings']))
                command['role_bindings'][role] = str(uuid4())
            elif mutation == 'external':
                command['external_node_ids'] = ['b']
            else:
                assert command['node_actions']['b'] == 'data'
                command['node_actions']['b'] = 'observe'
            row.data = reseal_command(row, forged); db.commit()
            try:
                with pytest.raises(HTTPException) as error:
                    graph.checked(db, p, row)
                assert error.value.status_code == 409
                assert client.get(recovery).status_code == 409
                assert [digest(value) for value in db.scalars(query)] == history_hashes
            finally:
                row.data = copy.deepcopy(original); db.commit()
            assert reply(client.get(recovery))['receipt'] == accepted['receipt']
        assert graph.checked(db, p, row) == original
        assert [digest(value) for value in db.scalars(query)] == history_hashes
