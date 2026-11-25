"""Read-only counts from a restored synthetic checkpoint; no provider or user data."""
import os
import pytest
from sqlalchemy import func,select
from sqlalchemy.orm import Session
from generic_agents.config import settings
from generic_agents.db import engine
from generic_agents.models import Audit,CloudCall
from generic_agents import same_goal_advice_transport_intent_candidate_v2 as intent
from generic_agents import same_goal_advice_dispatch_permit_candidate_v2 as permit
from tests.synthetic_advice_fresh_checkpoint_restore import restore_to_disposable_test_db

@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_FRESH_CHECKPOINT'),reason='requires isolated synthetic fresh checkpoint')
def test_zero():
    settings().cloud_allowed_providers=''
    restore_to_disposable_test_db(os.environ['GA_SYNTHETIC_FRESH_CHECKPOINT'])
    with Session(engine()) as db:
        assert db.scalar(select(func.count()).select_from(CloudCall))==0
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action.in_([intent.ACTION,permit.ACTION])))==0
