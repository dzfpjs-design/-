"""Explicit managed weather Source issuance; no token entry or transport."""
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictInt, field_validator
from fastapi import APIRouter

from .device_gateway_api_v268 import P, DB, SafeGatewayRoute, VersionRequest
from . import device_gateway_store_v268 as store
from . import device_gateway_sources_v270 as sources

router = APIRouter(prefix='/device-gateways', route_class=SafeGatewayRoute)


class RegisterSource(VersionRequest):
    grant_id: UUID
    grant_expected_version: StrictInt = Field(ge=1)
    environment_id: UUID
    entity_type: str = Field(min_length=1, max_length=100)
    links: dict[str, dict[str, str]] = Field(min_length=1, max_length=4)
    extractor_id: UUID
    extractor_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirm_simulation: Literal[True]

    @field_validator('confirm_simulation', mode='before')
    @classmethod
    def explicit_simulation(cls, value):
        if value is not True:
            raise ValueError('请明确确认模拟接入范围')
        return value

    @field_validator('grant_id', 'environment_id', 'extractor_id', mode='before')
    @classmethod
    def exact_identifier(cls, value):
        store.canonical_uuid(value)
        return value


@router.post('/{connection_id}/sources')
def create_source(connection_id: str, body: RegisterSource, p: P, db: DB):
    return sources.create_source(db, p, body, connection_id=connection_id)


@router.get('/{connection_id}/sources')
def list_sources(connection_id: str, p: P, db: DB):
    return sources.list_sources(db, p, connection_id)
