"""Version-specific privacy projection; no DB or cloud authority.

The future server preview must assemble its frame from authenticated current
DB originals and compare the entire derived projection with the saved request.
No client-supplied self-consistent snapshot is authority to send.
"""
from . import same_goal_advice_projection_v307 as old
from . import same_goal_advice_sources_v302 as sources
from . import same_goal_advice_unlimited_contracts_v2 as contracts


@sources.checked
def derive(frame, preparation, *, evidence_parts, evidence_pins):
    return old._derive_model(frame, preparation, evidence_parts=evidence_parts,
        evidence_pins=evidence_pins, preparation_model=contracts.PreparationSnapshot)
