"""Public-HTTP V147 seed; only Root callbacks execute the exact real jobs.

Call prepare_threshold_cases once in a fresh, authenticated workspace and retain
its JSON-safe checkpoint.  Tests consume that checkpoint instead of rerunning
observations.  Synthetic integers prove the mechanism, not forestry quality.
"""
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from generic_agents.contracts import digest
from tests.test_instance_workspace import enable, prepared
from tests.test_vertical_slice import manifest, publish


def _ok(response, status=200):
    assert response.status_code == status, response.text
    value = response.json()
    assert isinstance(value, dict), value
    return value


def _published_rule(client, entity_id, field, unit, threshold):
    body = {
        'request_id': str(uuid4()), 'title': f'合成数量阈值 {threshold}',
        'entity_id': entity_id,
        'rationale': '独立合成整数只验证受控阈值机制，不证明林业异常质量',
        'rules': [{'id': 'count_high', 'field': field, 'unit': unit,
                   'operator': 'gt', 'threshold': threshold}],
    }
    created = _ok(client.post('/api/local-rules', json=body), 201)
    check_body = {'expected_version': created['version'], 'values': {field: 19}}
    checked = _ok(client.post('/api/local-rules/' + created['id'] + '/check', json=check_body))
    assert checked['last_check']['results'][0]['matched'] is (19 > threshold)
    publish_body = {'expected_version': checked['version'],
                    'reason': '明确发布合成单字段本地规则，尚未授权策略试用', 'confirm': True}
    result = _ok(client.post('/api/local-rules/' + created['id'] + '/publish', json=publish_body))
    assert result['status'] == 'published' and result['context_available'] is True
    assert result['rules'] == body['rules'] and result['entity_id'] == entity_id
    return {'view': result, 'commands': [
        {'method': 'POST', 'path': '/api/local-rules', 'body': body, 'response': created},
        {'method': 'POST', 'path': '/api/local-rules/' + created['id'] + '/check',
         'body': check_body, 'response': checked},
        {'method': 'POST', 'path': '/api/local-rules/' + created['id'] + '/publish',
         'body': publish_body, 'response': result},
    ]}


def _processing_proof(proof, observation_id, baseline, body, matched):
    assert isinstance(proof, dict), proof
    job, observation, invocation = (proof[key] for key in ('job', 'observation', 'invocation'))
    assert job['kind'] == 'process_reported_observation' and job['status'] == 'succeeded'
    assert observation['id'] == observation_id and observation['status'] == 'processed'
    assert invocation['status'] == 'succeeded' and invocation['capability_id'] == baseline['id']
    data = observation['data']; call = invocation['data']
    assert data['invocation_id'] == invocation['id'] and call['job_id'] == job['id']
    assert isinstance(call['lease_token'], str) and call['lease_token']
    assert call['capability_hash'] == baseline['definition_hash']
    assert call['input_hash'] == digest({'attributes': body['attributes']})
    assert data['input_hash'] == digest(data['original_request'])
    original = data['original_request']
    for key in ('source_id', 'source_record_id', 'processing_version', 'entity_id', 'extractor_id',
                'observed_at', 'business_day', 'startup_mode', 'attributes'):
        assert digest(original[key]) == digest(body[key]), key
    rule = baseline['rules'][0]
    anomalies = ([{'field': rule['field'], 'value': body['attributes'][rule['field']],
                   'rule_id': rule['id'], 'severity': 'warning'}] if matched else [])
    output = {'attributes': body['attributes'], 'anomalies': anomalies,
              'method': 'registered_numeric_rules'}
    assert digest(call['output']) == digest(output)
    assert digest(data['extracted']) == digest(output)
    return proof


def prepare_threshold_cases(client, run_exact_reported_job, deliver_scoped):
    """Prepare once; both callbacks are actual Root-owned worker/receiver paths.

    run_exact_reported_job(observation_id, workspace_id) runs only that newly
    created process_reported_observation Job once and returns JSON-safe actual
    job{id,kind,status}, observation{id,version,status,data}, invocation
    {id,capability_id,status,data}, receipt{...}.  Its pre-delivery receipt may
    still be empty.  deliver_scoped(workspace_id, observation_id) delivers only
    that scope; its optional JSON-safe proof is retained without fabrication.
    """
    account = _ok(client.get('/api/me'))
    assert account['role'] == 'admin'
    workspace_id = account['workspace_id']
    release, compilation, checked_configuration = prepared(client)
    activation = enable(client, release)
    kind = next(item for item in release['snapshot']['object_types'] if item['name'] == '档案条目')
    number = next(item for item in kind['fields'] if item['label'] == '数量')
    title = next(item for item in kind['fields'] if item['label'] == '标题')
    schema = release['snapshot']['entity_schemas'][kind['key']]['properties'][number['key']]
    assert number['kind'] == '整数' and schema['type'] == 'integer'
    assert title['kind'] == '文本'
    field = number['key']; unit = schema.get('unit')
    object_path = '/api/instances/' + release['instance_id'] + '/objects'
    objects = {}
    object_commands = []
    for name, label in (('training', '合成阈值训练对象'), ('evaluation', '独立合成评估对象')):
        body = {'release_id': release['id'], 'object_type': kind['key'],
                'request_id': str(uuid4()), 'attributes': {title['key']: label, field: 10}}
        value = _ok(client.post(object_path, json=body), 201)
        objects[name] = value
        object_commands.append({'method': 'POST', 'path': object_path, 'body': body, 'response': value})
    assert objects['training']['id'] != objects['evaluation']['id']
    baseline = _published_rule(client, objects['training']['id'], field, unit, 20)
    candidate = _published_rule(client, objects['training']['id'], field, unit, 18)
    assert baseline['view']['id'] != candidate['view']['id']
    source_manifest = manifest('source', name='threshold_source_' + uuid4().hex,
        description='合成数值人工登记来源', allowed_agent_roles=['perception'],
        execution={'adapter': 'upload'}, specification={'timezone': 'Asia/Shanghai'},
        data_policy={'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False})
    source = publish(client, source_manifest)
    path = '/api/workspace/instances/' + release['instance_id'] + '/reported-observations'
    observations = {}
    for name, value, matched in (('matched', 21, True), ('nomatch', 19, False)):
        clock = datetime.now(timezone.utc) - timedelta(seconds=10)
        body = {'release_id': release['id'], 'source_id': source['id'],
                'source_record_id': str(uuid4()), 'processing_version': '1.0.0',
                'entity_id': objects['training']['id'], 'extractor_id': baseline['view']['id'],
                'observed_at': clock.isoformat(),
                'business_day': clock.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat(),
                'startup_mode': 'manual', 'attributes': {field: value}}
        saved = _ok(client.post(path, json=body), 201)
        proof = _processing_proof(run_exact_reported_job(saved['id'], workspace_id),
                                  saved['id'], baseline['view'], body, matched)
        delivery = deliver_scoped(workspace_id, saved['id'])
        records = _ok(client.get(path))['records']
        record = next(item for item in records if item['id'] == saved['id'])
        assert record['status'] == 'processed' and record['data_received'] is True
        assert record['data_delivery']['status'] == 'received'
        assert record['extractor_id'] == baseline['view']['id']
        basis = record['review_basis']
        assert basis['integrity_issue'] is None and record['can_verify'] is True
        assert digest(basis['original_attributes']) == digest(body['attributes'])
        assert digest(basis['extracted_attributes']) == digest(body['attributes'])
        assert record['version'] == proof['observation']['version']
        observations[name] = {'command': {'method': 'POST', 'path': path, 'body': body,
                                        'response': saved},
                              'processing': proof, 'delivery': delivery, 'record': record,
                              'matched': matched}
    signals_path = '/api/workspace/instances/' + release['instance_id'] + '/signals'
    signals = _ok(client.get(signals_path))['records']
    assert all(item['integrity_issue'] is None and isinstance(item['basis'], dict) for item in signals)
    matching = [item for item in signals
                if item['basis']['observation_id'] == observations['matched']['record']['id']]
    assert len(matching) == 1 and matching[0]['integrity_issue'] is None
    assert not [item for item in signals
                if item['basis']['observation_id'] == observations['nomatch']['record']['id']]
    assert digest(matching[0]['basis']['extracted']) == digest(
        observations['matched']['processing']['invocation']['data']['output'])
    checkpoint = {'schema': 'threshold-evolution-test-checkpoint-v147', 'account': account,
        'workspace_id': workspace_id, 'release': release, 'compilation': compilation,
        'checked_configuration': checked_configuration, 'activation': activation,
        'objects': objects, 'object_commands': object_commands,
        'field': {'key': field, 'kind': number['kind'], 'type': schema['type'], 'unit': unit},
        'rule_id': 'count_high', 'baseline': baseline, 'candidate': candidate,
        'source': source, 'source_manifest': source_manifest, 'observations': observations,
        'readbacks': [{'path': path, 'response': _ok(client.get(path))},
                      {'path': signals_path, 'response': _ok(client.get(signals_path))}]}
    json.dumps(checkpoint, ensure_ascii=False, allow_nan=False)
    return checkpoint
