"""Unknown protocol rejection must happen before any journal or provider IO."""
from types import SimpleNamespace

import pytest

from generic_agents import business_reasoning_capture_v275 as capture


@pytest.mark.parametrize('protocol', [None, object(),
    SimpleNamespace(__name__='generic_agents.business_reasoning_v197')])
def test_workspace_like_protocol_names_cannot_select_an_executable_module(protocol):
    class ForbiddenDB:
        def __getattribute__(self, name):
            pytest.fail('unknown protocol attempted journal IO')

    with pytest.raises(ValueError, match='Unsupported server-owned'):
        capture.execute(ForbiddenDB(), object(), object(), {}, 'unknown', protocol=protocol)
