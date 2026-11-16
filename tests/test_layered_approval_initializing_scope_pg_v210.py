"""A start token belongs to one Managed row, never an entire read Session.

The source is genuinely accepted through normal V208/V207/V175 APIs and the
real local worker. The target is a fresh normal draft. Only an internal test
token and one uncommitted negative version change are injected; no executing
or successful Task/receipt is fabricated. Root runs on disposable PostgreSQL.
"""
import copy

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from generic_agents import models as m, layered_approval_store_v175 as approval
from generic_agents.db import engine
from tests.test_business_data_gateway_pg import scene
from tests.test_layered_cases_pg_v209 import accepted_case_source
from tests.test_layered_case_uses_pg_v210 import case_use_target


def test_start_scope_reads_other_real_accepted_source_without_granting_or_forgiving_authority(case_use_target):
    f = case_use_target
    key = 'layered_approval_initializing_v175'
    with Session(engine()) as db:
        source_task = db.get(m.Task, f.task_id)
        target_task = db.get(m.Task, f.target_task_id)
        source = approval.managed(db, f.s.p, source_task)
        target = approval.managed(db, f.s.p, target_task)
        assert source.status == 'accepted' and target.status == 'draft' and source.id != target.id
        original = copy.deepcopy(approval.authority(source))
        # Correctly shaped and hashed, but deliberately NOT a valid approved
        # -> executing transition for the target. It grants no execution.
        foreign = {'managed_id': target.id, 'state_hash': approval.digest(approval.authority(target)),
            'previous_state_hash': approval.digest(approval.authority(target))}
        db.info[key] = foreign
        try:
            assert approval.managed(db, f.s.p, source_task) is source
            assert approval.authority(source) == original
            assert db.info[key] == foreign

            # The same-row path must retain every original start condition.
            with pytest.raises(HTTPException) as wrong_target:
                approval.managed(db, f.s.p, target_task)
            assert wrong_target.value.status_code == 409
            assert '原启动事务的过渡状态无法核对' in str(wrong_target.value.detail)

            # A foreign token is also no exemption from the original source
            # command/Audit state chain. Even a one-version drift must fail.
            original_version = source.version
            source.version += 1
            with db.no_autoflush, pytest.raises(HTTPException) as damaged_source:
                approval.managed(db, f.s.p, source_task)
            assert damaged_source.value.status_code == 409
            assert '未与独立已保存命令相符' in str(damaged_source.value.detail)
            source.version = original_version
            assert approval.authority(source) == original
        finally:
            db.info.pop(key, None)
        assert key not in db.info
