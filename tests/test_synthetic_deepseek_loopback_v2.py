"""Validate only the local fake provider, not any agent or cloud service."""
import os
import httpx
import pytest
from tests.synthetic_deepseek_loopback_v2 import provider

pytestmark=pytest.mark.skipif(not os.environ.get('GA_TEST_SYNTHETIC_PROVIDER_PORT'),
    reason='requires explicitly allocated isolated loopback port')


def test_reply_is_local_and_counts_one_request():
    port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,content='synthetic',usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
        with httpx.Client(trust_env=False,timeout=3) as client:
            response=client.post(f'http://127.0.0.1:{port}/chat/completions',json={'messages':[]})
        assert response.status_code==200 and response.json()['usage']['total_tokens']==5
        assert len(server.requests)==1 and len(server.requests[0])==64


def test_dropped_response_is_still_one_observed_request():
    port=int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
    with provider(port,mode='drop') as server:
        with httpx.Client(trust_env=False,timeout=3) as client,pytest.raises(httpx.TransportError):
            client.post(f'http://127.0.0.1:{port}/chat/completions',json={'messages':[]})
        assert len(server.requests)==1
