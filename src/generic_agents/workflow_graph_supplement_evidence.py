"""Cold evidence for the completed V288 data-review route and its supplements."""
from fastapi import HTTPException
from sqlalchemy import select

from . import goal_evaluation_provider_v288 as provider
from . import workflow_job_dependencies_v264 as controls
from .contracts import digest
from .models import Audit, Plan

SCHEMA = 'workflow-graph-supplement-evidence-v1'
SKIPPED = ('packet', 'reasoning', 'review_decision')


def need(value, message='原资料核对分支、补充评估或独立复核原件无法核对'):
    if not value:
        raise HTTPException(409, message)


def candidate(task):
    """A cheap display hint. Actual release and receipts are verified separately."""
    state = task.data.get('graph_runtime')
    return (type(state) is dict and state.get('status') == 'blocked'
        and state.get('error') == 'required_receipts_missing')


def _route(graph, binding, state):
    nodes = {node['id']: node for node in graph['nodes']}
    activations = {}
    for identity, item in state['activations'].items():
        need(item['id'] == identity and item['node_id'] not in activations
            and item['status'] == 'completed' and item['epoch'] == state['cancel_epoch']
            and item['scope_id'] is None and item['branch_id'] is None)
        activations[item['node_id']] = item
    human_nodes = [item['node_id'] for item in binding['step_mapping']]
    prefix = ['context_reader', 'local_prepare', 'route_acquisition']
    choices = {'route_evaluated': 'false'}
    acquisition = activations['route_acquisition']['output']
    need(acquisition in ({'truth': 'true'}, {'truth': 'false'}))
    choices['route_acquisition'] = acquisition['truth']
    if acquisition['truth'] == 'true':
        prefix.append('resense')
    else:
        prefix.append('route_ready')
        choices['route_ready'] = 'true'
    route = [*prefix, 'evaluation_packet', 'route_evaluated', 'review_data', *human_nodes, 'end']
    need(len(route) == len(set(route)) and set(activations) == set(route)
        and not set(SKIPPED) & set(activations)
        and state['goal_receipts'] == [] and state['ready_human_ids'] == [] and state['eligible_ability_ids'] == []
        and nodes['end']['kind'] == 'end' and activations['end']['output'] == {}
        and type(activations['evaluation_packet']['output']) is dict
        and activations['evaluation_packet']['output'].get('route') == 'review_required')
    for node_id, truth in choices.items():
        need(nodes[node_id]['kind'] == 'decision' and activations[node_id]['output'] == {'truth': truth})
    expected = []
    for start, end in zip(route, route[1:]):
        edges = [edge for edge in graph['edges'] if edge['from'] == start and edge['to'] == end
            and edge['on'] == choices.get(start, 'success')
            and edge['branch_id'] is None and edge['loop_id'] is None]
        need(len(edges) == 1)
        expected.append({'kind': 'link', 'source_activation_id': activations[start]['id'],
            'activation_id': activations[end]['id'], 'edge_id': edges[0]['id']})
    return nodes, activations, route, expected


def _trace(db, p, task, state, nodes, activations, route, expected):
    control = controls.checked_control(db, p, task)
    need(control is not None and control['state_hash_at_capture'] == state['state_hash'])
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action == controls.CONTROL_AUDIT).limit(controls.MAX_CONTROLS + 1)))
    need(len(rows) == control['version'])
    rows.sort(key=lambda row: row.data['version'])
    need(rows[-1].data['record_hash'] == control['record_hash'])
    spawns, links, completed, producers = [], [], {}, {}
    for row in rows:
        for item in row.data['trace']:
            kind = item['kind']
            if kind == 'spawn':
                spawns.append(item)
            elif kind == 'link':
                links.append(item)
            elif kind == 'complete':
                identity = item['activation_id']
                need(identity not in completed)
                completed[identity] = row.actor_id
                producers[identity] = row.data['producer']
            else:
                need(False)
    need(links == expected and spawns == [{'kind': 'spawn', 'activation_id': activations[node_id]['id'],
        'node_id': node_id, 'scope_id': None, 'branch_id': None} for node_id in route])
    actual = [node_id for node_id in route if nodes[node_id]['kind'] in {'human', 'ability'}]
    need(list(completed) == [activations[node_id]['id'] for node_id in actual])
    for node_id in actual:
        node, item = nodes[node_id], activations[node_id]
        need(completed[item['id']] == node['actor_id'])
        producer = producers[item['id']]
        need((node['kind'] == 'human' and producer is None)
            or (node['kind'] == 'ability' and type(producer) is dict and producer['activation_id'] == item['id']))
    return control['record_hash']


def _humans(db, p, task, state, nodes, activations, route):
    from .workflow_graph_human_budget import verify as verify_budget
    budget_hash = verify_budget(db, p, task)
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == task.id, Audit.action == 'workflow.graph.human_completed').limit(len(route) + 1)))
    expected = [node_id for node_id in route if nodes[node_id]['kind'] == 'human']
    receipts = [receipt for receipt in task.data['workflow_runtime']['receipts']
        if receipt.get('execution_mode') == 'human_graph_result']
    need(len(rows) == len(receipts) == len(expected))
    audits = {row.data['activation_id']: row for row in rows}
    proof = []
    for node_id, receipt in zip(expected, receipts):
        node, item = nodes[node_id], activations[node_id]
        event = audits[item['id']]
        need(receipt['activation_id'] == item['id'] and receipt['actor'] == event.actor_id == node['actor_id']
            and receipt['step_id'] == (node['source_step_id'] or node_id)
            and digest(receipt['output']) == digest(item['output'])
            and type(receipt['output'].get('result')) is str and bool(receipt['output']['result'].strip())
            and receipt['entity_state_updated'] is False)
        if receipt.get('human_budget') is None:
            need(event.data == {'activation_id': item['id']})
        else:
            need(event.data == {'activation_id': item['id'], 'human_budget': receipt['human_budget'],
                'receipt_hash': digest(receipt), 'request_id': receipt['request_id']})
        matches = [command for command in state['commands'] if command['request_id'] == receipt['request_id']]
        command_body = {'activation_id': item['id'], 'output': receipt['output'], 'actor_id': receipt['actor'],
            'expected_version': item['version'] - 1, 'cancel_epoch': item['epoch']}
        need(matches == [{'request_id': receipt['request_id'], 'op': 'complete',
            'actor_id': receipt['actor'], 'body_hash': digest(command_body)}])
        proof.append({'activation_id': item['id'], 'node_id': node_id,
            'receipt_hash': digest(receipt), 'audit_id': event.id})
    return {'receipts': proof, 'budget_hash': budget_hash}


def _supplements(db, p, task, current):
    from . import same_goal_continuation_execution_goal_v314 as goal
    from . import same_goal_continuation_execution_store_v314 as store
    from . import same_goal_continuation_sources_v312 as advice

    gate = goal.require_settled(db, p, task)
    head = task.data[provider.KEY]
    metadata = current['evidence'][provider.VIEW]
    adopted = {row['plan_id'] for row in gate['runs'] if row['phase'] == 'adopted'}
    need(bool(adopted), '缺少已独立接收和采纳的办理结果')
    proofs, original_sources = [], {}
    for row in store._inventory(db, p, task):
        plan, proposal = row['plan'], row['proposal']
        if plan.id not in adopted:
            continue
        basis = proposal['grant']['basis']
        evaluation = basis['evaluation']
        if evaluation['evaluation_ref'] != current['evaluation_ref']:
            continue
        need(plan.status == 'adopted' and evaluation['goal_check_job_id'] == current['goal_check_job_id']
            and evaluation['goal_check_id'] == current['goal_check_id']
            and evaluation['report_hash'] == current['report_hash']
            and evaluation['mature_head_hash'] == head['head_hash']
            and evaluation['generation'] == metadata['generation'] and evaluation['cut_at'] == metadata['cut_at'])
        advice_id = proposal['advice_plan_id']
        if advice_id not in original_sources:
            original_plan = provider._fresh(db, p, Plan, advice_id)
            need(original_plan.task_id == task.id)
            _, _, review, source, _, _ = advice._source(db, p, task, original_plan)
            need(review['decision'] in {'accepted', 'needs_revision'})
            original_sources[advice_id] = source
        source = original_sources[advice_id]
        need(digest(source) == proposal['source_hash'] == digest(proposal['source']))
        proofs.append({'plan_id': plan.id, 'submission_hash': proposal['record_hash'],
            'output_ref': plan.data['output_ref'], 'receipt_ref': plan.data['receipt_ref'],
            'advice_review_ref': source['review_ref'], 'advice_data_as_of': source['data_as_of']})
    need(bool(proofs), '当前正式评估与已采纳办理的计算依据不同，不能沿用旧结果')
    return {'settlement_basis_hash': gate['basis_hash'], 'adoptions': sorted(proofs, key=lambda item: item['plan_id'])}


def verify(db, p, task, *, lock_sources=False):
    """Verify supplemental evidence for a completed fixed V288 false route."""
    if not candidate(task):
        return None
    try:
        with db.no_autoflush:
            selected = provider.binding(db, p, task)
            if selected is None:
                return None
            binding = selected['binding']; graph = binding['graph']; state = task.data['graph_runtime']
            need(task.workspace_id == p.workspace_id and type(lock_sources) is bool)
            from . import full_semantic_graph_v1 as kernel
            kernel.check_run(graph, state)
            nodes, activations, route, expected = _route(graph, binding, state)
            trace = _trace(db, p, task, state, nodes, activations, route, expected)
            humans = _humans(db, p, task, state, nodes, activations, route)
            current = provider.require_current(db, p, task, lock_sources=lock_sources)
            need(current is not None and current['evidence']['status'] == 'sufficient'
                and not current['evidence']['conflicts'])
            supplements = _supplements(db, p, task, current)
            evaluation = current['evidence'][provider.VIEW]
            body = {'task_id': task.id, 'graph_hash': state['graph_hash'], 'state_hash': state['state_hash'],
                'control_hash': trace, 'humans': humans, 'evaluation_ref': current['evaluation_ref'],
                'goal_check_id': current['goal_check_id'], 'report_hash': current['report_hash'],
                'supplements': supplements}
            return {'schema': SCHEMA, 'reason': '原图资料核对路径已结束，后续正式评估与采纳记录提供补充依据',
                'original_graph_status': state['status'], 'original_graph_error': state['error'],
                'skipped_node_ids': list(SKIPPED), 'evidence_generation': evaluation['generation'],
                'basis_hash': digest(body)}
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        raise HTTPException(409, '原资料核对分支、补充评估或独立复核原件无法核对') from None
