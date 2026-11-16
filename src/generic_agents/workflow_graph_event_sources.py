"""Release boundary for graph waits whose real source protocol is unavailable.

The pure engine can express and reduce typed waits. No production event source
currently issues that typed contract with a durable acceptance/deadline ordering
protocol. Legacy outbox fan-out and source observation times do not establish
that capability. This gate grants no source registration or runtime authority.

Callers must still validate the full graph and enforce their existing authority
checks. Apply this only to new executable adoption/enabling/execution paths;
historical reads, saving human drafts and cancellation must remain available.
"""
from fastapi import HTTPException


UNAVAILABLE = (
    '流程等待消息的接入能力尚未完成，暂时不能采用或启用这类流程。'
    '完整设计、历史记录和已保存草稿会保留，已有任务仍可取消。'
)
INVALID = '完整流程的事件来源范围无法核对，请保留原设计重新读取。'
_WITHOUT_WAIT = frozenset({'human', 'ability', 'decision', 'fork', 'join', 'end'})


def require_supported_event_sources(graph):
    """Reject every declared subscription/wait until a real protocol exists.

    There is intentionally no synthetic supported-source list or capability
    flag. Matching an arbitrary legacy event name is not a registered producer.
    A valid graph with no event subscriptions or wait nodes passes unchanged.
    """
    if (type(graph) is not dict or graph.get('schema') != 'full-semantic-graph-v1'
            or type(graph.get('subscriptions')) is not list
            or type(graph.get('nodes')) is not list):
        raise HTTPException(409, INVALID)
    if graph['subscriptions']:
        raise HTTPException(409, UNAVAILABLE)
    for node in graph['nodes']:
        if type(node) is not dict or type(node.get('kind')) is not str:
            raise HTTPException(409, INVALID)
        if node['kind'] == 'wait':
            raise HTTPException(409, UNAVAILABLE)
        if node['kind'] not in _WITHOUT_WAIT:
            raise HTTPException(409, INVALID)
