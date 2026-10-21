"""Content-only verification of expected IL against a separately read CSV.

Matching bytes/instructions do not prove compilation, runtime or hardware safety.
No GUI or device access occurs in this module.
"""
from __future__ import annotations

from itertools import zip_longest

from .il import Instruction, Program


def _signature(item: Instruction, compare_comments: bool) -> tuple:
    # Do not guess CPU-specific radix/address aliases. Ignore only display steps,
    # whitespace already normalized by the parser, and identifier case.
    values = (item.mnemonic.upper(), tuple(x.upper() for x in item.operands),
              (item.label or '').upper())
    return values + ((item.comment or '').strip(),) if compare_comments else values


def compare_programs(expected: Program, actual: Program, *, compare_comments: bool = True,
                     limit: int = 50) -> dict:
    """Linear, order-sensitive comparison, retaining duplicates and operand order."""
    if limit < 1 or limit > 500:
        raise ValueError('limit must be between 1 and 500')
    differences = []
    count = 0
    for index, (left, right) in enumerate(zip_longest(expected.instructions, actual.instructions), 1):
        if left is not None and right is not None and _signature(left, compare_comments) == _signature(right, compare_comments):
            continue
        count += 1
        if len(differences) < limit:
            def describe(item):
                if item is None:
                    return None
                return {'instruction': str(item), 'label': item.label, 'comment': item.comment}
            differences.append({'instructionIndex': index, 'expected': describe(left), 'actual': describe(right)})
    return {'matches': count == 0, 'expectedInstructions': len(expected),
            'actualInstructions': len(actual), 'differenceCount': count,
            'differences': differences, 'truncated': count > len(differences),
            'commentsCompared': compare_comments, 'displayStepsCompared': False}
