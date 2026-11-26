"""The disposable-PG guard must validate the actual child connection too."""
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents import same_goal_advice_prepare_writer_candidate_v2 as guard


def test_engine_and_child_session_check_same_real_database(account,monkeypatch):
    with Session(engine()) as outer:
        guard.require_disposable_database(outer)
        with Session(bind=outer.connection()) as child:
            guard.require_disposable_database(child)
            monkeypatch.setenv('GA_TEST_PG_SYSTEM_IDENTIFIER','1')
            with pytest.raises(HTTPException):guard.require_disposable_database(child)
