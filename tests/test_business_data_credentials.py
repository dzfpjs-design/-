"""Pure server-grant checks; no secrets, network or database are consulted."""
import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from generic_agents import business_data_credentials as credentials

WORKSPACE = 'd2059f82-3c95-499c-a659-7c85ba4c8df4'
ENDPOINT = 'https://service.example/records'
REFERENCE = 'GA_CONNECTOR_SECRET_TEST_REFERENCE'


def grant(**changes):
    return {'workspace_id': WORKSPACE, 'endpoint': ENDPOINT, 'secret_ref': REFERENCE, **changes}


def configure(monkeypatch, raw):
    monkeypatch.setattr(credentials, 'settings', lambda: SimpleNamespace(business_data_access_grants=raw))


def test_public_read_needs_no_credential_or_grant_lookup(monkeypatch):
    monkeypatch.setattr(credentials, 'settings', lambda: pytest.fail('public source consulted private grants'))
    assert credentials.require_grant(WORKSPACE, ENDPOINT, None) is None


def test_server_exact_workspace_destination_and_reference_grant(monkeypatch):
    configure(monkeypatch, json.dumps({'schema': 'business-data-access-grants-v204', 'grants': [grant()]}))
    assert credentials.require_grant(WORKSPACE, ENDPOINT, REFERENCE) is None
    for values in [('another-workspace', ENDPOINT, REFERENCE), (WORKSPACE, ENDPOINT + '/another', REFERENCE),
                   (WORKSPACE, ENDPOINT, REFERENCE + '_ANOTHER'), (WORKSPACE, ENDPOINT + '?token=private', REFERENCE)]:
        with pytest.raises(HTTPException) as caught: credentials.require_grant(*values)
        assert caught.value.status_code == 409 and 'private' not in str(caught.value.detail)


@pytest.mark.parametrize('raw', ['{}', 'null', '{"schema":"business-data-access-grants-v204","grants":[],"grants":[]}',
    json.dumps({'schema':'wrong', 'grants':[grant()]}),
    json.dumps({'schema':'business-data-access-grants-v204', 'grants':[grant(extra='unauthorized')]}),
    json.dumps({'schema':'business-data-access-grants-v204', 'grants':[grant(secret_ref='DASHSCOPE_API_KEY')]}),
    'x' * (128 * 1024 + 1)])
def test_missing_malformed_ambiguous_or_oversize_grants_fail_closed(monkeypatch, raw):
    configure(monkeypatch, raw)
    with pytest.raises(HTTPException): credentials.require_grant(WORKSPACE, ENDPOINT, REFERENCE)
