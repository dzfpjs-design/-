"""Unconnected v2 intent gate: fails closed until a real verifier is integrated.

Tests may explicitly monkeypatch this isolated interface. An Audit row or CloudCall
alone is never evidence that a request was sent or that an intent was valid.
"""
from fastapi import HTTPException


def check_original(db, workspace_id, cloud_call_id, intent_audit_id):
    raise HTTPException(409, '原发送意图及云账本验证器尚未接入；保留原记录，不得重发')
