"""Exact immutable partial-design families; never whole-report selection.

New flow-only answers use their own preparation/decoder and activity. Existing
V181 bytes, original failures, command bodies and financial records stay V181.
"""
from types import SimpleNamespace

SCOPED='scope-v181'
FLOW='flow-v182'
FLOW_COMPAT='flow-v183'
FLOW_PLAN='flow-v184'
FLOW_REVISION='flow-v185'
FLOW_REASONED='flow-v187'
FLOW_CAUSAL='flow-v190'
FLOW_NESTED='flow-v193'
MARKERS={SCOPED:'report-scoped-design-preparation-v181',FLOW:'report-business-flow-preparation-v182',
    FLOW_COMPAT:'report-business-flow-preparation-v183',FLOW_PLAN:'report-business-flow-preparation-v184',
    FLOW_REVISION:'report-business-flow-preparation-v185',FLOW_REASONED:'report-business-flow-preparation-v187',
    FLOW_CAUSAL:'report-business-flow-preparation-v190',FLOW_NESTED:'report-business-flow-preparation-v193'}

def is_flow(family):
    return family.id in {FLOW,FLOW_COMPAT,FLOW_PLAN,FLOW_REVISION,FLOW_REASONED,FLOW_CAUSAL,FLOW_NESTED}

def modules(profile_id):
    if profile_id==SCOPED:
        from . import report_scoped_design_native_v181 as native
        from . import report_scoped_design_preparation_v181 as preparation
        from . import report_scoped_design_wire_v181 as wire
    elif profile_id==FLOW:
        from . import report_business_flow_native_v182 as native
        from . import report_business_flow_preparation_v182 as preparation
        from . import report_business_flow_wire_v182 as wire
    elif profile_id==FLOW_COMPAT:
        from . import report_business_flow_native_v183 as native
        from . import report_business_flow_preparation_v183 as preparation
        from . import report_business_flow_wire_v182 as wire
    elif profile_id==FLOW_PLAN:
        from . import report_business_flow_native_v184 as native
        from . import report_business_flow_preparation_v184 as preparation
        from . import report_business_flow_wire_v184 as wire
    elif profile_id==FLOW_REVISION:
        from . import report_business_flow_native_v185 as native
        from . import report_business_flow_preparation_v185 as preparation
        from . import report_business_flow_wire_v185 as wire
    elif profile_id==FLOW_REASONED:
        from . import report_business_flow_native_v187 as native
        from . import report_business_flow_preparation_v187 as preparation
        from . import report_business_flow_wire_v184 as wire
    elif profile_id==FLOW_CAUSAL:
        from . import report_business_flow_native_v190 as native
        from . import report_business_flow_preparation_v190 as preparation
        from . import report_business_flow_wire_v190 as wire
    elif profile_id==FLOW_NESTED:
        from . import report_business_flow_native_v193 as native
        from . import report_business_flow_preparation_v193 as preparation
        from . import report_business_flow_wire_v193 as wire
    else:raise ValueError('Unknown independent partial-design family')
    return SimpleNamespace(id=profile_id,native=native,preparation=preparation,wire=wire)

def selected(preparation):
    if type(preparation) is not dict:raise ValueError('Missing independent preparation')
    matches=[key for key,marker in MARKERS.items() if preparation.get('schema')==marker]
    if len(matches)!=1:raise ValueError('Unknown independent preparation; no decoder fallback')
    return modules(matches[0])
