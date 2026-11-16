"""Root combines the actual formal-goal journey with the prior recovery journey."""
from tests.test_workflow_graph_goal_journey_v126 import run_goal_journey
from tests.test_graph_source_retirement_journey_v125 import run_controller_journey as previous_journey
from tests.test_report_evidence_pipeline import reply

def run_controller_journey(waiting_release,tmp_path):
    # Complete the original waiting-source goal before the prior journey
    # legitimately revokes that source. No old authority is reconstructed.
    formal=run_goal_journey(waiting_release,tmp_path)
    configuration=previous_journey(waiting_release,tmp_path)
    client=waiting_release['client']
    task=reply(client.get(formal['task_path']))
    goal=reply(client.get(formal['goal_path']))
    exported=reply(client.get(formal['export_path']))
    assert task['completed'] and goal['acceptance']
    assert goal['acceptance']['request_id']==formal['acceptance']['body']['request_id']
    assert exported['goal']['acceptance']==goal['acceptance']
    assert not goal['acceptance']['physical_state_verified'] and not exported['learning_auto_published']
    recovery=formal['read_only_recovery']
    recovered=reply(client.post(recovery['path'],json=recovery['body']))
    assert recovered['saved'] and not recovered['abandoned']
    return {**configuration,'formal_goal':{**formal,'frozen_task':task,'frozen_goal':goal,'frozen_export':exported,'frozen_recovery':recovered}}
