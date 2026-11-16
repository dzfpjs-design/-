"""Whitelist existing human flows for an explicitly previewed cloud request."""
from .instance_compiler import flow_for


def workflow_context(configuration, authorized_blocks, *, scrub_text):
    """Project selected evidence only; never alter the saved configuration."""
    source = {}
    for block in authorized_blocks:
        if all(isinstance(block.get(key), str) for key in ('material_id', 'block_id', 'text')):
            source.setdefault((block['material_id'], block['block_id']), []).append(block['text'])
    selected = {identity for identity, _ in source}

    def text(value):
        return scrub_text(value) if isinstance(value, str) else ''

    def citations(item):
        projected, omitted = [], 0
        for citation in item.get('citations', []):
            if not isinstance(citation, dict) or any(not isinstance(citation.get(key), str)
                    for key in ('material_id', 'block_id', 'quote')):
                omitted += 1
                continue
            identity, block = citation['material_id'], citation['block_id']
            quote = text(citation['quote'])
            if not quote or not any(quote in original for original in source.get((identity, block), [])):
                omitted += 1
                continue
            projected.append({'material_id': identity, 'block_id': block, 'quote': quote})
        return projected, omitted

    result = {}
    for business in configuration.get('businesses', []):
        name = business if isinstance(business, str) else business['name']
        flow = flow_for(configuration, name)
        route = {step.get('id'): text(step.get('name', '未命名步骤')) for step in flow.get('steps', [])}
        route.update({'auto': '顺序进入下一步', 'end': '结束流程'})
        refs, omitted = citations(flow)
        projected = {'trigger': text(flow.get('trigger', '')), 'output': text(flow.get('output', '')),
                     'citations': refs, 'inference_reason': text(flow.get('inferenceReason', '')),
                     'omitted_citation_count': omitted, 'steps': []}
        for step in flow.get('steps', []):
            refs, omitted = citations(step)
            value = {key: text(step.get(key, ''))
                     for key in ('name', 'purpose', 'agent', 'role', 'output', 'condition')}
            value.update(next_label=route.get(step.get('nextId', 'auto'), '去向待核对'),
                         else_label=route.get(step.get('elseId', 'end'), '去向待核对'),
                         citations=refs, inference_reason=text(step.get('inferenceReason', '')))
            # A joined old quote may contain unselected sources even when its
            # first sourceId is selected. Structured references are authoritative.
            if not step.get('citations'):
                legacy = text(step.get('citation', ''))
                if step.get('sourceId') and step['sourceId'] not in selected:
                    omitted += bool(legacy)
                else:
                    value['citation'] = legacy
            value['omitted_citation_count'] = omitted
            projected['steps'].append(value)
        result[text(name)] = projected
    return result
