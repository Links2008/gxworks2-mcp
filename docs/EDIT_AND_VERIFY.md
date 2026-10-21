# Local edits, SFC preview and content verification

## Versioned local edits

1. `gx_program_slice(name, start=0, limit=50)` returns a bounded slice, a version,
   and stable instruction IDs. `nextStart` fetches further context.
2. `gx_program_patch(name, expected_version, operations)` applies a batch. Each
   operation names `target_id` and `op`: `insert_before`, `insert_after`,
   `replace`, or `delete`. All except delete include an `il` fragment.
3. The complete resulting program is statically validated. Version, parse and
   validation errors do not change it. Successful results include a local diff,
   added IDs and the new version. CSV is refreshed by default; failure is reported
   separately from whether the patch was applied. Never import after doNotImport.

These are instruction IDs, not guessed rung numbers. IDs survive supported
patches; other whole-program rewrites invalidate them. Untouched original text,
comments, CPU and custom metadata are preserved. On a version conflict, re-read
and reconsider the edit rather than retrying it blindly.

Advisory locks cover cooperating patch writers and Store save/delete operations.
Old whole-program tools still do not perform a stale-version check; use patches
for version-aware collaboration. External editors are not locked. Multi-file
commit exceptions attempt rollback and report rollbackComplete; a process crash
is not guaranteed atomic. Windows lock code uses msvcrt, but this change was
executed only on Linux, not independently validated on Windows.

## SFC step-table preview

`gx_sfc_preview(table)` accepts a restricted FX3U single-step state table. Example:

```json
{
  "cpu": "FX3U", "initial": "idle", "startup": "restart_initial",
  "reset": {"when": "X007", "mode": "all_off_then_initial"},
  "steps": [
    {"id": "idle", "state": "S0", "actions": [],
     "transition": {"when": "X001", "to": "work"}},
    {"id": "work", "state": "S20", "actions": ["Y001"]}
  ]
}
```

The tool checks targets, reachability, duplicate steps/outputs, address aliases,
supported actions and condition expressions. Unknown constructs are rejected.
`valid=true` means table structure only: `runnable=false` is always returned.
There is no downloadable STL, CSV, native graphical SFC, or scan simulator here.

Why compilation remains blocked: Mitsubishi JY997D16601R §35.2.2 (printed p786)
describes transition overlap for one operation cycle; §35.2.4 (p789) distinguishes
SET sequential transitions from OUT jump/repeat transitions. Initialization and
RET behavior are described in §35.2.5 (p791). An abstract single-active-step graph
does not establish mutually exclusive physical outputs during a PLC scan.

[Official FX3U/FX3UC programming manual](https://dl.mitsubishielectric.com/dl/fa/document/manual/plc_fx/jy997d16601/jy997d16601r.pdf)
also confirms FOR S has one operand (§8.9, p241) and NEXT none (§8.10, p242).

## Readback verification

`gx_verify_import(name)` exports the currently open GX Works2 program to a unique,
previously nonexistent temporary CSV, then compares it with the local project.
It checks ordered instructions, every operand, labels and (by default) comments,
ignoring display step numbering and identifier case. It does not guess CPU radix
aliases. A changed expected project invalidates the result.

`gx_verify_import(name, readback_path=...)` performs an offline file comparison.
Even a match returns `verified=false`, `liveReadback=false`: provenance and
freshness of a supplied CSV are unknown. A fresh UI export match can set verified
true, but compiled and hardwareValidated remain false. The existing import step
count result is only a legacy heuristic, not proof of matching content.

The UI orchestration was tested with mocks plus actual MCP stdio file comparison;
no live Windows GX Works2 readback was performed in this change. Whole-block
import still requires confirmation. No PLC read/write or RUN/STOP was executed.

## CSV integrity

Additional operands use device-only continuation rows, not the PI statement
column. Evidence: [original GX Works2 export](https://github.com/ito-soft-design/ladder_converter/blob/master/test/files/src/MAIN.csv)
The observed source file is
`test/files/src/MAIN.csv` in ito-soft-design/ladder_converter, with blob SHA
f3957ff53867d9e265f60bbb6b01260e250cbb82. MOV at step72 and ADD at step77 show
device-only continuations. Nonempty PI statements, ambiguous rows, unsupported
IL labels and unknown columns are rejected rather than silently discarded.
Older MCP CSVs that put extra operands in PI must be regenerated from the IL.
