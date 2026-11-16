"""Explicit immutable decoder selection; no source or spending authority."""
import importlib
from types import SimpleNamespace

LEGACY = 'saved-v176'
INDEXED = 'indexed-v178'
COMPATIBLE = 'indexed-v179'
CATALOGUE = 'catalogue-v211'
ALLOCATION = 'catalogue-allocation-v213'
CURRENT_ALLOCATION = 'catalogue-current-allocation-v260'
PREPARATIONS = {
    'report-saved-map-design-preparation-v176': LEGACY,
    'report-saved-map-design-preparation-v178': INDEXED,
    'report-saved-map-design-preparation-v179': COMPATIBLE,
    'report-business-catalogue-preparation-v211': CATALOGUE,
    'report-business-catalogue-allocation-preparation-v213': ALLOCATION,
    'report-business-catalogue-current-allocation-preparation-v260': CURRENT_ALLOCATION,
}


def profile_id(preparation):
    if type(preparation) is not dict or preparation.get('schema') not in PREPARATIONS:
        raise ValueError('原方案生成版本无法核对，不能改用其他版本解释原回答')
    return PREPARATIONS[preparation['schema']]


def modules(identity):
    if identity not in {LEGACY, INDEXED, COMPATIBLE, CATALOGUE, ALLOCATION, CURRENT_ALLOCATION}:
        raise ValueError('未登记的方案生成版本')
    if identity == CURRENT_ALLOCATION:
        return SimpleNamespace(id=identity,
            preparation=importlib.import_module('.catalogue_current_allocation_v260', __package__),
            native=importlib.import_module('.report_business_catalogue_allocation_native_v213', __package__),
            wire=importlib.import_module('.report_business_catalogue_allocation_wire_v213', __package__))
    if identity == ALLOCATION:
        return SimpleNamespace(id=identity,
            preparation=importlib.import_module('.report_business_catalogue_allocation_preparation_v213', __package__),
            native=importlib.import_module('.report_business_catalogue_allocation_native_v213', __package__),
            wire=importlib.import_module('.report_business_catalogue_allocation_wire_v213', __package__))
    if identity == CATALOGUE:
        return SimpleNamespace(id=identity,
            preparation=importlib.import_module('.report_business_catalogue_preparation_v211', __package__),
            native=importlib.import_module('.report_business_catalogue_native_v211', __package__),
            wire=importlib.import_module('.report_business_catalogue_wire_v211', __package__))
    suffix = {LEGACY:'v176',INDEXED:'v178',COMPATIBLE:'v179'}[identity]
    wire = 'v174' if identity == LEGACY else suffix
    def module(name):
        return importlib.import_module('.' + name, __package__)
    return SimpleNamespace(
        id=identity,
        preparation=module('report_saved_map_design_preparation_' + suffix),
        native=module('report_design_grouped_native_' + suffix),
        wire=module('report_design_grouped_wire_' + wire),
    )
