"""Quality time presentation from an already checked Observation.

Only public row lookup and entity naming are replaced. The actual sample
projection is called without DB access. These cases establish display shape,
not ingestion authority, source time truth or a real PG cold-chain result.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from generic_agents import quality_review_store_v152 as store
from generic_agents import threshold_study_views_v149 as study_views
from generic_agents.typed_payload_codec_v131 import canonical_json


WORKSPACE = '152abc00-0000-4000-8000-000000000021'
OBSERVATION = '152abc00-0000-4000-8000-000000000022'
ENTITY = '152abc00-0000-4000-8000-000000000023'
PACKAGE = '152abc00-0000-4000-8000-000000000024'
BASELINE = '152abc00-0000-4000-8000-000000000025'
INSTANCE = '152abc00-0000-4000-8000-000000000026'
FEEDBACK = '152abc00-0000-4000-8000-000000000027'


def sample_summary(monkeypatch, raw_time, sql_time):
    db = object(); principal = SimpleNamespace(workspace_id=WORKSPACE)
    observation = SimpleNamespace(id=OBSERVATION, version=4, observed_at=sql_time)
    feedback = SimpleNamespace(id=FEEDBACK, version=2,
        data={'label': 'should_alert', 'reason': '原核查理由保持可读'})
    basis = {'observation_id': OBSERVATION, 'instance_id': INSTANCE,
        'baseline_capability_id': BASELINE, 'entity_id': ENTITY,
        'field': '温度', 'field_label': '原登记温度', 'unit': '摄氏度',
        'actual_value': 21.0,
        'proof': {'original_request': {'observed_at': raw_time},
            'extractor': {'manifest': {'specification': {
                'managed_numeric_rule': {'package_id': PACKAGE}}}}}}
    original_basis = canonical_json(basis); calls = []

    def scoped(actual_db, model, identity, actual_principal):
        assert actual_db is db and actual_principal is principal
        assert model is store.Observation and identity == OBSERVATION
        calls.append('observation')
        return observation

    def entity_label(actual_db, actual_principal, identity, package):
        assert actual_db is db and actual_principal is principal
        assert identity == ENTITY and package == PACKAGE
        calls.append('entity-label')
        return '原核查对象'

    monkeypatch.setattr(store, 'scoped', scoped)
    monkeypatch.setattr(study_views, '_entity_label', entity_label)
    result = store._sample_summary(db, principal, 'evaluation', basis, feedback, {})
    assert calls == ['observation', 'entity-label']
    assert canonical_json(basis) == original_basis
    assert basis['proof']['original_request']['observed_at'] == raw_time
    assert result == {'group': 'evaluation', 'observation_id': OBSERVATION,
        'observation_version': 4, 'instance_id': INSTANCE,
        'baseline_capability_id': BASELINE,
        'observed_at': sql_time.isoformat() if sql_time is not None else None,
        'entity_id': ENTITY, 'entity_name': '原核查对象', 'field': '温度',
        'field_label': '原登记温度', 'unit': '摄氏度', 'value': 21.0,
        'feedback_id': FEEDBACK, 'feedback_version': 2, 'label': 'should_alert',
        'reason': '原核查理由保持可读', 'eligible': True, 'issue': None}
    return result


@pytest.mark.parametrize('raw_time,sql_text', [
    ('2026-10-21T09:30:00+0800', '2026-10-21T01:30:00+00:00'),
    ('2026-10-21 09:30:00+08:00', '2026-10-21T09:30:00+08:00'),
    ('2026-10-21T01:30:00Z', '2026-10-21T01:30:00+00:00'),
    ('2026-10-21T09:30:00+08:00', '2026-10-20T18:30:00-07:00'),
    ('2026-10-20T18:30:00-07:00', '2026-10-21T09:30:00+08:00'),
], ids=['compact-offset', 'space-separator', 'utc-z',
        'sql-negative-offset', 'sql-positive-offset'])
def test_quality_sample_uses_standard_sql_aware_display_without_rewriting_original_basis(
        monkeypatch, raw_time, sql_text):
    sql_time = datetime.fromisoformat(sql_text)
    # The authority reader has established this equality before projection.
    # The projection must use the SQL display, not repair the original proof.
    assert datetime.fromisoformat(raw_time) == sql_time
    result = sample_summary(monkeypatch, raw_time, sql_time)
    assert 'T' in result['observed_at']
    assert result['observed_at'] == sql_text


def test_quality_sample_keeps_unknown_sql_time_unknown_without_fabricating_time(monkeypatch):
    result = sample_summary(monkeypatch, None, None)
    assert result['observed_at'] is None
