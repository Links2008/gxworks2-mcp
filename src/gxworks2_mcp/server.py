"""MCP server: GX Works2 ladder-program authoring, validation, export and PLC I/O.

Tool surface (all tools are read-only on the filesystem except the ones marked WRITE):

Program authoring
  gx_program_create    WRITE  create a named project and seed it with rungs or IL
  gx_program_add_rungs WRITE  append rungs described as a logic tree (compiles to IL)
  gx_program_replace   WRITE  replace the whole program with new IL
  gx_program_read             return the current IL, structured rungs and ASCII ladder
  gx_program_validate         run structural/device checks and report issues
  gx_program_export           emit paste-ready IL, JSON or an ASCII ladder preview
  gx_project_list             list projects in the workspace
  gx_device_usage             every device plus the instructions that touch it

GX Works2 desktop bridge (optional, Windows only; UIA-driven, no mouse needed)
  gx_import_csv        WRITE  verified hand-off: Edit > Read from CSV file via UIA
  gx_compile_csv       WRITE  transform/compile (F4) through the menu
  gx_workspace_status         install, running state and automation availability
  gx_paste_ladder      WRITE  legacy clipboard paste (empirically does nothing on 1.620W)
  gx_save_project      WRITE  Ctrl+S in GX Works2
  gx_open_project             ask GX Works2 to open a .gxw project

Live PLC (MC protocol 3E frame, independent of GX Works2)
  plc_probe                   check whether host:port answers MC protocol
  plc_read                    read bits/words from devices
  plc_write            WRITE  write bits/words to devices
  plc_remote_control   WRITE  remote RUN / STOP / PAUSE / latch clear

The write tools are the only ones with side effects. plc_write refuses unless
allow_write is passed as true, and gx_paste_ladder refuses unless confirm=true.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .analysis import AnalysisError, interlock_summary, trace_device, where_used
from .author import BuildError, compile_rungs
from .csvio import CSVFormatError, read_program_csv, write_program_csv
from .gxw import (
    GxwError,
    device_comments,
    main_res_program,
    open_gxw,
    pou_anchors,
    pou_stream,
)
from .blocks import ladder_blocks, render_ascii, to_boolean
from .devices import DeviceError, describe as describe_device, expand_range, parse_range
from .il import ILParseError, Program, instruction_count, parse_il, render_il
from .mcprotocol import MCClient, MCConfig, MCProtocolError, probe as mc_probe
from .store import Store, StoreError, device_usage, diff_programs
from .compat import SDK_GENERATION, create_server, run_server
from .validate import summarize, validate_program

DEFAULT_WORKSPACE = Path(os.environ.get("GXWORKS2_MCP_HOME", Path.home() / ".gxworks2-mcp"))

_INSTRUCTIONS = (
    "Author MELSEC ladder programs for GX Works2. Verified end-to-end flow: "
    "gx_program_create with rungs -> gx_program_validate -> gx_program_export_csv -> "
    "gx_import_csv (confirm=true; drives GX Works2's Edit > Read from CSV file through UIA, "
    "no mouse or foreground focus needed) -> gx_compile_csv -> gx_save_project. "
    "The CSV import REPLACES the current ladder program. Verify runtime behaviour with "
    "plc_read. gx_paste_ladder is legacy: pasting IL text does nothing on GX Works2 1.620W."
)
server = create_server("gxworks2", _INSTRUCTIONS)
_store: Store | None = None


def store() -> Store:
    """Lazily open the workspace store so the import has no side effects."""

    global _store
    if _store is None:
        _store = Store(DEFAULT_WORKSPACE)
    return _store


# --- program authoring ------------------------------------------------------

@server.tool()
def gx_program_create(
    name: str,
    cpu: str = "Q",
    title: str = "",
    il: str = "",
    rungs: list[dict[str, Any]] | None = None,
    overwrite: bool = False,
) -> str:
    """Create a program project.

    Pass either IL text or a list of rungs. A rung looks like:
      {"out": "Y0", "comment": "start", "logic": {"or": [{"and": ["X0", {"not": "X1"}]}, "M10"]}}
    The logic tree supports and / or, and operands may be a device string or
    {"not": "X1"}, {"rise": "X2"}, {"fall": "X3"}.
    """

    try:
        if rungs:
            program = compile_rungs(rungs)
        elif il:
            program = parse_il(il)
        else:
            program = Program(title=title or name)
    except (BuildError, ILParseError, DeviceError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    try:
        if overwrite:
            try:
                store().delete(name)
            except StoreError:
                pass
        project = store().create(name, cpu=cpu, title=title or name)
    except StoreError as exc:
        return json.dumps({"error": str(exc), "hint": "pass overwrite=true to replace it"}, ensure_ascii=False)
    report = project.save(program)
    return json.dumps(
        {"project": report, "il": render_il(program), "ascii": render_ascii(program)},
        ensure_ascii=False,
        indent=2,
    )


@server.tool()
def gx_program_add_rungs(name: str, rungs: list[dict[str, Any]], replace: bool = False) -> str:
    """Append compiled rungs to a project (or replace the whole program)."""

    try:
        project = store().get(name)
    except StoreError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    program = Program(title=project.name) if replace else project.read_program()
    # Drop a trailing END so new rungs land before it, then re-append exactly one END.
    if program.instructions and program.instructions[-1].mnemonic == "END":
        program.instructions.pop()
    try:
        program.extend(compile_rungs(rungs).instructions[:-1])
    except BuildError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    program.append(parse_il("END").instructions[0])
    report = project.save(program)
    return json.dumps(
        {
            "project": report,
            "added": len(rungs),
            "il": render_il(program),
            "ascii": render_ascii(program),
            "validation": summarize(validate_program(program, cpu=project.cpu)),
        },
        ensure_ascii=False,
        indent=2,
    )


@server.tool()
def gx_program_replace(name: str, il: str) -> str:
    """Replace a project's program with new IL text (validated before writing)."""

    try:
        project = store().get(name)
    except StoreError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    try:
        program = parse_il(il, title=project.name)
    except ILParseError as exc:
        return json.dumps({"error": "IL parse failed: " + str(exc)}, ensure_ascii=False)
    before = project.read_program()
    report = project.save(program)
    return json.dumps(
        {
            "project": report,
            "diff": diff_programs(before, program),
            "ascii": render_ascii(program),
            "validation": summarize(validate_program(program, cpu=project.cpu)),
        },
        ensure_ascii=False,
        indent=2,
    )


@server.tool()
def gx_program_read(name: str, include: str = "il,ascii,logic") -> str:
    """Read a project: IL text, ASCII ladder preview, boolean logic listing and rungs."""

    try:
        project = store().get(name)
    except StoreError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    program = project.read_program()
    wanted = {part.strip() for part in include.split(",") if part.strip()}
    payload: dict[str, Any] = {
        "project": project.name,
        "cpu": project.cpu,
        "instructions": len(program),
        "instructionCount": instruction_count(program),
        "devices": program.devices(),
    }
    if "il" in wanted:
        payload["il"] = render_il(program)
    if "ascii" in wanted:
        payload["ascii"] = render_ascii(program)
    if "logic" in wanted:
        payload["logic"] = to_boolean(program)
    if "rungs" in wanted:
        payload["rungs"] = ladder_blocks(program)
    payload["validation"] = summarize(validate_program(program, cpu=project.cpu))
    return json.dumps(payload, ensure_ascii=False, indent=2)


@server.tool()
def gx_program_validate(name: str, cpu: str = "") -> str:
    """Validate a project and return every issue with severity, code and location."""

    try:
        project = store().get(name)
    except StoreError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    program = project.read_program()
    issues = validate_program(program, cpu=cpu or project.cpu)
    return json.dumps(
        {
            "project": project.name,
            "cpu": cpu or project.cpu,
            "instructions": len(program),
            **summarize(issues),
        },
        ensure_ascii=False,
        indent=2,
    )


@server.tool()
def gx_program_export(name: str, format: str = "il") -> str:
    """Export a program as paste-ready IL, raw JSON, or an ASCII ladder preview."""

    try:
        project = store().get(name)
    except StoreError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    program = project.read_program()
    fmt = format.strip().lower()
    if fmt in {"il", "list", "txt"}:
        return render_il(program)
    if fmt in {"ascii", "ladder"}:
        return render_ascii(program)
    if fmt == "logic":
        return to_boolean(program)
    if fmt == "json":
        return json.dumps(
            {
                "project": project.name,
                "cpu": project.cpu,
                "il": render_il(program),
                "rungs": ladder_blocks(program),
                "devices": program.devices(),
                "validation": summarize(validate_program(program, cpu=project.cpu)),
            },
            ensure_ascii=False,
            indent=2,
        )
    return "unknown format " + repr(format) + "; use il, ascii, logic or json"


@server.tool()
def gx_program_import_csv(name: str, path: str) -> str:
    """Load a GX Works2-exported program CSV into the MCP store (CSV -> IL).

    GX Works2 exports the CSV from the project tree (Write to CSV File), and its Edit
    menu's "Read from CSV file" accepts the same 7-column shape - verified live on
    1.620W. This tool reads such a CSV into a store project so you can inspect,
    validate and re-export it. To push a CSV INTO GX Works2 itself, use gx_import_csv.
    """

    try:
        project = store().get(name)
    except StoreError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    try:
        parsed = read_program_csv(path)
    except (CSVFormatError, OSError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    try:
        program = parsed.to_program(title=project.name)
    except Exception as exc:  # ILParseError or DeviceError from the converted text
        return json.dumps({"error": "CSV rows did not convert to IL: " + str(exc)}, ensure_ascii=False)
    report = project.save(program)
    return json.dumps(
        {
            "project": report,
            "rows": len(parsed.rows),
            "banner": parsed.banner,
            "il": render_il(program),
            "validation": summarize(validate_program(program, cpu=project.cpu)),
        },
        ensure_ascii=False,
        indent=2,
    )


@server.tool()
def gx_program_export_csv(
    name: str,
    path: str = "",
    locale: str = "zh",
    cpu_label: str = "QCPU (Q mode) Q03UDV",
) -> str:
    """Write the project as a GX Works2 program CSV (UTF-16 LE, tab, seven quoted columns).

    The output matches the template the verified Edit > Read from CSV file import accepts
    (banner lines, Chinese or Japanese headers, one instruction per row). Pair this with
    gx_import_csv for the full automated hand-off into a running GX Works2.
    """

    try:
        project = store().get(name)
    except StoreError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    program = project.read_program()
    target = path or str(project.root / (project.name + ".csv"))
    try:
        written = write_program_csv(
            program, target, project_name=project.name, cpu_label=cpu_label, locale=locale
        )
    except OSError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps(
        {
            "written": str(written),
            "rows": len(program) + 2,
            "encoding": "utf-16-le",
            "locale": locale,
            "note": "this file matches the template GX Works2 imports via Edit > Read from "
            "CSV file; pass its path to gx_import_csv for the automated hand-off",
        },
        ensure_ascii=False,
        indent=2,
    )


@server.tool()
def gx_project_list() -> str:
    """List program projects in the workspace with sizes and modification times."""

    return json.dumps({"workspace": str(store().workspace), "projects": store().list()}, ensure_ascii=False, indent=2)


@server.tool()
def gx_device_usage(name: str) -> str:
    """List every device in a project with the instructions that reference it."""

    try:
        project = store().get(name)
    except StoreError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps(device_usage(project.read_program()), ensure_ascii=False, indent=2)


@server.tool()
def gx_device_describe(device: str, count: int = 1) -> str:
    """Explain a device address: family, index, bit, MC-protocol code and I/O kind."""

    try:
        info = describe_device(device)
        if count > 1:
            rng = parse_range(device + " " + str(count))
            info["range"] = str(rng)
            info["points"] = [str(ref) for ref in expand_range(rng)[:64]]
        return json.dumps(info, ensure_ascii=False, indent=2)
    except DeviceError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


# --- .gxw direct reading (no GX Works2 process needed) ----------------------

def _safe_name(text: str) -> str:
    return "".join(ch if (ch.isalnum() or ch in ".()-") else "_" for ch in text)


@server.tool()
def gx_gxw_inventory(path: str) -> str:
    """Read a .gxw without GX Works2: identity, inventory, per-program decode status.

    .gxw is an OLE2 container whose _hdb stream nests a second OLE2; the outer
    projectdatalist.xml diffgram maps every data file (iID == inner stream id):
    MAIN.res program bodies, POU .pou bodies, task bindings, COMMENT.qcd,
    parameters and libraries. Q-family MAIN.res bodies are decoded to IL with the
    grammar proven in re-evidence/GRAMMAR.md (verified byte-exact on a known
    round trip); anything unverified stops with a stopReason instead of guessing.
    FX/other families still report framing only.
    """

    try:
        project = open_gxw(path)
    except GxwError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    payload = project.summary()
    structure = project.program_structure()
    for entry in structure:
        if entry.get("available") and entry.get("name", "").lower().endswith((".res", ".pou")):
            body = project.read_stream(project.find(entry["name"]))
            try:
                if entry["name"].lower().endswith(".pou"):
                    decoded = pou_stream(body)
                else:
                    decoded = main_res_program(body)
            except GxwError as exc:
                decoded = {"decoded": False, "error": str(exc)}
            entry["decode"] = {
                k: decoded[k]
                for k in ("decoded", "coverage", "il", "stopReason", "edgeFidelityContacts")
                if k in decoded
            }
            if decoded.get("decoded"):
                payload["programBodiesDecoded"] = payload.get("programBodiesDecoded", 0) + 1
    payload["programStructure"] = structure
    project.close()
    return json.dumps(payload, ensure_ascii=False, indent=2)


@server.tool()
def gx_gxw_open(path: str, import_into: str = "", overwrite: bool = False) -> str:
    """Deep-read a .gxw, optionally materialize it as an editable store project.

    Extracts container metadata, label-table comments, per-program anchors and raw
    bodies (program_<name>.raw.bin + gxw_manifest.json). When a program body decodes
    under the proven grammar, its IL is written to the project's program.il, so the
    round trip continues: edit with gx_program_replace / gx_program_add_rungs, push
    back with gx_import_csv. Bodies that do not decode stay raw with a stopReason;
    nothing is ever guessed.
    """

    try:
        project = open_gxw(path)
    except GxwError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    summary = project.summary()
    summary["programStructure"] = project.program_structure()
    summary["deviceComments"] = device_comments(project)
    blobs: dict[str, int] = {}
    target = import_into or (Path(path).stem + "-gxw")
    try:
        if import_into:
            if overwrite:
                try:
                    store().delete(import_into)
                except StoreError:
                    pass
            store().create(import_into, cpu="", title=project.name)
        base = store().get(target).root
        (base / "gxw_manifest.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        decoded_any = False
        for entry in project.programs():
            try:
                payload = project.read_stream(entry)
            except GxwError:
                continue
            (base / ("program_" + _safe_name(entry.name) + ".raw.bin")).write_bytes(payload)
            blobs[entry.name] = len(payload)
            if entry.name.endswith(".res"):
                attempt = main_res_program(payload)
                summary["decode"] = {k: attempt[k] for k in ("decoded", "coverage", "il", "stopReason") if k in attempt}
                if attempt.get("decoded"):
                    from .il import parse_il, render_il as _render

                    store_project = store().get(target)
                    store_project.save(parse_il(attempt["il"], title=project.name))
                    decoded_any = True
                    summary["programBodiesDecoded"] = 1
        if decoded_any:
            summary["editable"] = True
            summary["editableNote"] = "program.il holds the decoded IL: edit with gx_program_add_rungs / "
            summary["editableNote"] += "gx_program_replace, then push back with gx_import_csv"
        else:
            summary["editable"] = False
        summary["storeProject"] = target
        summary["rawBodies"] = blobs
    except (StoreError, OSError) as exc:
        summary["importError"] = str(exc)
    project.close()
    return json.dumps(summary, ensure_ascii=False, indent=2)


@server.tool()
def gx_gxw_comments(path: str) -> str:
    """Extract whatever device comments a .gxw exposes without GX Works2.

    label.xml rows decode today; COMMENT.qcd is a passworded Access database (the
    ACE engine rejects an empty password), so coverage is reported honestly.
    """

    try:
        project = open_gxw(path)
    except GxwError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    comments = device_comments(project)
    qcd = project.find("COMMENT.qcd")
    result = {
        "project": project.name,
        "comments": comments,
        "count": len(comments),
        "qcdAvailable": qcd is not None,
        "qcdBytes": qcd.size if qcd else None,
        "note": "label-table comments only; Jet-backed COMMENT.qcd pending the REA task",
    }
    project.close()
    return json.dumps(result, ensure_ascii=False, indent=2)


# --- program analysis (design reference: purinzan/gx3-cli-mcp) ---------------

@server.tool()
def gx_where_used(name: str, device: str = "") -> str:
    """Cross-reference: every write and read of each device (or one device).

    The GX-works answer to 'where is M100 used' - OUT/SET/RST/PLS/PLF and
    application-instruction destinations count as writes, contacts and sources
    as reads. Check this before changing interlocks.
    """

    try:
        project = store().get(name)
    except StoreError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    program = project.read_program()
    usage = where_used(program)
    if device:
        try:
            from .devices import parse_device

            key = str(parse_device(device))
        except DeviceError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps({"device": key, "sites": usage.get(key, [])}, ensure_ascii=False, indent=2)
    return json.dumps({"project": name, "usage": usage}, ensure_ascii=False, indent=2)


@server.tool()
def gx_trace_device(name: str, device: str, max_depth: int = 4) -> str:
    """Why will this coil turn on? Trace its logic tree to boundary inputs.

    Expands every writable contact in a rung to its own writer rung,
    recursively, until reaching X/SM/T-C contacts (external boundary) or a
    cycle/depth stop, which is reported explicitly. Read-only analysis over
    the stored project (works for programs decoded from .gxw or written here).
    """

    try:
        project = store().get(name)
    except StoreError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    try:
        tree = trace_device(project.read_program(), device, max_depth=max_depth)
    except AnalysisError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps(tree, ensure_ascii=False, indent=2)


@server.tool()
def gx_lint_program(name: str) -> str:
    """Static review: multi-writer coils and write-only devices.

    The two classics that bite PLC programmers: a coil driven from several
    rungs (only the last OUT wins per scan), and devices written but never
    read (dead logic). Structural checks still live in gx_program_validate.
    """

    try:
        project = store().get(name)
    except StoreError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    summary = interlock_summary(project.read_program())
    summary["project"] = name
    return json.dumps(summary, ensure_ascii=False, indent=2)

# --- GX Works2 desktop bridge ----------------------------------------------

@server.tool()
def gx_workspace_status() -> str:
    """Report whether GX Works2 is installed/running and whether UI automation is available."""

    from . import ui

    report: dict[str, Any] = {"workspace": str(store().workspace)}
    report["process"] = ui.gxworks2_running()
    report["automation"] = ui.available()
    return json.dumps(report, ensure_ascii=False, indent=2)


@server.tool()
def gx_import_csv(
    csv_path: str = "",
    project: str = "",
    *,
    title_hint: str = "",
    confirm: bool = False,
) -> str:
    """Import a program CSV into the running GX Works2 via its verified UIA seam (WRITE).

    This is the direct-write path: it drives Edit > Read from CSV file (从CSV文件读取)
    through UIA expand()/invoke(), which a live 1.620W test proved works WITHOUT mouse
    input or foreground focus. The import REPLACES the whole current ladder program, so
    confirm=true is required; the window-title step count before/after is returned as
    evidence the replacement took effect.

    Pass csv_path (a file written by gx_program_export_csv), or project to export that
    stored project to a temp CSV first and import it. After import, run gx_compile_csv
    (or F4 by hand) and gx_save_project, then verify logic against the PLC with plc_read.
    """

    from . import ui

    if not confirm:
        return json.dumps(
            {
                "error": "refusing to import without confirm=true",
                "reason": "GX Works2 discards the current ladder editor content when reading "
                "a CSV; confirm you have saved or exported the live program first",
            },
            ensure_ascii=False,
        )
    target = csv_path
    if not target and project:
        try:
            store_project = store().get(project)
        except StoreError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        program = store_project.read_program()
        target = str(store_project.root / (store_project.name + ".import.csv"))
        try:
            write_program_csv(program, target, project_name=store_project.name)
        except OSError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
    if not target:
        return json.dumps({"error": "pass csv_path or project"}, ensure_ascii=False)
    try:
        result = ui.import_program_csv(target, title_hint=title_hint or None, confirm=True)
    except ui.AutomationError as exc:
        return json.dumps({"error": str(exc), "csv": target}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False, indent=2)


@server.tool()
def gx_compile_csv(*, title_hint: str = "") -> str:
    """Trigger GX Works2's ladder transform/compile through UIA after a CSV import (WRITE).

    The import dialog itself says to run a program check after reading; this invokes the
    Transform/Compile menu entry (转换/编译, F4) so the ladder is actually built and any
    instruction errors surface before saving or transferring.
    """

    from . import ui

    try:
        return json.dumps(ui.compile_program(title_hint=title_hint or None), ensure_ascii=False, indent=2)
    except ui.AutomationError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


@server.tool()
def gx_paste_ladder(name: str, title_hint: str = "", confirm: bool = False) -> str:
    """EXPERIMENTAL: paste the project's IL into the focused GX Works2 ladder editor (WRITE).

    Mitsubishi documents no "paste an instruction list and get a ladder" feature, and GX
    Works2's supported languages do not include IL, so this may do nothing depending on the
    version and focus. Prefer gx_program_export: the exported IL/CSV path is the one the
    documents support.
    """

    from . import ui

    if not confirm:
        return json.dumps(
            {
                "error": "refusing to paste without confirm=true",
                "reason": "this types the IL listing into the active GX Works2 ladder window and "
                "rebuilds the ladder there; it cannot be undone by this server",
            },
            ensure_ascii=False,
        )
    try:
        project = store().get(name)
    except StoreError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    il_text = render_il(project.read_program())
    try:
        result = ui.paste_il(il_text, title_hint=title_hint or None, confirm=True)
    except ui.AutomationError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps(result.as_dict(), ensure_ascii=False, indent=2)


@server.tool()
def gx_save_project(title_hint: str = "") -> str:
    """Send Ctrl+S to GX Works2 (WRITE)."""

    from . import ui

    try:
        return json.dumps(ui.save_project(title_hint=title_hint or None), ensure_ascii=False, indent=2)
    except ui.AutomationError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


@server.tool()
def gx_open_project(path: str) -> str:
    """Ask the running GX Works2 instance to open a project file (WRITE)."""

    from . import ui

    try:
        return json.dumps(ui.open_project(path), ensure_ascii=False, indent=2)
    except ui.AutomationError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


# --- live PLC over MC protocol ---------------------------------------------

def _client(host: str, port: int, timeout: float, transport: str) -> MCClient:
    config = MCConfig(host=host, port=int(port), timeout=float(timeout), transport=transport)  # type: ignore[arg-type]
    return MCClient(config)


@server.tool()
def plc_probe(host: str = "127.0.0.1", port: int = 5000, timeout: float = 2.0, transport: str = "tcp") -> str:
    """Check whether an MC-protocol endpoint answers (GX Simulator2, or a real PLC)."""

    return json.dumps(mc_probe(host, port, timeout, transport), ensure_ascii=False, indent=2)  # type: ignore[arg-type]


@server.tool()
def plc_read(
    devices: str,
    host: str = "127.0.0.1",
    port: int = 5000,
    timeout: float = 3.0,
    transport: str = "tcp",
) -> str:
    """Read one or more device ranges, e.g. "D100-D109, M0-M15, X0".

    Word devices come back as 16-bit integers; bit devices as 0/1. Comma-separated
    ranges are read in one connection.
    """

    results: dict[str, Any] = {}
    try:
        with _client(host, port, timeout, transport) as client:
            for spec in [part.strip() for part in devices.split(",") if part.strip()]:
                rng = parse_range(spec)
                values = client.read_typed(str(rng.start), rng.count)
                results[spec] = {
                    "start": str(rng.start),
                    "count": rng.count,
                    "values": values,
                    "hex": ["0x" + format(value, "04X") for value in values] if rng.start.kind == "word" else values,
                }
    except (MCProtocolError, DeviceError) as exc:
        return json.dumps({"error": str(exc), "partial": results}, ensure_ascii=False, indent=2)
    return json.dumps({"host": host, "port": port, "transport": transport, "reads": results}, ensure_ascii=False, indent=2)


@server.tool()
def plc_write(
    device: str,
    values: list[int],
    host: str = "127.0.0.1",
    port: int = 5000,
    timeout: float = 3.0,
    transport: str = "tcp",
    allow_write: bool = False,
) -> str:
    """Write values to devices on a live PLC (WRITE; requires allow_write=true)."""

    if not allow_write:
        return json.dumps(
            {
                "error": "refusing to write without allow_write=true",
                "reason": "this writes to a live PLC; a wrong address can move a machine. "
                "Re-issue the call with allow_write=true once the device list is reviewed.",
                "device": device,
                "values": values,
            },
            ensure_ascii=False,
            indent=2,
        )
    try:
        with _client(host, port, timeout, transport) as client:
            from .devices import parse_device

            ref = parse_device(device)
            if ref.is_read_only:
                return json.dumps(
                    {
                        "error": "device " + str(ref) + " is read-only over MC protocol",
                        "reason": "the reference manual marks the S (step relay) as read only "
                        "for external access; writes are rejected instead of failing on the PLC",
                    },
                    ensure_ascii=False,
                )
            if ref.kind == "bit" or ref.bit is not None:
                written = client.write_bits(str(ref), values)
                kind = "bit"
            else:
                written = client.write_words(str(ref), values)
                kind = "word"
    except (MCProtocolError, DeviceError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2)
    return json.dumps(
        {"ok": True, "host": host, "port": port, "device": device, "kind": kind, "written": written},
        ensure_ascii=False,
        indent=2,
    )


@server.tool()
def plc_remote_control(
    action: str = "run",
    host: str = "127.0.0.1",
    port: int = 5000,
    timeout: float = 3.0,
    transport: str = "tcp",
) -> str:
    """Remote RUN / STOP / PAUSE / LATCH_CLEAR on a live PLC (WRITE)."""

    normalized = action.strip().lower()
    if normalized not in {"run", "stop", "pause", "latch_clear"}:
        return json.dumps({"error": "action must be run, stop, pause or latch_clear"}, ensure_ascii=False)
    try:
        with _client(host, port, timeout, transport) as client:
            result = client.remote(normalized)  # type: ignore[arg-type]
            # A remote RUN reports normal completion even when the CPU's RUN/STOP switch is
            # in STOP, so read the operation-status relay back before claiming success.
            if normalized in {"run", "stop"}:
                try:
                    result["verified"] = client.cpu_run_state()
                except MCProtocolError as exc:
                    result["verified"] = {"error": "could not read SM0 back: " + str(exc)}
            return json.dumps(result, ensure_ascii=False, indent=2)
    except MCProtocolError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2)


def main() -> None:
    """Entry point for the console script and 'python -m gxworks2_mcp'."""

    transport = os.environ.get("GXWORKS2_MCP_TRANSPORT", "stdio")
    port = int(os.environ.get("GXWORKS2_MCP_PORT", "8931"))
    run_server(server, transport=transport, port=port)


if __name__ == "__main__":
    main()
