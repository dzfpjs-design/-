import itertools
import pytest
from generic_agents.asr_quality import aggregate, edits, normalized, score


def test_known_errors_and_empty_output():
    assert edits(['a', 'b', 'c'], ['a', 'x', 'c']) == {
        'reference_units': 3, 'hypothesis_units': 3, 'substitutions': 1,
        'deletions': 0, 'insertions': 0, 'errors': 1, 'rate': 1/3}
    assert edits(['a', 'b'], ['a'])['deletions'] == 1
    assert edits(['a'], ['a', 'b', 'c'])['rate'] == 2
    assert edits(['a', 'b'], [])['deletions'] == 2
    with pytest.raises(ValueError, match='Empty reference'):
        score('... ', 'speech')


def test_declared_normalization_does_not_oracle_correct_numbers():
    assert normalized("‘ＤＯＮ’T’ red-blue, 林 场！２") == {
        'words': ["don't", 'red', 'blue', '林', '场', '2'],
        'characters': list('dontredblue林场2')}
    assert score('five', '5')['wer']['errors'] == 1


def test_weighted_aggregation_not_mean_of_clip_percentages():
    result = aggregate([score('a', 'x'), score('a b c d e f g h i', 'a b c d e f g h i')])
    assert result['wer']['rate'] == .1
    assert result['wer']['errors'] == result['wer']['substitutions'] == 1
    with pytest.raises(ValueError, match='No scored'):
        aggregate([])


def test_counts_match_independent_recursive_oracle_for_short_sequences():
    from functools import lru_cache
    @lru_cache(None)
    def distance(a, b):
        if not a or not b: return len(a) + len(b)
        return min(distance(a[1:], b[1:]) + (a[0] != b[0]),
                   distance(a[1:], b) + 1, distance(a, b[1:]) + 1)
    sequences = [tuple(x) for n in range(4) for x in itertools.product('ab', repeat=n)]
    for a in sequences[1:]:
        for b in sequences:
            result = edits(a, b)
            assert result['errors'] == distance(a, b)
            assert result['errors'] == sum(result[f] for f in ('substitutions', 'deletions', 'insertions'))
            assert len(a) - result['deletions'] + result['insertions'] == len(b)
