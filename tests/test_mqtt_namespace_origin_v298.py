"""Classification seam only; complete MQTT proof is not replaced by these spies."""
import pytest
from fastapi import HTTPException

from generic_agents import mqtt_namespace_origin_v298 as mqtt
from generic_agents import simulation_origin_v176 as subject
from tests.test_simulation_origin_v176 import scene


@pytest.mark.parametrize('marker', ['data', 'source', 'policy', 'linked_sample'])
def test_explicit_v176_or_independent_linked_sample_never_enters_mqtt_exception(scene, monkeypatch, marker):
    s = scene; original = s.observation.data.pop('simulation_origin')
    s.source.data['specification'] = {}; s.source.data['execution'] = {'adapter': 'mqtt_source'}
    s.source.data['data_policy'] = {}
    if marker != 'linked_sample': s.db.rows[subject.Sample] = []
    if marker == 'data': s.observation.data['simulation_origin'] = original
    if marker == 'source': s.source.data['specification']['simulation_environment_id'] = s.env.id
    if marker == 'policy': s.source.data['data_policy']['simulation'] = True
    monkeypatch.setattr(mqtt, 'is_independent_mqtt', lambda *_args, **_kwargs: pytest.fail('explicit V176 skipped'))
    with pytest.raises(HTTPException): subject.guard(s.db, s.p, s.observation)


@pytest.mark.parametrize('authenticated', [True, False])
def test_namespace_only_seam_preserves_actual_execution_flag_and_never_invents_sample(scene, monkeypatch, authenticated):
    s = scene; s.observation.data.pop('simulation_origin'); s.db.rows[subject.Sample] = []
    s.source.data['specification'] = {}; s.source.data['execution'] = {'adapter': 'mqtt_source'}
    s.source.data['data_policy'] = {}; calls = []
    def proof(db, principal, obs, **kwargs):
        assert db is s.db and principal is s.p and obs.id == s.observation.id
        assert kwargs['source'].id == s.source.id and kwargs['entity'].id == s.entity.id
        assert [item.id for item in kwargs['environments']] == [s.env.id]
        calls.append(kwargs['execution']); return authenticated
    monkeypatch.setattr(mqtt, 'is_independent_mqtt', proof)  # Explicit protocol IO boundary, not actual custody.
    if authenticated:
        assert subject.guard(s.db, s.p, s.observation, execution=True) is None
    else:
        with pytest.raises(HTTPException): subject.guard(s.db, s.p, s.observation, execution=True)
    assert calls == [True] and not s.db.rows[subject.Sample]
