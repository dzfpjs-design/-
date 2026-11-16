"""Actual V177 HTTP, extraction, independent ACK and local receipt custody.

The stricter confirmed query excludes a real unreviewed result. Its collection
receipt remains authentic without becoming admissible data. The receipt list
below is assembled from real rows; this is not a V288 adoption/creator or V271
producer proof. No verifier, receiver, supplier or query engine is replaced.
"""
import copy

from sqlalchemy.orm import Session

from generic_agents import services, instance_data_receipts
from generic_agents import workflow_goal_evaluation_store_v288 as subject
from generic_agents.contracts import EvidenceRequirement, digest
from generic_agents.models import Observation, Task, CloudCall
from generic_agents.temporal import instant
from tests.test_acquisition_ack_settlement_pg_v245 import (
    simulated_supplier, mapped_scene, extract, ack, finish, counts,
)


def test_actual_received_http_observation_remains_an_ack_when_confirmed_query_excludes_it(client, simulated_supplier):
    s = mapped_scene(client, simulated_supplier)
    extract(s); finish(s, ack(s))
    with Session(s.bound) as db:
        task = db.get(Task, s.task['id']); observation = db.get(Observation, s.observation_id)
        original_task = copy.deepcopy(task.data); original_observation = copy.deepcopy(observation.data)
        before = counts(db, s)
        requirement = EvidenceRequirement.model_validate({'source': 'observations',
            'time': {'mode': 'latest', 'timezone': 'Asia/Shanghai', 'max_age_seconds': 86400},
            'minimum_verification': 'confirmed'}).model_dump(mode='json')
        report = services.query_evidence(db, s.p, task.data['entity_ids'], task.data['goal_spec']['required_fields'],
            learning=False, include_trusted_cases=False, requirement=requirement)
        assert report['status'] == 'insufficient' and report['observation_evidence'] == []
        assert any(item.get('observation_id') == observation.id for item in report['excluded_evidence'])
        received = instance_data_receipts.observation_receipt(db, s.p, observation)
        reception = received['data_reception']
        resense = {'service_receipt': {'observations': [{'acquisition_id': s.acquisition_id,
            'observation_id': observation.id, 'data_receipt_hash': digest(received)}],
            'followup_observations': []}}
        frozen = copy.deepcopy(report)
        refs = subject._acks(db, s.p, task, {'result': report}, instant(report['evaluated_at']), report['resolved_time'], resense)
        assert len(refs) == 1 and refs[0]['query_admitted'] is False
        assert refs[0]['observation_id'] == observation.id and refs[0]['origin_task_id'] == task.id
        assert refs[0]['data_reception_hash'] == reception['receipt_hash']
        assert report == frozen and observation.data == original_observation and task.data == original_task
        assert counts(db, s) == before and before['CloudCall'] == 0
        assert not db.new and not db.dirty and not db.deleted
    assert simulated_supplier.calls == [('POST', '/acquisitions')]
