"""Deliberate failure used only by the isolated retention runner self-check."""
import os
import pytest
from sqlalchemy import text
from generic_agents.db import engine


@pytest.mark.skipif(os.environ.get('GA_TEST_RETENTION_PROBE')!='1',reason='explicit isolated probe only')
def test_deliberate_failure_preserves_synthetic_database():
    with engine().begin() as db:
        db.execute(text('CREATE TABLE synthetic_retention_probe (value integer NOT NULL)'))
        db.execute(text('INSERT INTO synthetic_retention_probe VALUES (17)'))
    pytest.fail('intentional synthetic retention probe')
