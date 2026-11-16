"""Strict public SQL routing negatives, not real PG/source/paid acceptance."""
from types import SimpleNamespace

import pytest
from sqlalchemy import or_, select

from generic_agents import report_saved_map_design_consumer_v176 as consumer
from generic_agents.models import Audit, Job
from tests.test_report_saved_map_design_consumer_v176 import InventoryIO, identity, no_network_or_process


def principal():
    return SimpleNamespace(user_id=identity(1),workspace_id=identity(2),role='operator')


@pytest.mark.parametrize('action',[consumer.ALLOCATION_LINK_AUDIT,consumer.ALLOCATION_CHILD_LINK_AUDIT,
    consumer.CURRENT_ALLOCATION_LINK_AUDIT,consumer.CURRENT_ALLOCATION_CHILD_LINK_AUDIT])
def test_deleted_parent_link_with_changed_actor_is_still_reverse_inventory(action):
    p=principal()
    event=Audit(id=identity(11),workspace_id=p.workspace_id,actor_id=identity(999),
        action=action,object_id=identity(12),data={'actor_id':p.user_id})
    io=InventoryIO(p,audits=[event])
    # No Consumer or Command remains. The independent original payload still
    # identifies this actor, so changing Audit.actor_id cannot erase the trace.
    with pytest.raises(consumer.InventoryError):consumer._inventory(io,p)


def test_public_sql_fixture_filters_actual_link_workspace_actor_payload_and_action():
    p=principal();other=identity(999)
    def event(number,workspace,actor,action,payload_actor):
        return Audit(id=identity(number),workspace_id=workspace,actor_id=actor,
            action=action,object_id=identity(number+100),data={'actor_id':payload_actor})
    rows=[event(11,p.workspace_id,p.user_id,consumer.ALLOCATION_LINK_AUDIT,other),
        event(12,p.workspace_id,other,consumer.ALLOCATION_CHILD_LINK_AUDIT,p.user_id),
        event(13,p.workspace_id,other,consumer.ALLOCATION_LINK_AUDIT,other),
        event(14,other,p.user_id,consumer.ALLOCATION_LINK_AUDIT,p.user_id),
        event(15,p.workspace_id,p.user_id,consumer.STATE_AUDIT,p.user_id),
        event(16,p.workspace_id,p.user_id,consumer.CURRENT_ALLOCATION_LINK_AUDIT,other),
        event(17,p.workspace_id,other,consumer.CURRENT_ALLOCATION_CHILD_LINK_AUDIT,p.user_id),
        event(18,p.workspace_id,p.user_id,consumer.CURRENT_ALLOCATION_END_AUDIT,p.user_id),
        event(19,p.workspace_id,p.user_id,'report.unrelated_fixture_action',p.user_id)]
    io=InventoryIO(p,audits=rows)
    actions=[consumer.ALLOCATION_LINK_AUDIT,consumer.ALLOCATION_CHILD_LINK_AUDIT,consumer.ALLOCATION_END_AUDIT,
        consumer.CURRENT_ALLOCATION_LINK_AUDIT,consumer.CURRENT_ALLOCATION_CHILD_LINK_AUDIT,
        consumer.CURRENT_ALLOCATION_END_AUDIT]
    statement=select(Audit).where(Audit.workspace_id==p.workspace_id,
        or_(Audit.actor_id==p.user_id,Audit.data['actor_id'].as_string()==p.user_id),
        Audit.action.in_(actions))
    assert io.scalars(statement)==rows[:2]+rows[5:8]
    bad_queries=[select(Audit).where(Audit.action==consumer.ALLOCATION_LINK_AUDIT),
        select(Audit).where(Audit.workspace_id==p.workspace_id,Audit.action.in_(actions)),
        select(Audit).where(or_(Audit.actor_id==p.user_id,Audit.data['actor_id'].as_string()==p.user_id),
            Audit.action.in_(actions)),
        select(Audit).where(Audit.workspace_id==p.workspace_id,
            or_(Audit.actor_id==p.user_id,Audit.data['actor_id'].as_string()==p.user_id),
            Audit.action.in_([*actions,'report.unrelated_fixture_action']))]
    for bad in bad_queries:
        with pytest.raises(AssertionError,match='Unexpected SELECT'):
            io.scalars(bad)


def test_closure_probe_fixture_preserves_both_independent_discovery_arms_and_rejects_wide_sql():
    """Synthetic SQL predicate test, not a saved/accepted Closure proof."""
    from generic_agents import catalogue_current_allocation_closure_v260 as closure
    p=principal();nonce=identity(90);fid=closure.fence_identity(p.workspace_id,nonce)
    key='saved-map-design:'+nonce;other=identity(999)
    jobs=[Job(id=fid,workspace_id=p.workspace_id,kind='damaged-tag',status='internal',
            idempotency_key='damaged-key',data={}),
        Job(id=identity(91),workspace_id=p.workspace_id,kind=consumer.JOB,status='queued',
            idempotency_key=key,data={}),
        Job(id=identity(92),workspace_id=other,kind=closure.JOB_KIND,status='internal',
            idempotency_key=key,data={}),
        Job(id=identity(93),workspace_id=p.workspace_id,kind=consumer.JOB,status='queued',
            idempotency_key='unrelated:'+nonce,data={})]
    audits=[Audit(id=identity(94),workspace_id=p.workspace_id,actor_id=other,
            action=closure.AUDIT_ACTION,object_id=fid,data={'request_id':identity(95)}),
        Audit(id=identity(96),workspace_id=p.workspace_id,actor_id=other,
            action=closure.AUDIT_ACTION,object_id=identity(97),data={'request_id':nonce}),
        Audit(id=identity(98),workspace_id=other,actor_id=p.user_id,
            action=closure.AUDIT_ACTION,object_id=fid,data={'request_id':nonce}),
        Audit(id=identity(99),workspace_id=p.workspace_id,actor_id=p.user_id,
            action='report.unrelated_fixture_action',object_id=fid,data={'request_id':nonce})]
    io=InventoryIO(p,jobs=jobs,audits=audits)
    job_query=select(Job).where(Job.workspace_id==p.workspace_id,
        or_(Job.id==fid,Job.idempotency_key==key)).limit(3).execution_options(populate_existing=True)
    audit_query=select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.action==closure.AUDIT_ACTION,
        or_(Audit.object_id==fid,Audit.data['request_id'].as_string()==nonce)).limit(3)
    assert io.scalars(job_query)==jobs[:2]
    # Discovery is deliberately workspace-wide: the payload/object still ties
    # a changed-actor Audit to this nonce; only the real checker decides owner.
    assert io.scalars(audit_query)==audits[:2]
    bad_queries=[select(Job).where(or_(Job.id==fid,Job.idempotency_key==key)).limit(3),
        select(Job).where(Job.workspace_id==p.workspace_id).limit(3),
        job_query.limit(4),
        select(Audit).where(Audit.workspace_id==p.workspace_id,
            or_(Audit.object_id==fid,Audit.data['request_id'].as_string()==nonce)).limit(3),
        audit_query.limit(4)]
    for bad in bad_queries:
        with pytest.raises(AssertionError,match='Unexpected SELECT'):
            io.scalars(bad)
