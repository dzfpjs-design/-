"""Resume a synthetic Start in another disposable PG and lose HTTP response."""
import os
import pytest

from tests import test_same_goal_advice_common_disconnect_v1_pg as disconnect_test
from tests.test_same_goal_advice_manual_append_pg import reject_non_loopback_network
from tests.test_same_goal_advice_restored_provider_v1_pg import restore_case


@pytest.mark.skipif(not os.environ.get('GA_SYNTHETIC_ADVICE_RESTORE'),
    reason='requires an explicitly created isolated synthetic Start checkpoint')
def test_restored_disconnect_preserves_original_and_never_resends(monkeypatch, reject_non_loopback_network):
    case = restore_case(monkeypatch)
    # Restore the actual Start; only skip the separate long test-data builder.
    monkeypatch.setattr(disconnect_test, 'prepare_and_start', lambda _case, _monkeypatch: None)
    disconnect_test.test_v1_disconnect_is_not_retried_and_remains_in_counter(case, monkeypatch, reject_non_loopback_network)
