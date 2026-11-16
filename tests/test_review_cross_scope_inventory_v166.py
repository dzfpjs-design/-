"""Cross-scope compound-loss negatives; SQL IO is explicit, never a PG claim."""
import json

import pytest
from fastapi import HTTPException

from generic_agents import native_video_review_store_v165 as store
from generic_agents.native_video_review_contracts_v165 import Draft, command_path
from tests.test_native_video_review_store_v165 import human_inventory, P, INSTANCE

B_INSTANCE = 'ffffffff-ffff-4fff-8fff-fffffffffff1'
B_OBSERVATION = 'ffffffff-ffff-4fff-8fff-fffffffffff2'


@pytest.mark.parametrize('operation', ['fresh_write', 'unknown_lookup'])
def test_compound_command_audit_loss_cannot_reuse_nonce_under_another_instance(monkeypatch, operation):
    review, command, _ = human_inventory()
    # The original retained human row is found independently of the two lost
    # nonce carriers; target B has no prior human row to catch this by itself.
    results = iter([[], [], [review], [], [], [review], [], []])
    locks = []
    class IO:
        def execute(self, *args, **kwargs): return None
        def scalars(self, statement): return iter(next(results))
        def scalar(self, *args, **kwargs): pytest.fail('no IAM/source/clock step after broken prior inventory')
        def add(self, *args): pytest.fail('no replacement human record may be written')
        def flush(self): pytest.fail('no replacement human record may be flushed')
    monkeypatch.setattr(store, '_observation_lock', lambda db, p, instance, obs, shared=False: locks.append((instance, obs, shared)))
    monkeypatch.setattr(store, 'scoped', lambda *args: object())
    monkeypatch.setattr(store, '_source', lambda *args: pytest.fail('must not construct a new source after compound loss'))
    with pytest.raises(HTTPException) as error:
        if operation == 'unknown_lookup':
            store.request_result(IO(), P, B_INSTANCE, command.request_id, command.wire_sha256)
        else:
            body = Draft.model_validate({**json.loads(command.data['wire']), 'expected_version': 0})
            raw = json.dumps(body.model_dump(), ensure_ascii=False, separators=(',', ':')).encode()
            store.mutate(IO(), P, B_INSTANCE, B_OBSERVATION, 'draft', body, raw,
                'POST', command_path(B_INSTANCE, B_OBSERVATION, 'draft'))
    assert error.value.status_code == 409
    if operation == 'fresh_write':
        assert locks[:2] == [(INSTANCE, review.observation_id, False), (B_INSTANCE, B_OBSERVATION, False)]
        assert all(shared is True for _, _, shared in locks[2:])
    else:
        assert locks and all(shared is True for _, _, shared in locks)
