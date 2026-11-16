"""Privacy semantics for unreadable policy input reasons, not authority checks.

No shape/hash/binding/authority validator is patched or replaced. Root's real
PG recovery journey separately proves that the display branch is reached.
"""
import pytest

from generic_agents.threshold_policy_views_v153 import selection_unreadable_issue


REVOKED = '原操作者执行授权已撤销；已停止此输入，原记录保留，不重新计算'
UNREADABLE = '当前固定审核、规则或来源不可用于这些机器数值；原输入及核查历史保留'


def test_exact_revoked_stop_explains_the_stop_and_the_current_display_limit():
    assert selection_unreadable_issue('stopped', REVOKED) == REVOKED + '；' + UNREADABLE


@pytest.mark.parametrize('status', ['queued', 'reserved', 'completed', 'failed'])
def test_other_actual_states_do_not_inherit_the_revoked_stop_explanation(status):
    assert selection_unreadable_issue(status, REVOKED) == UNREADABLE


@pytest.mark.parametrize('saved_error', [
    REVOKED + '；候选阈值18，原输入19.0',
    REVOKED + ' ',
    REVOKED + '；另有内部诊断',
    '原操作者执行授权已撤销',
    '规则阈值18、原输入19.0导致处理失败',
    None,
    19.0,
], ids=['machine-suffix', 'space-suffix', 'other-suffix', 'partial-reason',
        'machine-error', 'no-error', 'non-text-error'])
def test_unreadable_machine_or_noncanonical_errors_are_never_echoed(saved_error):
    assert selection_unreadable_issue('stopped', saved_error) == UNREADABLE
