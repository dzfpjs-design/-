"""Audit all vault users when the shared key marker was lost."""
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Job
from generic_agents.managed_file_grants import _key_guard

@pytest.mark.parametrize('encrypted_kind',['managed_ftps_grant_v1','managed_mqtt_grant_v1'])
def test_file_vault_refuses_new_identity_when_other_protocol_ciphertext_exists(account,encrypted_kind):
    workspace=account[2]
    with Session(engine()) as db:
        db.add(Job(workspace_id=workspace,kind=encrypted_kind,status='internal',idempotency_key='synthetic-cross-kind-'+encrypted_kind,data={'cipher':'synthetic'}));db.commit()
        with pytest.raises(HTTPException) as caught:
            _key_guard(db,b'synthetic-replacement-key',workspace,establish=True)
        assert caught.value.status_code==503

def test_shared_vault_identity_creation_serializes_different_keys(account):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import sleep
    workspace=account[2];started=Event()
    def competitor():
        with Session(engine()) as other:
            started.set()
            with pytest.raises(HTTPException) as caught:
                _key_guard(other,b'synthetic-key-two',workspace,establish=True)
            return caught.value.status_code
    with Session(engine()) as first:
        _key_guard(first,b'synthetic-key-one',workspace,establish=True)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(competitor);assert started.wait(2);sleep(.2);assert not future.done()
            first.commit();assert future.result(timeout=5)==503
