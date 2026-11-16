"""Explicit managed-tool registration and passive, secret-free inventory."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter
from pydantic import Field, StrictInt, field_validator
from .device_gateway_api_v268 import P, DB, SafeGatewayRoute, VersionRequest
from . import device_gateway_store_v268 as store
from . import device_gateway_tools_v269 as tools

router = APIRouter(prefix='/device-gateways', route_class=SafeGatewayRoute)


class RegisterTool(VersionRequest):
    grant_id: UUID
    grant_expected_version: StrictInt = Field(ge=1)
    action: Literal['get_status', 'read_telemetry']

    @field_validator('grant_id', mode='before')
    @classmethod
    def exact_grant_id(cls, value):
        store.canonical_uuid(value)
        return value


@router.post('/{connection_id}/tools')
def create_tool(connection_id: str, body: RegisterTool, p: P, db: DB):
    return tools.create_tool(db, p, body, connection_id=connection_id)


@router.get('/{connection_id}/tools')
def list_tools(connection_id: str, p: P, db: DB):
    return tools.list_tools(db, p, connection_id)
