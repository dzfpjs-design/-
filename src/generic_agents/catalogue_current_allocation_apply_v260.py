"""Fixed V260 parent adapter over the existing real Catalogue-save journey."""
import copy
import sys
from uuid import uuid5, NAMESPACE_URL

from pydantic import Field, StrictStr, field_validator

from . import catalogue_current_allocation_v260 as current
from . import report_business_catalogue_allocation_apply_v213 as original
from . import report_business_catalogue_store_v211 as catalogue
from .services import require

OPERATION = 'apply-current-allocation'
DATA_KEY = 'current_allocation_apply'
APPLICATION = 'report-business-catalogue-current-allocation-application-v260'
LINK_SCHEMA = 'report-business-catalogue-current-allocation-link-v260'
LINK_AUDIT = 'report.catalogue_current_allocation_apply_link_v260'
CHILD_LINK_AUDIT = 'report.catalogue_current_allocation_child_link_v260'
SELECTION_PREVIEW = 'report-business-catalogue-current-allocation-selection-preview-v260'
MARKER_KEYS = original.MARKER_KEYS | {'binding_id', 'binding_record_hash', 'current_scope_hash'}


class SelectionBody(original.AllocationSelectionBody):
    binding_id: StrictStr
    binding_record_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    current_scope_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')

    @field_validator('binding_id')
    @classmethod
    def binding_identity(cls, value):
        return catalogue.identity(value)


class ApplyBody(SelectionBody):
    request_id: StrictStr
    expected_apply_proposal_hash: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    confirm: bool = Field(strict=True)

    @field_validator('request_id')
    @classmethod
    def nonce(cls, value):
        return catalogue.identity(value)

    @field_validator('confirm')
    @classmethod
    def explicit(cls, value):
        if value is not True:
            raise ValueError('请明确采纳并保存所选归属建议')
        return value


def _self():
    return sys.modules[__name__]


def path(consumer_id):
    return current._consumer().PREFIX + '/' + catalogue.identity(consumer_id) + '/' + OPERATION


def child_request(request_id, workspace_id=None):
    return str(uuid5(NAMESPACE_URL, APPLICATION + ':' + catalogue.identity(workspace_id)
        + ':' + catalogue.identity(request_id)))


def marker_pins(row):
    current.need(current._consumer()._family(row.data['preview']['preparation']).id == current.PROFILE)
    prep = row.data['preview']['preparation']; current.native_preparation(prep)
    return {'binding_id': prep['admission_basis']['binding_id'],
        'binding_record_hash': prep['admission_basis']['binding_record_hash'],
        'current_scope_hash': prep['current_scope']['current_scope_hash']}


def _basis(row):
    current.native_preparation(row.data['preview']['preparation'])
    return original.checked_allocation_basis(row, current.PROFILE)


def _proposal(row, selected):
    evidence, raw, projection = _basis(row)
    return original.proposal_from_verified(evidence, raw, projection, selected)


def _selected_proposal(row, body):
    pins = marker_pins(row)
    current.need(all(getattr(body, key) == value for key, value in pins.items()))
    return original._selected_proposal(row, body, _adapter=_self())


def before_selection(db, p, row, body):
    require(p, 'admin', 'operator')
    current.verify_history(db, p, row)
    _selected_proposal(row, body)


def _link_core(p, row, command, child, proposal):
    core = original._link_core(p, row, command, child, proposal)
    return {**core, 'schema': LINK_SCHEMA, **marker_pins(row)}


def prepare_apply_source(db, p, row, body, frozen):
    _selected_proposal(row, body)
    original_body = current.PreviewBody.model_validate(row.data['preview']['body'])
    return current.prepare_source(db, p, original_body, lock=True)


def preview_selection(db, p, consumer_id, body):
    return original.preview_selection(db, p, consumer_id, body, _adapter=_self())


def apply_selection(db, p, consumer_id, raw, *, actual_method, actual_path):
    return original.apply_selection(db, p, consumer_id, raw,
        actual_method=actual_method, actual_path=actual_path, _adapter=_self())


def check_parent_command(db, p, row, command, *, ref):
    current.check_preview(row.data['preview'], p)
    return original.check_parent_command(db, p, row, command, ref=ref, _adapter=_self())


def check_inventory_links(db, p, by_consumer, by_command, link_events):
    return original.check_inventory_links(db, p, by_consumer, by_command, link_events, _adapter=_self())


def check_catalogue_links(db, p, by_draft, by_command, link_events):
    return original.check_catalogue_links(db, p, by_draft, by_command, link_events, _adapter=_self())
