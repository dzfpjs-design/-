"""New explicit test-target allowance keeps actual DB/port/owner checks."""
from types import SimpleNamespace as NS
from contextlib import contextmanager
import pytest
from tests import test_report_full_reading_source as source


def target(monkeypatch, *, port=53679, database='ga_f11_dual_tests', host='127.0.0.1',
        marker='53679', optin=True, actual=None):
    calls=[]
    @contextmanager
    def connect():
        calls.append('read_actual_database_identity')
        yield NS(execute=lambda _sql:NS(one=lambda:actual or
            ('ga_f11_dual_tests',53679,'owned-disposable-f11-dual')))
    monkeypatch.setattr(source,'engine',lambda:NS(url=NS(port=port,database=database,host=host),connect=connect))
    monkeypatch.setenv('GA_F11_DUAL_TESTS_OWNED',marker)
    if optin:monkeypatch.setenv('GA_V197_LEGACY_EXACT_DATABASE','owned-physical-pg-v175-v197')
    else:monkeypatch.delenv('GA_V197_LEGACY_EXACT_DATABASE',raising=False)
    return calls


def test_current_owned_port_requires_the_physical_database_identity(monkeypatch):
    calls=target(monkeypatch);source.fence()
    assert calls==['read_actual_database_identity']


@pytest.mark.parametrize('change',[
    {'optin':False},{'database':'saved_map_design_v176_application'},
    {'host':'production.example'},{'port':8765},{'marker':'not-a-port'},
    {'actual':('ga_f11_dual_tests',53679,'unowned')},
    {'actual':('application',53679,'owned-disposable-f11-dual')},
    {'actual':('ga_f11_dual_tests',5432,'owned-disposable-f11-dual')},
])
def test_current_marker_never_waives_endpoint_database_or_actual_owner(monkeypatch,change):
    target(monkeypatch,**change)
    with pytest.raises(AssertionError):source.fence()


def test_old_explicit_local_target_is_unchanged(monkeypatch):
    calls=target(monkeypatch,port=63131,marker='63131',optin=False)
    source.fence();assert calls==[]
