"""Pure default schemas for two immutable, process-versioned report classes.

This memo contains only default schemas derived from fixed Pydantic classes. It contains no caller schema,
proof, options, model result, source material, identity or authorization decision.
Changing these classes requires the normal worker code-version restart; this is
not a general cache for dynamically rebuilt Pydantic models.
"""
import copy
from functools import lru_cache


def _fixed_model(model):
    # Lazy imports keep the cache independent from both fixed wire modules.
    from .report_evidence_map_slots_v5 import MapSlotsWireV5
    from .report_evidence_reduction_wire_v6 import ReductionWireV6
    if model is not MapSlotsWireV5 and model is not ReductionWireV6:
        raise ValueError('fixed_report_schema_model')
    return model


@lru_cache(maxsize=2)
def _cached_schema(model):
    return model.model_json_schema()


def fixed_report_schema(model):
    """Return a fresh deep copy of the exact fixed default Pydantic schema."""
    return copy.deepcopy(_cached_schema(_fixed_model(model)))
