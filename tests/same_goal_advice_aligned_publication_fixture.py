"""Publish the synthetic candidate ports against the actual object schema."""
import copy

import pytest

from generic_agents.instance_compiler import identifier
from tests import test_auto_evaluation_real_journey_fixture_v356 as journey
from tests.test_same_goal_advice_manual_append_pg import deepseek_3600_publication as original


@pytest.fixture
def deepseek_3600_publication(managed, monkeypatch):
    previous = journey.prepared_decision
    def aligned(managed_fixture):
        body, pins, model, candidate, extractor, material = previous(managed_fixture)
        body = copy.deepcopy(body)
        replacements = {'temperature_c': identifier('field', '气温'),
            'relative_humidity_pct': identifier('field', '相对湿度')}
        changed = set()
        for spec in body['candidate_specs']:
            for pin in spec['capability_pins']:
                for port in pin['ports']:
                    if port['field'] in replacements:
                        old = port['field']; port['field'] = replacements[old]
                        port['pointer'] = '/requested_observation/' + port['field']; changed.add(old)
        assert changed == set(replacements), 'fixture candidate targets changed; do not guess replacements'
        return body, pins, model, candidate, extractor, material
    monkeypatch.setattr(journey, 'prepared_decision', aligned)
    return original.__wrapped__(managed, monkeypatch)
