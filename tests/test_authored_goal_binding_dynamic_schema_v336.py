"""The original scoped binding check must notice real schema validator changes."""
import pytest
from fastapi import HTTPException
from generic_agents import authored_goal_binding_parse_scope_v300 as scopes
from generic_agents import authored_goal_data_workflow_store_v288 as store
from tests.test_authored_goal_data_workflow_v288 import cold_binding,command


def test_changed_runtime_packet_schema_cannot_reuse_old_binding(monkeypatch):
    binding=cold_binding(command())
    with scopes.authored_goal_binding_parse_scope():
        assert store.check_binding(binding)==binding
        module=store.template.module_for('evaluation_packet')
        monkeypatch.setattr(module,'output_schema',lambda:{'type':'string'})
        with pytest.raises((HTTPException,ValueError,KeyError)):
            store.check_binding(binding)


def test_schema_value_changes_even_when_function_identity_is_stable(monkeypatch):
    binding=cold_binding(command());module=store.template.module_for('evaluation_packet')
    original=module.output_schema;state={'changed':False}
    def dynamic():return {'type':'string'} if state['changed'] else original()
    monkeypatch.setattr(module,'output_schema',dynamic)
    with scopes.authored_goal_binding_parse_scope():
        assert store.check_binding(binding)==binding
        state['changed']=True
        with pytest.raises((HTTPException,ValueError,KeyError)):
            store.check_binding(binding)


