"""Retain closed original-POST recovery through the complete graph journey."""
import copy
from tests.test_graph_formal_goal_checkpoint_journey_v126 import run_controller_journey as previous_journey
from tests.test_report_evidence_pipeline import reply
from tests.test_operator_journal_backend_v128 import run_operator_controller_journey


def stable_goal(value):
    result=copy.deepcopy(value)
    stamp=result['check_worker'].pop('database_now')
    from datetime import datetime
    assert isinstance(stamp,str) and datetime.fromisoformat(stamp.replace('Z','+00:00')).utcoffset() is not None
    return result


def stable_export(value):
    result=copy.deepcopy(value);result['goal']=stable_goal(result['goal']);return result


def run_controller_journey(waiting_release,tmp_path,late_scene):
    result=previous_journey(waiting_release,tmp_path)
    client=waiting_release['client'];late=copy.deepcopy(late_scene)
    task=reply(client.get(late['task_path']));goal=reply(client.get(late['goal_path']))
    exported=reply(client.get(late['export_path']))
    assert task==late['task'] and stable_goal(goal)==stable_goal(late['goal'])
    assert stable_export(exported)==stable_export(late['export'])
    assert goal['cancelled'] and not goal['acceptance']
    acquisitions=reply(client.get(late['goal_path']+'/acquisitions'))
    assert len(acquisitions['items'])==1
    item=acquisitions['items'][0]
    assert item['id']==late['acquisition_id'] and item['status']=='succeeded' and not item['reservation_held']
    assert item['closing_observation_id']==late['observation_id']
    assert item['reason']=='原采集已核查结案，设备占用已释放。'
    verification=reply(client.post(late['verify']['path'],json=late['verify']['body']))
    assert verification==late['verify']['response']
    receipt=reply(client.post(late['receipt_read']['path'],json=late['receipt_read']['body']))
    assert receipt==item
    stopped=reply(client.post(late['goal_stop']['path'],json=late['goal_stop']['body']))
    assert stable_goal(stopped)==stable_goal(goal)
    original=late['original_submission_replay']
    assert original['path']==late['ingest']['path'] and original['body']==late['ingest']['body']
    intake=reply(client.post(original['path'],json=original['body']),status=201)
    assert intake==original['response']
    assert intake['id']==late['observation_id'] and intake['status']=='processed'
    assert intake['data']['input_hash']==late['ingest']['input_hash']
    assert intake['data']['data_reception']['receipt_hash']==late['event']['data']['data_reception']['receipt_hash']
    assert original['new_observations']==original['new_jobs']==original['new_invocations']==0
    operator=run_operator_controller_journey(client)
    return {**result,'operator_original_submit':operator,'late_acquisition':{**late,'frozen_task':task,'frozen_goal':goal,
        'frozen_export':exported,'frozen_acquisitions':acquisitions,'frozen_verification':verification,
        'frozen_receipt':receipt,'frozen_stopped':stopped,'closed_intake_replay_status':201,
        'closed_intake_replay_response':intake,
        'raw_closed_intake_replay_recovers_saved_record':True,
        'raw_closed_original_submission_restored':True}}
