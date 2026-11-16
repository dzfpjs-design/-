"""Dispatch each simulated source to its own independent provenance contract."""


def guard(db, p, observation, *, execution=False, result=None):
    from .mapped_acquisition_origin_v177 import guard as mapped_guard
    mapped = mapped_guard(db, p, observation, execution=execution, result=result)
    if mapped is not None:
        return mapped
    from .simulation_origin_v176 import guard as sample_guard
    # The Sample contract retains all of its own trace and deletion checks.
    # A mapped contract never supplies a Sample identity or MQTT receipt.
    return sample_guard(db, p, observation, execution=execution)
