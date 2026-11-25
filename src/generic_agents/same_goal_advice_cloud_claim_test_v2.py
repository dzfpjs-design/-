"""Disposable-only schema experiment; never called by routes or startup.

A unique claim constrains cooperating v2 writers only. It cannot identify an
unmarked CloudCall inserted by an old, external or direct database writer.
It grants neither send authority nor proof of an unsent request.
"""
from sqlalchemy import text
from sqlalchemy.engine import Connection
from . import same_goal_advice_prepare_writer_candidate_v2 as guard
from . import same_goal_advice_store_v302 as store

TABLE='ga_test_advice_v2_dispatch_claims'
ACTION='same_goal_advice.unlimited_cloud_reserved_v2'


def require_test_database(db):
    """Check the actual transaction even for the isolated connection reader."""
    bound=db.get_bind()
    if not isinstance(bound,Connection):
        return guard.require_disposable_database(db)
    class _BoundReader:
        def get_bind(self):return bound.engine
        def execute(self,*args,**kwargs):return db.execute(*args,**kwargs)
    return guard.require_disposable_database(_BoundReader())


def _constraints(db):
    return dict(db.execute(text("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid=to_regclass('public.ga_test_advice_v2_dispatch_claims')")).all())


EXPECTED={
 'ga_test_v2_claim_pk':'PRIMARY KEY (workspace_id, job_id)',
 'ga_test_v2_claim_plan':'UNIQUE (workspace_id, plan_id)',
 'ga_test_v2_claim_call':'UNIQUE (cloud_call_id)',
 'ga_test_v2_claim_audit':'UNIQUE (audit_id)',
 'ga_test_v2_claim_workspace_fk':'FOREIGN KEY (workspace_id) REFERENCES workspaces(id)',
 'ga_test_v2_claim_plan_fk':'FOREIGN KEY (plan_id) REFERENCES plan_versions(id)',
 'ga_test_v2_claim_job_fk':'FOREIGN KEY (job_id) REFERENCES jobs(id)',
 'ga_test_v2_claim_call_fk':'FOREIGN KEY (cloud_call_id) REFERENCES cloud_model_calls(id)',
 'ga_test_v2_claim_audit_fk':'FOREIGN KEY (audit_id) REFERENCES audit_events(id)',
}


@store.checked
def require(db):
    require_test_database(db)
    store.need(db.get_nested_transaction() is None, '测试 claim 不支持保存点')
    db.execute(text('LOCK TABLE public.ga_test_advice_v2_dispatch_claims IN SHARE ROW EXCLUSIVE MODE NOWAIT'))
    columns=db.execute(text("SELECT attname, format_type(atttypid,atttypmod),attnotnull "
        "FROM pg_attribute WHERE attrelid=to_regclass('public.ga_test_advice_v2_dispatch_claims') "
        "AND attnum>0 AND NOT attisdropped ORDER BY attnum")).all()
    store.need(columns==[(name,'character varying(36)',True) for name in
        ('workspace_id','job_id','plan_id','cloud_call_id','audit_id')])
    store.need(_constraints(db)==EXPECTED)
    indices=db.execute(text("SELECT i.indisvalid,i.indisready,i.indislive FROM pg_index i "
        "JOIN pg_constraint c ON c.conindid=i.indexrelid "
        "WHERE c.conrelid=to_regclass('public.ga_test_advice_v2_dispatch_claims') AND c.contype IN ('p','u')")).all()
    store.need(len(indices)==4 and all(tuple(row)==(True,True,True) for row in indices))


@store.checked
def install(db):
    """Create only in an explicitly identified empty-history disposable PG.

    Never deletes, rewrites or repairs historical records or foreign objects.
    Existing claim objects are checked, not silently replaced.
    """
    require_test_database(db)
    store.need(db.get_nested_transaction() is None)
    # No candidate caller can interleave a CloudCall/Audit write with the
    # history check. Other applications must never run against this test DB.
    db.execute(text('LOCK TABLE public.cloud_model_calls, public.audit_events IN SHARE ROW EXCLUSIVE MODE NOWAIT'))
    existing=db.scalar(text("SELECT to_regclass('public.ga_test_advice_v2_dispatch_claims')"))
    if existing is not None:
        require(db)
        _no_orphans(db)
        return
    history=db.scalar(text("SELECT EXISTS(SELECT 1 FROM public.cloud_model_calls c "
        "LEFT JOIN public.jobs j ON j.id=(c.data->'gateway_dispatch'->'identity'->>'job_id') "
        "AND j.workspace_id=c.workspace_id WHERE c.data ? 'same_goal_advice_unlimited_v2' "
        "OR j.kind='generate_same_goal_advice_unlimited_v2') "
        "OR EXISTS(SELECT 1 FROM public.audit_events WHERE action=:action)"),{'action':ACTION})
    store.need(not history, '已存在 v2 云账本或回执历史；保留原记录等待核对')
    db.execute(text('''CREATE TABLE public.ga_test_advice_v2_dispatch_claims (
      workspace_id varchar(36) NOT NULL,
      job_id varchar(36) NOT NULL,
      plan_id varchar(36) NOT NULL,
      cloud_call_id varchar(36) NOT NULL,
      audit_id varchar(36) NOT NULL,
      CONSTRAINT ga_test_v2_claim_pk PRIMARY KEY (workspace_id,job_id),
      CONSTRAINT ga_test_v2_claim_plan UNIQUE (workspace_id,plan_id),
      CONSTRAINT ga_test_v2_claim_call UNIQUE (cloud_call_id),
      CONSTRAINT ga_test_v2_claim_audit UNIQUE (audit_id),
      CONSTRAINT ga_test_v2_claim_workspace_fk FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id),
      CONSTRAINT ga_test_v2_claim_plan_fk FOREIGN KEY (plan_id) REFERENCES public.plan_versions(id),
      CONSTRAINT ga_test_v2_claim_job_fk FOREIGN KEY (job_id) REFERENCES public.jobs(id),
      CONSTRAINT ga_test_v2_claim_call_fk FOREIGN KEY (cloud_call_id) REFERENCES public.cloud_model_calls(id),
      CONSTRAINT ga_test_v2_claim_audit_fk FOREIGN KEY (audit_id) REFERENCES public.audit_events(id)
    )'''))
    require(db)


def _no_orphans(db):
    orphan=db.scalar(text("SELECT EXISTS(SELECT 1 FROM public.cloud_model_calls c "
        "LEFT JOIN public.jobs j ON j.id=(c.data->'gateway_dispatch'->'identity'->>'job_id') "
        "AND j.workspace_id=c.workspace_id WHERE (c.data ? 'same_goal_advice_unlimited_v2' "
        "OR j.kind='generate_same_goal_advice_unlimited_v2') AND NOT EXISTS(SELECT 1 FROM "
        "public.ga_test_advice_v2_dispatch_claims x WHERE x.cloud_call_id=c.id AND x.workspace_id=c.workspace_id)) "
        "OR EXISTS(SELECT 1 FROM public.audit_events a WHERE a.action=:action AND NOT EXISTS "
        "(SELECT 1 FROM public.ga_test_advice_v2_dispatch_claims x WHERE x.audit_id=a.id AND x.workspace_id=a.workspace_id))"),{'action':ACTION})
    store.need(not orphan, '已存在未归属 v2 云账本或回执；保留原记录等待核对')
    mismatch=db.scalar(text("SELECT EXISTS(SELECT 1 FROM public.ga_test_advice_v2_dispatch_claims x "
        "JOIN public.cloud_model_calls c ON c.id=x.cloud_call_id "
        "JOIN public.audit_events a ON a.id=x.audit_id JOIN public.jobs j ON j.id=x.job_id "
        "JOIN public.plan_versions p ON p.id=x.plan_id WHERE "
        "c.workspace_id IS DISTINCT FROM x.workspace_id OR a.workspace_id IS DISTINCT FROM x.workspace_id "
        "OR j.workspace_id IS DISTINCT FROM x.workspace_id OR p.workspace_id IS DISTINCT FROM x.workspace_id "
        "OR j.kind IS DISTINCT FROM 'generate_same_goal_advice_unlimited_v2' "
        "OR a.action IS DISTINCT FROM :action OR a.object_id IS DISTINCT FROM x.plan_id "
        "OR a.data->>'schema' IS DISTINCT FROM 'same-goal-advice-unlimited-cloud-reservation-test-v2' "
        "OR a.data->>'workspace_id' IS DISTINCT FROM x.workspace_id "
        "OR a.data->>'plan_id' IS DISTINCT FROM x.plan_id OR a.data->>'job_id' IS DISTINCT FROM x.job_id "
        "OR a.data->>'audit_id' IS DISTINCT FROM x.audit_id OR a.data->>'call_id' IS DISTINCT FROM x.cloud_call_id "
        "OR a.data->>'actor_id' IS DISTINCT FROM a.actor_id OR c.data->>'actor_id' IS DISTINCT FROM a.actor_id "
        "OR a.data->>'task_id' IS DISTINCT FROM p.task_id OR j.data->>'task_id' IS DISTINCT FROM p.task_id "
        "OR j.data->>'plan_id' IS DISTINCT FROM x.plan_id OR c.data->'same_goal_advice_unlimited_v2' IS DISTINCT FROM a.data "
        "OR c.capability_id IS DISTINCT FROM a.data->'model_pin'->>'id' "
        "OR c.data->>'request_hash' IS DISTINCT FROM a.data->>'request_hash' "
        "OR c.data->>'dispatch_options_hash' IS DISTINCT FROM a.data->>'dispatch_options_hash' "
        "OR c.data->>'day' IS DISTINCT FROM a.data->>'day' "
        "OR to_jsonb(c.reserved_tokens) IS DISTINCT FROM a.data->'token_bound')"),{'action':ACTION})
    store.need(not mismatch, 'v2 云账本 claim 与原始回执或任务不一致；保留全部记录等待核对')


@store.checked
def lock_and_check(db):
    """Fence test-only history during creation; never install missing DDL."""
    require_test_database(db)
    db.execute(text('LOCK TABLE public.cloud_model_calls, public.audit_events IN SHARE ROW EXCLUSIVE MODE NOWAIT'))
    store.need(db.scalar(text("SELECT to_regclass('public.ga_test_advice_v2_dispatch_claims')")) is not None)
    require(db); _no_orphans(db)
