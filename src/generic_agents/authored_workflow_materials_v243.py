"""Parsed authored-flow sources retain their own original adoption authority.

An authored material never borrows a full-report qualification or the legacy
needs_input exception. The shared store checks original command/audit/revision
and physical custody; this adapter binds that proof to a compiled material.
"""
import copy
from fastapi import HTTPException
from .contracts import digest
from . import authored_workflow_store_v208 as store

KEY = 'authored_workflow_sources'
BINDINGS = {'authored-workflow-binding-v208','authored-image-workflow-binding-v243',
    'authored-service-workflow-binding-v270', 'authored-decision-workflow-binding-v278', 'authored-decision-workflow-binding-v286', 'authored-goal-data-workflow-binding-v288'}
FIELDS = ('id','version','hash','evidence_id','status','archived','blocks_hash')


def need(value):
    if not value: raise HTTPException(409, '人工编制流程的原文、解析代次或采用依据无法核对；未借用完整报告资格')


def source_origins(db, p, configuration, graph_bindings, business_names):
    result = {}
    for name in sorted(business_names):
        binding = graph_bindings[name]
        need(binding.get('schema') in BINDINGS)
        origin = configuration.get(store.CONFIG_KEY, {}).get(name)
        actual = store.checked_origin(db,p,origin)
        need(store.same(actual,binding))
        materials = store.verify_materials(db,p,actual,current=True)
        for material in materials:
            need(material.get('status') == 'parsed' and material.get('archived') is False)
            values = result.setdefault(material['id'],[])
            if not any(store.same(value,origin) for value in values): values.append(copy.deepcopy(origin))
    return result


def verify_material_snapshot(db, p, material):
    try:
        origins = material[KEY]
        need(type(origins) is list and 1 <= len(origins) <= 20
            and len({digest(origin) for origin in origins}) == len(origins)
            and material.get('status') == 'parsed' and 'business_flow_execution_sources' not in material)
        for origin in origins:
            binding = store.checked_origin(db,p,origin)
            need(binding.get('schema') in BINDINGS)
            # Historical custody is allowed after archival; the caller still
            # requires actual parsed status/version and controls archive use.
            originals = store.verify_materials(db,p,binding,current=False)
            selected = [item for item in originals if item['id'] == material['id']]
            # Legacy sourceId ordering intentionally retains repetitions.
            # Every such original pin must agree; it is still one material.
            need(bool(selected) and all(item['status'] == 'parsed'
                and all(key in material and store.same(item[key],material[key]) for key in FIELDS)
                for item in selected))
        return copy.deepcopy(material)
    except HTTPException as error:
        if error.status_code != 404: raise
        need(False)
    except (KeyError,TypeError,ValueError,AttributeError,IndexError): need(False)
