"""Structure/condition tests only: intentionally NOT an STL scan simulator."""
import copy

import pytest

from gxworks2_mcp.sfc import SFCError, compile_sfc, preview_sfc


@pytest.fixture
def table():
    return {
        "cpu": "FX3U", "initial": "idle", "startup": "restart_initial",
        "reset": {"when": "X007", "mode": "all_off_then_initial"},
        "steps": [
            {"id": "idle", "state": "S0", "actions": [],
             "transition": {"when": {"and": ["X001", {"not": "X002"}]}, "to": "work"}},
            {"id": "work", "state": "S20", "actions": ["Y001"],
             "transition": {"when": "X003", "to": "done"}},
            {"id": "done", "state": "S21", "actions": ["M1"]},
        ],
    }


def test_preview_is_explicitly_not_runnable_and_does_not_mutate(table):
    original = copy.deepcopy(table)
    result = preview_sfc(table)
    assert result["valid"] is True
    assert result["runnable"] is False
    assert result["mode"] == "structure_preview"
    assert not {"il", "csv", "program"} & result.keys()
    assert result["blockingReasons"]
    assert table == original
    result["steps"][0]["outActions"].append("Y002")
    assert table == original


def test_compiler_never_returns_program_even_if_all_conditions_true(table):
    # The table is structurally valid; truth of all input contacts cannot make
    # this preview a proven single-transfer-per-scan or output-exclusive PLC.
    for step in table["steps"]:
        if "transition" in step:
            step["transition"]["when"] = "X001"
    with pytest.raises(SFCError, match="compilation is disabled"):
        compile_sfc(table)


def test_condition_fragment_semantics_only(table):
    fragment = preview_sfc(table)["steps"][0]["transition"]["conditionPreview"]
    assert fragment == ["LD X001", "ANI X002"]
    # Bounded evaluator for just the two Boolean instructions in this example.
    # No claim is made about STL state or physical-output scan timing.
    for start in (False, True):
        for inhibit in (False, True):
            contacts = {"X001": start, "X002": inhibit}
            value = None
            for line in fragment:
                op, device = line.split()
                if op == "LD":
                    value = contacts[device]
                elif op == "ANI":
                    value = value and not contacts[device]
                else:
                    pytest.fail("unsupported test evaluator instruction")
            assert value == (start and not inhibit)


def test_compound_logic_reuses_author(table):
    table["steps"][0]["transition"]["when"] = {
        "or": [{"and": ["X1", "M2"]}, {"and": ["X2", {"not": "M3"}]}]
    }
    assert preview_sfc(table)["steps"][0]["transition"]["conditionPreview"] == [
        "LD X001", "AND M2", "LD X002", "ANI M3", "ORB"
    ]


def test_cycle_is_a_structural_preview_not_scan_execution(table):
    table["steps"][-1]["transition"] = {"when": "X004", "to": "idle"}
    result = preview_sfc(table)
    assert result["steps"][-1]["transition"]["to"] == "idle"
    assert result["runnable"] is False


@pytest.mark.parametrize("mutate,match", [
    (lambda t: t.update(cpu="Q"), "only FX3U"),
    (lambda t: t.pop("startup"), "missing"),
    (lambda t: t.update(startup="resume"), "resume is unsupported"),
    (lambda t: t["reset"].update(mode="resume"), "reset mode"),
    (lambda t: t.update(parallel=True), "unknown fields"),
    (lambda t: t["steps"][0].update(transitions=[]), "unknown fields"),
    (lambda t: t["steps"][0].update(transition=[]), "must be an object"),
    (lambda t: t["steps"][1].update(id="idle"), "duplicate step id"),
    (lambda t: t["steps"][2].update(state="S020"), "duplicate state"),
    (lambda t: t["steps"][1]["transition"].update(to="missing"), "target does not exist"),
    (lambda t: t["steps"][1]["transition"].update(to="work"), "self-transition"),
    (lambda t: t["steps"][1].pop("transition"), "unreachable"),
    (lambda t: t.update(initial="absent", steps=[]), "1..128"),
    (lambda t: t["steps"][0].update(state="S20"), "initial state"),
    (lambda t: t["steps"][1].update(state="S500"), "S20..S499"),
    (lambda t: t["steps"][1].update(actions=["X001"]), "cannot be OUT"),
    (lambda t: t["steps"][1].update(actions=["Y008"]), "octal"),
    (lambda t: t["steps"][1].update(actions=["Y400"]), "octal"),
    (lambda t: t["steps"][1].update(actions=["M8000"]), "special/out-of-range"),
    (lambda t: t["steps"][1].update(actions=["Y1", "Y001"]), "duplicate output"),
    (lambda t: t["steps"][2].update(actions=["Y01"]), "duplicate output"),
    (lambda t: t["steps"][1].update(actions=[{"set": "Y001"}]), "device string"),
    (lambda t: t["reset"].update(when="X001\nOUT Y001"), "only X/Y"),
    (lambda t: t["reset"].update(when="D1"), "only X/Y"),
    (lambda t: t["reset"].update(when={"rise": "X1"}), "level conditions"),
    (lambda t: t["reset"].update(when={"and": []}), "level conditions"),
    (lambda t: t["reset"].update(when={"not": ""}), "only X/Y"),
])
def test_invalid_and_unsupported_tables_fail_closed(table, mutate, match):
    mutate(table)
    with pytest.raises(SFCError, match=match):
        preview_sfc(table)


def test_initial_missing(table):
    table["initial"] = "absent"
    table["steps"][0]["state"] = "S22"
    with pytest.raises(SFCError, match="initial step does not exist"):
        preview_sfc(table)


def test_deep_and_cyclic_conditions_rejected(table):
    cycle = {"and": []}
    cycle["and"].append(cycle)
    table["reset"]["when"] = cycle
    with pytest.raises(SFCError, match="depth/node limit"):
        preview_sfc(table)


def test_condition_budget(table):
    table["reset"]["when"] = {"or": ["X1"] * 513}
    with pytest.raises(SFCError, match="depth/node limit"):
        preview_sfc(table)
