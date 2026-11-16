"""Mechanical review scope only: no judgments, source status or execution rights.

The strict decision contract already requires these original ordered arrays.
Expose them explicitly so a model need not infer scope from enclosing packets.
Historical requests remain frozen; this is used only when preparing new ones.
"""
from . import business_decision_contracts_v274 as decision

SCHEMA='business-decision-review-assignment-v283'


def review_assignment(inputs):
    value=decision.check_input(inputs)
    requirements=[row['requirement_ref'] for row in value['requirements']]
    return {'schema':SCHEMA,'input_hash':decision.input_hash(value),
        'candidate_scope':[{'candidate_ref':row['candidate_ref'],
            'capability_refs':list(row['capability_refs']),
            'requirement_refs':list(requirements)} for row in value['candidates']],
        'source_scope':[{'source_ref':row['source_ref'],
            'fact_refs':[fact['fact_ref'] for fact in value['facts']
                if any(citation['source_ref']==row['source_ref'] for citation in fact['source_refs'])]}
            for row in value['sources']]}
