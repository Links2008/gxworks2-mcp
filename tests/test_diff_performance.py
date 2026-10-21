"""Structural performance checks: no machine-dependent timing thresholds."""

from difflib import SequenceMatcher
import itertools

import pytest

from gxworks2_mcp.il import parse_il
from gxworks2_mcp.store import diff_programs


def assert_reconstructs(left, right, result):
    assert result['before'] == len(left)
    assert result['after'] == len(right)
    rebuilt = []
    before_cursor = after_cursor = 0
    removed, added = [], []
    for change in result['changes']:
        i1, i2 = change['beforeRange']
        j1, j2 = change['afterRange']
        assert before_cursor <= i1 <= i2 <= len(left)
        assert after_cursor <= j1 <= j2 <= len(right)
        assert left[before_cursor:i1] == right[after_cursor:j1]
        assert change['removed'] == left[i1:i2]
        assert change['added'] == right[j1:j2]
        if change['operation'] == 'insert':
            assert i1 == i2 and j1 < j2
        elif change['operation'] == 'delete':
            assert i1 < i2 and j1 == j2
        else:
            assert change['operation'] == 'replace'
            assert i1 < i2 and j1 < j2
        rebuilt.extend(left[before_cursor:i1])
        rebuilt.extend(change['added'])
        removed.extend(change['removed'])
        added.extend(change['added'])
        before_cursor, after_cursor = i2, j2
    assert left[before_cursor:] == right[after_cursor:]
    rebuilt.extend(left[before_cursor:])
    assert rebuilt == right
    assert result['removed'] == removed
    assert result['added'] == added


@pytest.mark.parametrize('position', [0, 2500, 5000])
@pytest.mark.parametrize('operation', ['replace', 'insert', 'delete'])
def test_large_repeat_edit_matches_only_changed_window(monkeypatch, position, operation):
    left = ['OUT Y1'] * 5001
    right = left.copy()
    if operation == 'replace':
        right[position] = 'OUT Y2'
        expected = (['OUT Y1'], ['OUT Y2'])
    elif operation == 'insert':
        right.insert(position, 'OUT Y2')
        expected = ([], ['OUT Y2'])
    else:
        del right[position]
        expected = (['OUT Y1'], [])
    seen = []

    def matcher(junk, a, b, *, autojunk):
        assert junk is None
        assert autojunk is False
        seen.append((a, b))
        return SequenceMatcher(junk, a, b, autojunk=autojunk)

    monkeypatch.setattr('gxworks2_mcp.store.SequenceMatcher', matcher)
    result = diff_programs(parse_il('\n'.join(left)), parse_il('\n'.join(right)))
    assert seen == [expected]
    assert_reconstructs(left, right, result)
    if operation != 'delete':
        assert result['changes'][0]['beforeRange'][0] == position
        assert result['changes'][0]['afterRange'][0] == position


def test_labels_and_multiple_changes_keep_global_ranges():
    before = parse_il('LD X1\nP1:\nOUT Y1\nOUT Y1\nP3:\nEND')
    after = parse_il('LD X1\nP2:\nOUT Y1\nOUT Y2\nP3:\nEND')
    result = diff_programs(before, after)
    assert_reconstructs(
        ['LD X1', 'P1: NOP', 'OUT Y1', 'OUT Y1', 'P3: NOP', 'END'],
        ['LD X1', 'P2: NOP', 'OUT Y1', 'OUT Y2', 'P3: NOP', 'END'], result)
    assert result['changes'][0]['beforeRange'][0] == 1
    assert 'P1: NOP' in result['removed']
    assert 'P2: NOP' in result['added']


def test_all_short_duplicate_sequences_reconstruct_exactly():
    # Exhaustively cover empty sides, prefix/suffix overlap, repeats and reorder.
    sequences = [list(items) for size in range(5)
                 for items in itertools.product(['OUT Y1', 'OUT Y2'], repeat=size)]
    for left in sequences:
        for right in sequences:
            result = diff_programs(parse_il('\n'.join(left)), parse_il('\n'.join(right)))
            assert_reconstructs(left, right, result)
