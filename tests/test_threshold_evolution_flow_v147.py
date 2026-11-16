"""The first V147 product red follows real published rules and received inputs.

Root supplies threshold_cases from one retained prepare_threshold_cases call.
No new V147 module is imported, and neither original observation is rerun.
"""
from uuid import uuid4


def test_received_nomatch_can_save_explicit_should_alert_feedback(client, threshold_cases):
    cases = threshold_cases
    original = cases['observations']['nomatch']
    record = original['record']
    assert record['status'] == 'processed' and record['data_received'] is True
    assert original['processing']['invocation']['data']['output']['anomalies'] == []
    assert original['processing']['job']['status'] == 'succeeded'
    assert record['review_basis']['integrity_issue'] is None
    body = {'request_id': str(uuid4()), 'observation_id': record['id'],
            'rule_id': cases['rule_id'], 'expected_observation_version': record['version'],
            'label': 'should_alert',
            'reason': '独立合成判断：原值19被基线20漏过，应提示核查；不作为现场或林业质量金标'}
    response = client.post('/api/local-rules/evolution/feedback', json=body)
    assert response.status_code == 201, response.text
    assert isinstance(response.json(), dict)
