# Offline reliability checks

This change hardens existing decoding and project-review behavior. It does not
add PLC commands, connect to hardware, or validate a live control program.

## Structured POU decoding

- A network is complete only when its entire token payload is understood.
  A fully consumed network without END is accepted because structured networks
  need not contain a program terminator.
- Unknown instructions, unreadable operands, and truncated network payloads
  return `decoded: false`, a failure location and `partialIl`. They do not return
  editable `il` or append END to a partial result.
- `partialIl` is the contiguous known prefix, not later networks spliced across
  an unknown region. Network coverage is diagnostic, not a safety approval.
- Multi-operand instructions retain separate operands for device inventory.
- Continuous streams must decode in full. An apparent LD/END fragment inside
  an unrecognized wrapper is no longer sufficient to claim success. Unsupported
  wrappers require additional format evidence, not heuristic truncation.

## Project metadata

Reopening a project restores its stored CPU instead of resetting it to Q.
An empty CPU remains the existing unknown-family state used by GXW imports.
Legacy workspaces with no metadata or no CPU field retain the historical Q
default. Invalid JSON, non-object metadata and non-string/whitespace-only CPU
values fail explicitly without rewriting project files.

## Replacement review

Instruction diffs preserve order and duplicate counts. Existing `added`,
`removed`, `before` and `after` fields remain available. The new `changes` list
contains operation names, removed/added lines and zero-based, half-open
`beforeRange` / `afterRange` instruction indexes. Labels affect behavior and
are compared. Comments and displayed step numbers are intentionally excluded.

## Verification

Run `python -m pytest -o addopts='' -q` for offline tests, including
`tests/test_pou_safety.py` and `tests/test_store_integrity.py`.
Run `python tests/test_stdio_e2e.py` for an MCP handshake, local project creation
and local export. This script does not invoke PLC tools.

Tests requiring private Windows GXW samples are skipped when those samples are
absent. Passing synthetic tests does not establish that every GX Works2 version,
CPU family or unrecognized binary format is supported. Real-project acceptance
and any hardware operation remain separate steps.
