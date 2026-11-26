"""Isolated evidence UI data: no model, source text or original is changed."""
import copy
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit, CloudCall
from generic_agents import same_goal_advice_deepseek_adapter_candidate_v2 as adapter
from generic_agents import same_goal_advice_evidence_explain_candidate_v2 as subject
from generic_agents import same_goal_advice_unlimited_preview_candidate_v2 as preview
from tests.synthetic_deepseek_loopback_v2 import provider
from tests.test_advice_v2_cloud_reservation_candidate_pg import started
from tests.test_same_goal_advice_loopback_dispatch_candidate_v2_pg import setup, synthetic_source_expiry
from tests.test_same_goal_advice_projection_v307 import scene as projection_scene, derive
from tests.test_same_goal_advice_runtime_v308 import _opinion
from tests.test_same_goal_advice_unlimited_preview_candidate_v2_pg import prepared_preview
from tests.test_same_goal_advice_unlimited_store_v2_pg import synthetic_custody


def test_disabled_by_default_never_reads_records(client, account):
    assert type(settings()).model_fields['v2_advice_evidence_explanation_enabled'].default is False
    result = client.get('/api/workspace/tasks/any/business-advice/evidence-explanation')
    assert result.status_code == 200 and result.json() == {'enabled': False, 'items': []}


def test_explanation_categories_and_tampered_references(monkeypatch):
    sample = projection_scene.__wrapped__(monkeypatch)
    cloud = derive(sample)['cloud_request']['decision_input']
    opinion = _opinion(cloud)
    capability = [f['fact_ref'] for f in cloud['facts'] if f['kind'] == 'capability_declaration']
    observed = [f['fact_ref'] for f in cloud['facts'] if f['kind'] == 'observation']
    assert capability and observed
    opinion['recommendation']['supporting_fact_refs'] = capability[:1]
    explanation = subject._derive(cloud, opinion, {})
    assert explanation['capability_count'] == 1 and explanation['observation_count'] == 0
    assert explanation['has_recommendation'] is True
    assert explanation['observation_state'] == 'not_cited'
    rendered = json.dumps(explanation, ensure_ascii=False)
    assert 'fact_ref' not in rendered and 'source_id' not in rendered and 'reasoning' not in rendered
    opinion['recommendation']['supporting_fact_refs'] = capability[:1] + observed[:1]
    assert subject._derive(cloud, opinion, {})['observation_state'] == 'unknown'
    explanation = subject._derive(cloud, opinion, {observed[0]: 'stale'})
    assert explanation['observation_count'] == 1 and explanation['observation_state'] == 'stale'
    assert subject._derive(cloud, opinion, {observed[0]: 'current'})['observation_state'] == 'current'
    opinion['recommendation']['candidate_ref'] = None
    assert subject._derive(cloud, opinion, {})['has_recommendation'] is False
    opinion['recommendation']['supporting_fact_refs'] = ['invented_fact']
    with pytest.raises(ValueError): subject._derive(cloud, opinion, {})


def test_partial_sources_are_never_presented_as_full(monkeypatch):
    sample = projection_scene.__wrapped__(monkeypatch)
    cloud = derive(sample)['cloud_request']['decision_input']
    opinion = _opinion(cloud)
    index = next(i for i, source in enumerate(cloud['sources']) if source['readability'] == 'readable')
    cloud['sources'][index]['readability'] = 'partial'
    opinion['source_reviews'][index]['assessment'] = 'reviewed'
    assert subject._derive(cloud, opinion, {})['partial_source_count'] >= 1


def test_observation_freshness_requires_original_source_and_policy():
    now = datetime(2026, 11, 25, tzinfo=timezone.utc)
    at = (now - timedelta(seconds=30)).isoformat()
    fact = {'kind':'observation','semantics':'instantaneous','time_scope':{'start':at,'end':at},
            'entity_id':'entity','source_refs':[{'source_ref':'source'}]}
    sources = {'source':{'kind':'observation','source_id':'observation'}}
    original = {'observation_id':'observation','entity_id':'entity','observed_at':at}
    assert subject._one_observation_freshness(fact, sources, [original], 60, now) == 'current'
    assert subject._one_observation_freshness(fact, sources, [original], 20, now) == 'stale'
    expired = {**original, 'valid_until':(now-timedelta(seconds=1)).isoformat()}
    assert subject._one_observation_freshness(fact, sources, [expired], 60, now) == 'stale'
    assert subject._one_observation_freshness(fact, sources, [original, original], 60, now) is None
    assert subject._one_observation_freshness(fact, {'source':{'kind':'observation','source_id':'other'}}, [original], 60, now) is None
    assert subject._one_observation_freshness({**fact,'semantics':'interval_mean'}, sources, [original], 60, now) is None


@pytest.mark.skipif(not os.environ.get('GA_TEST_SYNTHETIC_PROVIDER_PORT'), reason='isolated loopback provider only')
def test_http_displays_only_verified_original_and_rejects_tampered_preview(started, client, account, monkeypatch):
    with tempfile.TemporaryDirectory(prefix='advice-v2-response-spool-') as path:
        monkeypatch.setattr(settings(), 'v2_response_spool_enabled', True)
        monkeypatch.setattr(settings(), 'v2_response_spool_root', Path(path))
        monkeypatch.setattr(settings(), 'v2_advice_recovery_console_enabled', True)
        monkeypatch.setattr(settings(), 'v2_advice_evidence_explanation_enabled', True)
        _, args, opinion = setup(started)
        port = int(os.environ['GA_TEST_SYNTHETIC_PROVIDER_PORT'])
        with provider(port, content=json.dumps(opinion), usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}) as server:
            result = adapter.execute_synthetic_test_only(*args)
            assert result['state'] == 'settled' and len(server.requests) == 1
        url = '/api/workspace/tasks/'+args[1]+'/business-advice/evidence-explanation'
        result = client.get(url)
        assert result.status_code == 200, result.text
        data = result.json()
        assert data['enabled'] and len(data['items']) == 1 and data['items'][0]['state'] == 'ready'
        assert 'fact_ref' not in result.text and 'source_id' not in result.text and 'reasoning' not in result.text
        with Session(engine()) as db:
            event = db.scalar(select(Audit).where(Audit.action == preview.ACTION))
            value = copy.deepcopy(event.data)
            value['preview_record']['cloud_request']['decision_input']['facts'][0]['fact_ref'] = 'tampered_ref'
            event.data = value; db.commit()
        result = client.get(url)
        assert result.status_code == 200 and result.json()['items'][0]['state'] == 'needs_review'
        assert len(server.requests) == 1
