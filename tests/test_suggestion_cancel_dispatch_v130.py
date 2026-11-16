"""Real cancellation preserves its no-send history and remains readable."""
import json
import os
from pathlib import Path
from tests.test_native_suggestion_not_sent_v130 import run_not_sent_retry_journey, run_timeout_unknown_journey, _inventory, _origin, _ok
from tests import test_native_method_suggestions as old
from generic_agents.models import Job, CloudCall


def test_cancelled_not_sent_parent_stays_readable_without_a_new_dispatch(client,monkeypatch):
    journey=run_not_sent_retry_journey(client,monkeypatch)
    task={'id':journey['task_id']};path=old.url(task)+'/'+journey['parent_id']
    before=_ok(client.get(path));original=_origin(task['id'])
    command=old.action(before)
    saved=_ok(client.post(path+'/cancel',json=command))
    detail=saved['suggestion']
    assert detail['status']=='cancelled' and detail['call']['status']=='not_started'
    assert detail['call']['dispatch']==before['call']['dispatch']
    assert not detail['has_saved_response'] and not detail['permissions']['retry']['allowed']
    rows=_inventory();current=_ok(client.get(path));listing=_ok(client.get(old.url(task)))
    assert current==detail and any(row==detail for row in listing['items'])
    assert _ok(client.post(path+'/cancel',json=command))['suggestion']==detail
    assert _inventory()==rows and _origin(task['id'])==original
    if os.environ.get('GA_V130_CANCEL_DTO'):
        target=Path(os.environ['GA_V130_CANCEL_DTO']);assert target.is_absolute() and not target.exists()
        target.write_text(json.dumps({'synthetic_only':True,'task_id':task['id'],'detail':detail,'list':listing,'response':saved},ensure_ascii=False,sort_keys=True));target.chmod(0o600)


def test_stopping_an_unknown_call_keeps_unknown_certainty_and_no_new_job(client,monkeypatch):
    journey=run_timeout_unknown_journey(client,monkeypatch)
    task={'id':journey['task_id']};path=old.url(task)+'/'+journey['suggestion_id']
    before=_ok(client.get(path));original=_origin(task['id']);inventory=_inventory()
    assert before['status']=='response_unknown' and before['call']['status']=='unknown'
    command=old.action(before)
    saved=_ok(client.post(path+'/cancel',json=command))
    detail=saved['suggestion']
    assert detail['status']=='cancelled' and detail['call']['status']=='unknown'
    assert detail['call'].get('dispatch') is None and not detail['has_saved_response']
    assert not detail['permissions']['retry']['allowed']
    assert _ok(client.get(path))==detail
    assert _ok(client.post(path+'/cancel',json=command))['suggestion']==detail
    now=_inventory();assert now[Job.__tablename__]==inventory[Job.__tablename__] and now[CloudCall.__tablename__]==inventory[CloudCall.__tablename__]
    assert _origin(task['id'])==original
