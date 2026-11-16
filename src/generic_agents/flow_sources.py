"""Pure source ordering shared by flow confirmation and recovery."""


def flow_source_ids(flow):
    """Keep legacy sourceId repetitions, then add unseen citation materials."""
    steps = flow.get('steps', [])
    sources = [step['sourceId'] for step in steps
               if isinstance(step, dict) and isinstance(step.get('sourceId'), str) and step['sourceId']]
    seen = set(sources)
    for citations in [*(step.get('citations', []) for step in steps if isinstance(step, dict)),
                      flow.get('citations', [])]:
        for citation in citations:
            identity = citation.get('material_id') if isinstance(citation, dict) else None
            if isinstance(identity, str) and identity and identity not in seen:
                sources.append(identity)
                seen.add(identity)
    return sources
