"""One explicit V5 agent spelling alias, never schema or execution authority.

Keep the original paid wire and parsed response separately. This helper only
returns a copy for the caller's unchanged V5 validation, plus exact change
records. It does not validate a result, repair a container, or bind any role.
"""
import copy

POLICY = 'setup-v5-agent-aliases-v261'
ORIGINAL = '业务驱动智能体'
CANONICAL = '业务智能体'


def normalize_v5_agents(value, *, contract_version):
    """Return ``(copied_value, changes)`` without modifying the input.

    Only the literal integer version 5 enables this exact-path alias. Unknown
    values and malformed containers stay untouched for the normal schema gate.
    Empty/no-op inputs and a second pass on canonical output produce no trace.
    """
    result = copy.deepcopy(value)
    changes = []
    if type(contract_version) is not int or contract_version != 5 or type(result) is not dict:
        return result, changes
    businesses = value.get('businesses')
    if type(businesses) is not list:
        return result, changes
    for business_index, business in enumerate(businesses):
        if type(business) is not dict or type(business.get('steps')) is not list:
            continue
        for step_index, step in enumerate(business['steps']):
            if type(step) is not dict or type(step.get('agent')) is not str or step['agent'] != ORIGINAL:
                continue
            result['businesses'][business_index]['steps'][step_index]['agent'] = CANONICAL
            changes.append({'policy': POLICY,
                'path': '/businesses/' + str(business_index) + '/steps/' + str(step_index) + '/agent',
                'original': ORIGINAL, 'canonical': CANONICAL})
    return result, changes
