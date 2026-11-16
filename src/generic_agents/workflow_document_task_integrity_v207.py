"""Historical V207 result checks for approval outcome and archival.

These checks grant no execution, repair, consent or paid-call authority. In
particular a completed declared producer cannot disappear into a generic upload.
"""
from sqlalchemy import select

from . import models as m, runtime_task_documents as sources
from . import workflow_document_packet_v207 as packet
from .services import scoped


def verify_task_results(db, p, task, graph):
    """Validate completed new-family branches; leave legacy graphs untouched."""
    producers = {node['id'] for node in graph.get('nodes', []) if node.get('kind') == 'ability'
        and node.get('execution', {}).get('adapter') == packet.ADAPTER}
    if not producers and not sources.ports(graph): return
    _, original, _ = sources.task_context(db, p, task)
    sources.need(sources.same(original, graph))
    from . import full_semantic_graph_v1 as pure, workflow_graph_scope as scope
    from . import business_reasoning_v197 as reasoning
    state = pure.check_run(graph, task.data['graph_runtime'])
    for activation in state['activations'].values():
        if activation['status'] != 'completed': continue
        node = next(node for node in graph['nodes'] if node['id'] == activation['node_id'])
        if node['kind'] != 'ability' or node['execution'].get('adapter') not in {packet.ADAPTER, reasoning.ADAPTER}: continue
        _, _, _, frozen_graph, current, actual, frozen_node = scope._frozen(db, p, task.id, activation['id'])
        sources.need(sources.same(frozen_graph, graph) and sources.same(actual, activation)
            and sources.same(frozen_node, node))
        transport = task.data.get('graph_jobs', {}).get(activation['id'])
        sources.need(type(transport) is dict and transport.get('phase') == 'succeeded')
        job = scoped(db, m.Job, transport.get('job_id'), p)
        scope._job_matches(task, job, current, actual, node)
        call = scoped(db, m.Invocation, transport.get('invocation_id'), p)
        sources.need(job.status == call.status == 'succeeded' and call.data.get('job_id') == job.id
            and call.idempotency_key == job.data.get('invocation_key')
            and sources.same(call.data.get('output'), activation['output']))
        if node['execution']['adapter'] == packet.ADAPTER:
            rows = list(db.scalars(select(packet.Packet).where(packet.Packet.workspace_id == p.workspace_id,
                packet.Packet.invocation_id == call.id)))
            sources.need(len(rows) == 1 and rows[0].task_id == task.id,
                '已完成的资料包节点缺少原独立库存；未将它改作普通文件或重新生产')
            packet.checked_packet(db, p, rows[0])
        else:
            cap = scoped(db, m.Capability, node['execution']['capability_id'], p)
            sources.need(cap.content_hash == node['execution']['capability_hash']
                and call.capability_id == cap.id and call.data.get('capability_hash') == cap.content_hash)
            result = reasoning.saved_result(db, p, task, job, call, cap, job.data['inputs'])
            sources.need(sources.same(result, activation['output']))
