"""Wait only for the exact queued test planning job; never a production synchronous fallback."""
from generic_agents.worker import process_once


def post_plan(client,path,**kwargs):
    response=client.post(path,**kwargs)
    if response.status_code==200 and response.json().get('status')=='queued':
        plan=response.json();assert process_once(plan['data']['planning_job_id'])
        return client.get('/api/plans/'+plan['id'])
    return response
