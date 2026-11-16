"""Actual PG registration must retain the original typed JSON payload.

These two baseline probes use public registration/validation/publication APIs,
then independent Sessions. No hash, authority, database type or successful
result is patched. Root alone executes them in the owned disposable database.
The full graph/goal journey belongs in a separate test file so this baseline
stays frozen while Root captures the pre-fix failures.
"""
import copy
import json
import math
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from generic_agents.contracts import Manifest, digest
from generic_agents.db import engine
from generic_agents.models import Capability
from tests.test_vertical_slice import manifest


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False)


def _public(value):
    if isinstance(value, dict):
        assert not any(key.startswith('_ga_') for key in value), 'private persistence fields leaked into a public DTO'
        for child in value.values():
            _public(child)
    elif isinstance(value, list):
        for child in value:
            _public(child)


def _typed_number(value, expected):
    assert type(value) is float, 'the original JSON float became an integer or another type'
    assert value == expected
    assert math.copysign(1.0, value) == math.copysign(1.0, expected), 'the original negative-zero sign was lost'
    assert _canonical(value) == _canonical(expected)


@pytest.mark.parametrize('number', [pytest.param(-0.0, id='negative-zero'),
    pytest.param(1e21, id='large-exponent-float')])
def test_actual_typed_manifest_survives_registration_validation_publication_and_cold_reads(client, number):
    schema = {'type': 'object', 'properties': {'reading': {'type': 'number', 'minimum': number}}}
    body = manifest('tool', name='typed_payload_' + uuid4().hex,
        allowed_agent_roles=['perception'], output_schema=schema,
        execution={'adapter': 'builtin', 'handler': 'numeric_rules'},
        specification={'rules': []}, data_policy={'cloud_allowed': False})
    expected = Manifest.model_validate(body).model_dump(mode='json')
    expected_canonical, expected_hash = _canonical(expected), digest(expected)
    wire = _canonical(body)
    assert ('"minimum":-0.0' if number == 0 else '"minimum":1e+21') in wire
    response = client.post('/api/capabilities', content=wire,
        headers={'Content-Type': 'application/json'})
    assert response.status_code == 201, response.text
    registered = response.json(); _public(registered)
    identity = registered['id']
    assert registered['content_hash'] == expected_hash
    # Capture an actual independent read before validation. Do not repair it or
    # recompute the registered hash from a lossy mirror to permit publication.
    with Session(engine()) as db:
        cold = db.get(Capability, identity)
        before = copy.deepcopy(cold.data)
        assert cold.content_hash == expected_hash
    validated_response = client.post('/api/capabilities/' + identity + '/validate', json={
        'expected_version': registered['version'], 'reason': '核对原始数值类型的真实能力合同'})
    assert validated_response.status_code == 200, validated_response.text
    validated = validated_response.json(); _public(validated)
    published_response = client.post('/api/capabilities/' + identity + '/publish', json={
        'expected_version': validated['version'], 'reason': '原合同与登记摘要一致后明确发布'})
    assert published_response.status_code == 200, published_response.text
    published = published_response.json(); _public(published)
    assert published['status'] == 'published'
    for payload in (before, registered['data'], validated['data'], published['data']):
        _typed_number(payload['output_schema']['properties']['reading']['minimum'], number)
        assert _canonical(payload) == expected_canonical and digest(payload) == expected_hash
    with Session(engine()) as db:
        cold = db.get(Capability, identity)
        assert cold.status == 'published'
        assert cold.content_hash == cold.validated_hash == expected_hash == digest(cold.data)
        _typed_number(cold.data['output_schema']['properties']['reading']['minimum'], number)
        assert _canonical(cold.data) == expected_canonical
    listing = client.get('/api/capabilities')
    assert listing.status_code == 200, listing.text
    public = listing.json(); _public(public)
    listed = next(row for row in public if row['id'] == identity)
    assert listed['content_hash'] == expected_hash
    _typed_number(listed['data']['output_schema']['properties']['reading']['minimum'], number)
    assert _canonical(listed['data']) == expected_canonical
