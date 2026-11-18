"""Fail-closed gate until scoped report dispatch is integrated end to end.

This is intentionally not configurable by environment or request parameters.
It must not grant authority until the prebound ledger, transport intent,
worker lease and response recovery are integrated and independently verified.
"""


def ready():
    return False
