"""Internal reduce-v2 adapter for one already-authorized fenced reservation."""
from __future__ import annotations

import copy
from sqlalchemy import func, inspect, select
from .contracts import digest
from .leases import fenced, ownership
from .models import Audit, CloudCall, Job
from . import report_evidence_delegated_reduce_start as start


def _deny():
    raise ValueError('汇总原计划、任务或依据无法安全核对；未授权新调用')


class _Binding:
    def __init__(self, db, row, grant, principal, activation=None):
        self.tx=db.get_transaction();self.row=row;self.grant=grant;self.principal=principal
        self.activation=activation
        self.before_row=copy.deepcopy(row.data);self.before_grant=copy.deepcopy(grant.data)
        self.snapshots=None

    def bind(self, db, call):
        from . import report_evidence_pipeline as evidence
        if db.get_transaction() is not self.tx or self.row.data!=self.before_row or self.grant.data!=self.before_grant:
            _deny()
        stage=evidence._stage(self.row,'reduce')
        if stage['status']!='running' or stage['inflight'] is not True or stage['reserved_call_id'] is not None or stage['journal'] is not None:
            _deny()
        call.data={**call.data,'input_policy_hash':self.grant.data['manifest']['policy_hash']}
        evidence._replace_stage(self.row,'reduce',{**stage,'reserved_call_id':call.id})
        self.grant.data={**self.grant.data,'used_call_id':call.id}
        db.flush()
        objects=[obj for obj in db.identity_map.values() if inspect(obj).persistent]
        self.snapshots=[(obj,{col.key:copy.deepcopy(getattr(obj,col.key)) for col in inspect(obj).mapper.column_attrs}) for obj in objects]

    def check(self, db):
        if db.get_transaction() is not self.tx or self.snapshots is None:
            _deny()
        if self.activation is not None and db.scalar(select(func.clock_timestamp())) >= self.activation[2]:
            _deny()
        for obj,expected in self.snapshots:
            state=inspect(obj)
            if obj in db.deleted or not state.persistent or state.deleted or state.detached:
                _deny()
            if any(getattr(obj,k)!=v for k,v in expected.items()):_deny()
        return True


def prepare_reduce_stage(db, principal, job, capability, permit_manifest):
    from . import cloud_report_plan_allowance as plans
    from . import cloud_scoped_extra_reservation as scoped
    from . import report_evidence_delegated_reduce_preflight as preflight
    from . import report_evidence_pipeline as evidence
    from .gateway import prepare_cloud_json
    from .report_evidence_worker_isolation import isolated
    if (not isolated(job) or not fenced(db) or db.info.get('worker_actor')!=principal or ownership(db).get('job_id')!=job.id
            or not isinstance(job.data,dict) or not job.data.get('report_draft_id')):
        _deny()
    row=evidence.owned(db,principal,job.data['report_draft_id'],True)
    if (not row.request_id.startswith('fl_') or row.status!='running' or row.data.get('phase')!='reduce'
            or row.data.get('job_id')!=job.id):
        _deny()
    grant=db.scalar(select(Job).where(Job.workspace_id==principal.workspace_id,
        Job.idempotency_key=='evidence-reduce-allowance-v1:'+digest({'job_id':job.id}))
        .with_for_update(nowait=True).execution_options(populate_existing=True))
    if (not grant or grant.kind!='cloud_report_reduce_allowance_v1' or grant.status!='internal'
            or type(grant.data) is not dict or set(grant.data)!={'schema','manifest','manifest_hash','audit_id','used_call_id'}
            or grant.data['schema']!='delegated-reduce-allowance-record-v1'
            or grant.data['manifest_hash']!=digest(grant.data['manifest']) or grant.data['used_call_id'] is not None):
        _deny()
    gm=grant.data['manifest'];saved=gm.get('preflight')
    if (type(saved) is not dict or gm.get('preflight_hash')!=saved.get('preflight_hash')
            or gm.get('workspace_id')!=principal.workspace_id or gm.get('actor_id')!=principal.user_id
            or gm.get('draft_id')!=row.id or gm.get('job_id')!=job.id or gm.get('model_id')!=capability.id
            or gm.get('model_hash')!=capability.content_hash or gm.get('max_calls')!=1
            or gm.get('user_confirmed_business') is not False or permit_manifest.get('scope_kind')!=start.SCOPE
            or permit_manifest.get('scope_hash')!=start.scope_hash(principal,row,job.id,saved)):
        _deny()
    stages=row.data['maps'];old_job=gm['original_job_id']
    from .report_evidence_delegated_dispatch_gate import ready
    activation=None
    if not ready():
        from . import report_evidence_scoped_activation as scope
        activation=scope.for_job(db,principal,row,map_job_id=old_job,lock=True)
    ids=[s['reserved_call_id'] for s in stages]
    db.scalars(select(CloudCall).where(CloudCall.workspace_id==principal.workspace_id,CloudCall.id.in_(ids))
        .order_by(CloudCall.id).with_for_update(nowait=True).execution_options(populate_existing=True)).all()
    records=db.scalars(select(Job).where(Job.workspace_id==principal.workspace_id,
        Job.idempotency_key.in_([plans.plan_key(old_job),*[key for s in stages for key in
            (scoped.permit_key(old_job,s['chunk_id']),scoped.dispatch_key(old_job,s['chunk_id']))]]))
        .order_by(Job.id).with_for_update(nowait=True).execution_options(populate_existing=True)).all()
    db.scalars(select(Audit).where(Audit.workspace_id==principal.workspace_id,
        Audit.object_id.in_([grant.id,*[r.id for r in records]])).order_by(Audit.id)
        .with_for_update(nowait=True).execution_options(populate_existing=True)).all()
    audits=db.scalars(select(Audit).where(Audit.workspace_id==principal.workspace_id,
        Audit.object_id==grant.id,Audit.action=='cloud.report_reduce_allowance.issued_v1').limit(2)).all()
    if (len(audits)!=1 or audits[0].id!=grant.data['audit_id'] or audits[0].actor_id!=principal.user_id
            or audits[0].data!={'grant_id':grant.id,'manifest_hash':grant.data['manifest_hash']}):
        _deny()
    command=evidence.ReducePreviewCommand.model_validate(row.data['reduction']['preview']['body'])
    if preflight.preview(db,principal,row,command,_rechecking=True)!=saved:
        _deny()
    req=evidence._request(row,'reduce');spec=evidence._spec(row,'reduce')
    messages,_,images=prepare_cloud_json(capability,req['messages'][1:],evidence.legacy.canonical_schema(spec['output_schema']))
    if (images or digest(messages)!=gm['request_hash'] or digest(messages)!=digest(req['messages'])
            or digest(spec['dispatch_options'])!=gm['options_hash'] or digest(spec['input_policy'])!=gm['policy_hash']
            or any(permit_manifest.get(k)!=gm[v] for k,v in [('request_hash','request_hash'),('options_hash','options_hash'),('token_bound','token_bound')])):
        _deny()
    return _Binding(db,row,grant,principal,activation)
