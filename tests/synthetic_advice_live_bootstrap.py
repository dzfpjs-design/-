"""Runs only inside the synthetic harness with only a local, fixed-destination proxy."""
from pathlib import Path
import os
import re

if os.environ.get('GA_SYNTHETIC_PROVIDER_MODE') != 'fake':
    raise RuntimeError('synthetic bootstrap permits fake provider only')

from pytest import MonkeyPatch
from generic_agents.config import settings
from generic_agents.db import engine, migrate
from sqlalchemy.orm import Session
from sqlalchemy import inspect, text

if __name__ == '__main__':
    assert not os.environ.get('DEEPSEEK_API_KEY') and not os.environ.get('DASHSCOPE_API_KEY')
    assert not settings().cloud_allowed_providers
    settings().storage_dir = Path(os.environ['GA_STORAGE_DIR'])
    url = engine().url
    assert url.host == '127.0.0.1' and url.username == 'advice_test' and url.password is None
    assert url.port and re.fullmatch(r'ga_test_[0-9a-f]{12}', url.database or '')
    expected = Path(os.environ['GA_TEST_PG_DATA_DIR']).resolve()
    assert expected.name == 'data' and expected.parent.name.startswith('advice-race-pg-')
    with engine().connect() as conn:
        actual, identifier, database, user = conn.execute(text("SELECT current_setting('data_directory'), "
            "(SELECT system_identifier::text FROM pg_control_system()), current_database(), current_user")).one()
        assert (Path(actual).resolve(), identifier, database, user) == (
            expected, os.environ['GA_TEST_PG_SYSTEM_IDENTIFIER'], url.database, url.username)
    migrate()  # only the newly created, identity-checked synthetic test DB
    # Match the existing test harness: migrate before loading the API's
    # optional model registrations, then restore the checkpoint schema.
    from tests.synthetic_advice_live_flow import run_synthetic_mature_flow
    from generic_agents.same_goal_advice_prepare_writer_candidate_v2 import require_disposable_database
    # Existing pytest integration tests clear migration seed rows before a
    # checkpoint restore. Repeat that step only inside our new test cluster.
    with Session(engine()) as db:
        require_disposable_database(db)
        names = inspect(db.connection()).get_table_names(schema='public')
        quote = db.connection().dialect.identifier_preparer.quote_identifier
        if names:
            db.execute(text('TRUNCATE ' + ', '.join(quote('public')+'.'+quote(name) for name in names)
                + ' RESTART IDENTITY CASCADE'))
        db.commit()
    try:
        with MonkeyPatch.context() as patch:
            run_synthetic_mature_flow(patch)
    finally:
        engine().dispose()
