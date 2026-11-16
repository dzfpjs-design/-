"""Synchronous Python SDK with explicit persisted server receipts."""
import httpx


class Client:
    def __init__(self, base_url, token, workspace_id=None):
        headers = {"Authorization": "Bearer " + token}
        if workspace_id:
            headers["X-Workspace-ID"] = workspace_id
        self.http = httpx.Client(base_url=base_url, headers=headers, timeout=90, trust_env=False)

    def close(self):
        self.http.close()

    def request(self, method, path, **kwargs):
        response = self.http.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def register(self, manifest):
        return self.request("POST", "/api/capabilities", json=manifest)

    def register_pixel_ability(self, instance_id, reference_run_id, command):
        body={'schema':'workflow-pixel-register-v250',**command}
        return self.request('POST', '/api/workspace/instances/'+instance_id+'/pixel-change/runs/'+reference_run_id+'/register-business', json=body)

    def create_workflow_task(self, instance_id, body):
        # Optional typed pixel input is sent only when explicitly supplied.
        value=dict(body)
        if value.get('pixel_pair') is None:value.pop('pixel_pair',None)
        return self.request('POST','/api/workspace/instances/'+instance_id+'/tasks',json=value)

    def task_pixel_results(self, task_id):
        return self.request('GET','/api/workspace/tasks/'+task_id+'/pixel-results')

    def settle_pixel_result(self, task_id, run_id, command):
        body={'schema':'workflow-pixel-settlement-v250',**command}
        return self.request('POST','/api/workspace/tasks/'+task_id+'/pixel-results/'+run_id+'/settle',json=body)

    def pixel_machine_results(self, instance_id, **filters):
        return self.request('GET','/api/workspace/instances/'+instance_id+'/pixel-machine-results',params=filters)

    def pixel_machine_daily(self, instance_id, **filters):
        return self.request('GET','/api/workspace/instances/'+instance_id+'/pixel-machine-daily',params=filters)

    def pixel_question_options(self, instance_id):
        return self.request('GET','/api/workspace/instances/'+instance_id+'/pixel-questions/options')

    def preview_pixel_question(self, instance_id, body):
        return self.request('POST','/api/workspace/instances/'+instance_id+'/pixel-questions/preview',json=body)

    def ask_pixel_question(self, instance_id, body):
        return self.request('POST','/api/workspace/instances/'+instance_id+'/pixel-questions',json=body)

    def pixel_question(self, instance_id, query_id):
        return self.request('GET','/api/workspace/instances/'+instance_id+'/pixel-questions/'+query_id)

    def pixel_question_request(self, instance_id, request_id, expected_body_hash):
        return self.request('GET','/api/workspace/instances/'+instance_id+'/pixel-questions/requests',params={
            'request_id':request_id,'expected_body_hash':expected_body_hash})

    def pixel_questions(self, instance_id, **filters):
        return self.request('GET','/api/workspace/instances/'+instance_id+'/pixel-questions',params=filters)

    def command_pixel_question(self, instance_id, query_id, body):
        if body.get('operation') not in {'cancel','retry'}:raise ValueError('未知问答操作')
        return self.request('POST','/api/workspace/instances/'+instance_id+'/pixel-questions/'+query_id+'/'+body['operation'],json=body)

    def pixel_question_command_request(self, instance_id, query_id, request_id, expected_body_hash):
        return self.request('GET','/api/workspace/instances/'+instance_id+'/pixel-questions/'+query_id+'/commands/requests',params={
            'request_id':request_id,'expected_body_hash':expected_body_hash})

    def query(self, question, entity_ids, fields=None, mode="cloud", model_profile_id=None,evidence_requirement=None,business_timezone=None,relation_query=None,
              tool_steps=None,max_tool_calls=6,max_tool_seconds=60,allow_external_tools=False):
        return self.request("POST", "/api/data/queries", json={"question": question,
            "entity_ids": entity_ids, "fields": fields or [], "mode": mode,
            "model_profile_id": model_profile_id,'evidence_requirement':evidence_requirement,'business_timezone':business_timezone,
            'relation_query':relation_query,'tool_steps':tool_steps or [],'max_tool_calls':max_tool_calls,
            'max_tool_seconds':max_tool_seconds,'allow_external_tools':allow_external_tools})

    def create_relation(self, claim):
        return self.request('POST','/api/relations',json=claim)

    def get_query(self,query_id):
        return self.request('GET','/api/data/queries/'+query_id)

    def save_query_draft(self,query_id,expected_version,draft,reason):
        return self.request('PUT','/api/data/queries/'+query_id+'/draft',json={
            'expected_version':expected_version,'draft':draft,'reason':reason})

    def confirm_query(self,query_id,expected_version,reason):
        return self.request('POST','/api/data/queries/'+query_id+'/confirm',json={
            'expected_version':expected_version,'reason':reason})

    def cancel_query(self,query_id,expected_version,reason):
        return self.request('POST','/api/data/queries/'+query_id+'/cancel',json={
            'expected_version':expected_version,'reason':reason})

    def review_derived(self,query_id,expected_version,decision,reason):
        return self.request('POST','/api/data/queries/'+query_id+'/review-derived',json={
            'expected_version':expected_version,'decision':decision,'reason':reason})

    def review_relation(self,relation_id,expected_version,decision,reason):
        return self.request('POST','/api/relations/'+relation_id+'/review',json={
            'expected_version':expected_version,'decision':decision,'reason':reason})

    def confirm_goal(self,task_id,expected_version,goal_spec,reason):
        return self.request('PUT','/api/tasks/'+task_id+'/goal',json={'expected_version':expected_version,
            'goal_spec':goal_spec,'reason':reason})

    def request_plan(self,task_id,expected_version,reason,model_profile_id=None,observation_mode=None):
        """Return a durable queued Plan; poll get_plan before human approval."""
        return self.request('POST','/api/tasks/'+task_id+'/plan',json={'expected_version':expected_version,
            'reason':reason,'model_profile_id':model_profile_id,'observation_mode':observation_mode})

    def get_plan(self,plan_id):
        return self.request('GET','/api/plans/'+plan_id)

    def observe(self, observation):
        return self.request("POST", "/api/observations", json=observation)

    def upload_evidence(self, path, media_type, cloud_model_id=None):
        from pathlib import Path
        params = {"cloud_model_id": cloud_model_id} if cloud_model_id else {}
        return self.request("POST", "/api/evidence", params=params, content=Path(path).read_bytes(),
                            headers={"Content-Type": media_type})
