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
  plc_list_ports              passive COM inventory; does not open serial ports
  plc_find_ports              read-only CPU-model probe of TCP 5000/5555/5556
  plc_probe                   check whether one host:port answers MC protocol
  plc_read                    read bits/words from devices
  plc_write            WRITE  write bits/words to devices
  plc_remote_control   WRITE  remote RUN / STOP / PAUSE / latch clear

The write tools are the only ones with side effects. plc_write asks 「现在写入吗？」
and refuses unless the user has agreed and both confirm and allow_write are true.
gx_paste_ladder refuses unless confirm=true.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import hashlib
from contextlib import contextmanager
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
from .store import Project, Store, StoreError, device_usage, diff_programs
from .compat import SDK_GENERATION, create_server, run_server
from .validate import _cpu_limits, summarize, validate_program
from .verification import compare_programs
from .locking import project_lock
from .patching import PatchError, PatchWriteError, VersionConflict, apply_patch, read_patch_view
from .sfc import SFCError, preview_sfc, compile_stl, describe_sfc

DEFAULT_WORKSPACE = Path(os.environ.get("GXWORKS2_MCP_HOME", Path.home() / ".gxworks2-mcp"))

_INSTRUCTIONS = (
    "调试交付用gx_handover_export(name)新建资料ZIP；只归档本会话匹配版本证据。"
    "交接前用gx_handover_verify(path,name可选)只读核验资料及工作区版本；完整性不等于来源或现场验收认证。"
    "需要归档仿真验证时给gx_simulator_verify传project_name；不代表已认证模拟器代码，缺项与现场签字不能编造。"
    "比较修改前后影响用gx_change_impact(before_name,after_name)，只读两个已存程序；"
    "报告为静态候选范围，复测建议未执行，不据此认定可下载或外部映射完整。"
    "排查Y/M输出不动作时用gx_diagnose_output：传name/device，可提供snapshot；"
    "未传snapshot只读现有FX模拟器，不启动、停止或写入。支持16/32位整数比较，D快照用原始16位字。"
    "先核对程序版本，遵守未知/锁存/多写入等限制。"
    "规划软元件、审查模式/报警或设计工艺测试时，用 gx_skill_list 查看三个工程技能，"
    "再按需 gx_skill_read(name) 读取匹配技能；普通小修改无需全部加载。"
    "结构化数据回路使用 instruction 与 operands，例如 MOV 的 operands=['K10','D0']；"
    "比较条件使用 compare={op:'>=',left:'D0',right:'K10',bits:16}，bits可为16或32。"
    "仿真 read 支持D、C触点和CN当前值；types按地址指定int16/uint16/int32/uint32。"
    "verify支持hold_ms持续保持、min_delay_ms最早响应及全批forbid禁止组合；结论只覆盖采样观察。"
    "连续按键需要按工艺确认程序接收松开，或验证足够的松开保持区间。"
    "真实本地仿真使用 gx_simulator_*：start/status/control/stop、read、inputs、press、verify。"
    "这些工具只访问已核对版本的FX Simulator2，X/Y按八进制，T为触点、TN为当前值；"
    "只向虚拟X输入写0/1，不允许Y/M/S输出覆写。先确认状态，再批量给输入并读实际输出；"
    "不要用plc_probe/plc_find_ports/plc_write连接GX Simulator2，也不要用离线扫描模型冒充真实仿真。"
    "plc_find_ports 只登记串口并只读探测 TCP 5000、5555、5556；没有唯一 CPU 型号就不代选端口。"
    "plc_write 写入前必须把主机、端口、软元件和数值告诉用户，并询问「现在写入吗？」。"
    "只有用户明确同意后，才可同时传 confirm=true 与 allow_write=true；不得自行确认。"
    "FX3U 原生 SFC 使用 gx_sfc_write：一次提交完整 spec，原生编译、检查、保存和读回；"
    "读取已有SFC内部程序优先用gx_sfc_read；gx_gxw_open也按源码格式返回步骤、动作和条件。"
    "topologyVerified=false时保留diagnostics中的多目标或未解析引用，不代表源码为空或可安全重建。"
    "这些读取不启动原生进程，不连接PLC。"
    "简单梯形图可用 gx_write_project 写出源码，仍需另行编译。"
    "Author MELSEC ladder programs for GX Works2. Prefer gx_program_prepare: send ALL "
    "rungs in one batch, or complete IL (including FX STL/RET sequences), to validate, "
    "save locally and export CSV in ONE call with a compact response. Do not append "
    "one rung per tool call. This does not automate the native graphical SFC editor. "
    "Only when the user requests the optional CSV/UI bridge, gx_import_csv requires explicit replacement confirmation, followed by "
    "gx_compile_csv, gx_verify_import for fresh content readback, and gx_save_project. "
    "For local edits use gx_program_slice then gx_program_patch with the returned version. "
    "gx_sfc_preview is structure-only and never emits runnable SFC. Legacy flow: "
    "gx_program_create with rungs -> gx_program_validate -> gx_program_export_csv -> "
    "gx_import_csv (confirm=true; drives GX Works2's Edit > Read from CSV file through UIA, "
    "no mouse or foreground focus needed) -> gx_compile_csv -> gx_save_project. "
    "The CSV import REPLACES the current ladder program. Offline validation does not "
    "prove hardware safety. Do not connect to live PLCs without authorization. "
    "gx_paste_ladder is legacy: pasting IL text does nothing on GX Works2 1.620W."
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
def gx_skill_list() -> str:
    """只读列出三菱工程技能名称和适用任务；不加载正文、不操作工程或PLC。"""
    from .skill_library import list_skills
    return json.dumps(dict(ok=True,skills=list_skills()),ensure_ascii=False)


@server.tool()
def gx_skill_read(name: str) -> str:
    """只读一个已登记工程技能；使用gx_skill_list返回的name，不接受文件路径。"""
    from .skill_library import read_skill
    try:return json.dumps(dict(ok=True,**read_skill(name)),ensure_ascii=False)
    except (ValueError,OSError) as exc:return json.dumps(dict(ok=False,error=str(exc)),ensure_ascii=False)

def _sim_result(operation, *args):
    """模拟器工具统一返回失败原因，不回退到实体 PLC 接口。"""
    try:
        return json.dumps(operation(*args), ensure_ascii=False)
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        return json.dumps(dict(ok=False,error=str(exc),physicalPlc=False),ensure_ascii=False)


@server.tool()
def gx_simulator_status() -> str:
    """查询真实 GX Simulator2 虚拟 CPU、已核验版本和 RUN/STOP 开关状态。"""
    from .simulator_ui import status
    return _sim_result(status)


@server.tool()
def gx_simulator_start(confirm: bool = False) -> str:
    """启动当前可见 GXW 工程的本地仿真，并确认传输完成和监视视图；不连接实体 PLC。"""
    if not confirm:return json.dumps(dict(ok=False,error='启动仿真需要confirm=true',physicalPlc=False))
    from .simulator_ui import start
    return _sim_result(start)


@server.tool()
def gx_simulator_stop(confirm: bool = False) -> str:
    """通过 GX Works2 正常结束当前仿真，不强杀进程。"""
    if not confirm:return json.dumps(dict(ok=False,error='结束仿真需要confirm=true',physicalPlc=False))
    from .simulator_ui import stop
    return _sim_result(stop)


@server.tool()
def gx_simulator_control(action: str, confirm: bool = False) -> str:
    """操作虚拟 CPU 开关：run 或 stop，并核对面板状态。"""
    if not confirm:return json.dumps(dict(ok=False,error='切换虚拟CPU需要confirm=true',physicalPlc=False))
    from .simulator_ui import cpu_control
    return _sim_result(cpu_control,action)


@server.tool()
def gx_simulator_read(devices: list[str], types: dict[str, str] | None = None) -> str:
    """读取 X/Y/M/S/T/TN/C/CN/D；types 可选 uint16/int16/uint32/int32，32位D读取连续两字。"""
    from .simulator import read_devices
    return _sim_result(read_devices,devices,types)


@server.tool()
def gx_simulator_inputs(values: dict[str, int], confirm: bool = False) -> str:
    """批量模拟按键和传感器：只允许向虚拟 X 输入写0/1，保留其他输入并读回。"""
    if not confirm:return json.dumps(dict(ok=False,error='修改虚拟输入需要confirm=true',physicalPlc=False))
    from .simulator import write_inputs
    return _sim_result(write_inputs,values)


@server.tool()
def gx_simulator_press(device: str, duration_ms: int = 100, confirm: bool = False) -> str:
    """模拟按下并松开一个虚拟 X 按钮，20..2000毫秒，异常时也尝试释放同一实例。"""
    if not confirm:return json.dumps(dict(ok=False,error='模拟按键需要confirm=true',physicalPlc=False))
    from .simulator import pulse
    return _sim_result(pulse,device,duration_ms)


@server.tool()
def gx_simulator_verify(steps: list[dict[str, Any]], confirm: bool = False,
                        forbid: list[dict[str, int]] | None = None, sample_ms: int = 20,
                        project_name: str = "") -> str:
    """批量验证时间窗与禁止组合，失败即停；可选project_name将实际结果绑定到工作区版本供归档。"""
    if not confirm:return json.dumps(dict(ok=False,error='执行仿真动作验证需要confirm=true',physicalPlc=False))
    from .simulator import verify
    if not project_name:return _sim_result(verify,steps,forbid,sample_ms)
    from .handover import program_hash,remember_evidence
    try:
        project=store().get(project_name)
        with project_lock(project):
            program=project.read_program()
            if not len(program):raise StoreError('程序源文件为空，不能绑定仿真验证版本')
            version=program_hash(program)
    except (StoreError,OSError,ValueError) as exc:
        return json.dumps(dict(ok=False,error=str(exc),physicalPlc=False),ensure_ascii=False)
    result=json.loads(_sim_result(verify,steps,forbid,sample_ms))
    try:
        with project_lock(project):stable=version==program_hash(project.read_program())
    except (OSError,ValueError,TimeoutError) as exc:
        result.update(archiveEvidenceId=None,archiveWarning='验证后无法核对版本：'+str(exc))
        return json.dumps(result,ensure_ascii=False)
    result['archiveEvidenceId']=remember_evidence(project,version,'simulator_verify',dict(
        result=result,workspaceStable=stable,programMatchVerified=False))
    return json.dumps(result,ensure_ascii=False)

@contextmanager
def _temporary_workspace(prefix: str, directory: Path):
    """Do not turn a completed write into a failure because staging cleanup failed."""
    temporary = tempfile.TemporaryDirectory(prefix=prefix, dir=directory)
    warnings: list[str] = []
    try:
        yield Path(temporary.name), warnings
    finally:
        try:
            temporary.cleanup()
        except OSError as exc:
            warnings.append(str(exc))

@server.tool()
def gx_sfc_preview(table: dict[str, Any]) -> str:
    """Validate a single-active-step FX3U state table and preview its structure.

    This is NOT a runnable SFC/STL compiler. Returns runnable=false even when
    structure is valid because scan-overlap, initialization and reset semantics
    still need dedicated verification. No files, UI or PLC operations occur.
    Table fields: cpu, initial, startup, reset, steps. Each step has id, state,
    actions, and optionally transition={when: contact logic, to: step id}.
    """
    try:
        return json.dumps(preview_sfc(table), ensure_ascii=False)
    except SFCError as exc:
        return json.dumps({"valid": False, "runnable": False, "error": str(exc)}, ensure_ascii=False)


@server.tool()
def gx_sfc_write(spec: dict[str, Any], path: str, template_path: str,
                 installation_root: str = "", confirm: bool = False) -> str:
    """写出并原生编译 FX3U SFC 图形工程，适配 GX Works2 1.620.0.1。

    一次提交 initial、preamble 和 steps=[{state,actions,transitions:[{to,when}]}]。
    支持顺序及双路选择分支；actions 为梯形图回路，可含定时器 preset。
    模板须包含 Block 图形和 Block1 连续扫描块；仅另存新文件。
    完成原生图形写入、完整程序检查、保存及磁盘读回才返回 ok=true。
    不调用 computer use；最多一个原生子进程，用完退出；不连接 PLC。
    installation_root 可省略以读取本机安装信息，或传包含 Easysocket 的根目录。
    """
    if not confirm:
        return json.dumps({"ok": False, "error": "请传 confirm=true，另存原生 SFC 新工程"}, ensure_ascii=False)
    from .native_sfc import write_sfc_gxw
    try:
        installation = installation_root or os.environ.get("GXWORKS2_INSTALL_ROOT", "")
        if not installation:
            from .ui import find_install
            found = find_install()
            if not found.get("found"):
                raise GxwError("未找到 GX Works2，请指定 installation_root")
            installation = str(Path(found["executable"]).parent.parent)
        return json.dumps(write_sfc_gxw(template_path, path, spec, installation), ensure_ascii=False)
    except (BuildError, GxwError, OSError, ValueError, TypeError, TimeoutError) as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


@server.tool()
def gx_sfc_read(path: str) -> str:
    """离线读取 FX3U 原生 SFC 步骤、动作和条件；无进程、无 UI、无 PLC 通信。

    返回每个程序块的步骤引用、S 状态号、动作指令和条件跳转关系。
    未知图形指令或不完整的动作注册均返回错误，不拼凑可编辑的完整程序。
    """
    from .native_sfc import read_sfc_gxw
    try:
        return json.dumps(read_sfc_gxw(path), ensure_ascii=False)
    except (GxwError, OSError, ValueError) as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


@server.tool()
def gx_program_prepare(
    name: str,
    cpu: str,
    il: str = "",
    rungs: list[dict[str, Any]] | None = None,
    overwrite: bool = False,
    locale: str = "zh",
    cpu_label: str = "",
    preview: bool = False,
) -> str:
    """WRITE locally: batch author + validate + save + CSV export in one call.

    Send either a complete IL string or ALL ladder rungs, not both. For FX SFC
    use the existing STL/RET IL representation, not native graphical SFC blocks.
    Validation errors block writes; warnings are returned. Existing projects
    require overwrite=true. Responses stay compact unless preview=true. It
    never opens GX Works2, imports a CSV, or connects to a PLC. Importing the
    resulting CSV still requires explicit whole-program replacement approval.
    """
    if bool(il.strip()) == bool(rungs):
        return json.dumps({"error": "pass exactly one non-empty il or rungs input"})
    if not cpu.strip():
        return json.dumps({"error": "cpu is required for validation and CSV labeling"})
    if _cpu_limits(cpu) is None:
        return json.dumps({"error": "unsupported CPU family; specify Q, L, FX3U or FX5U (or a supported model prefix)"})
    if locale not in {"zh", "ja", "en"}:
        return json.dumps({"error": "locale must be zh, ja or en"})
    phase = "validation"
    project_may_be_modified = False
    try:
        program = compile_rungs(rungs) if rungs else parse_il(il, title=name)
        program.title = name
        validation = summarize(validate_program(program, cpu=cpu))
        if not validation["ok"]:
            return json.dumps({"prepared": False, "validation": validation}, ensure_ascii=False)
        workspace = store()
        target = workspace.project_path(name)
        with project_lock(Project(name, target, cpu=cpu)):
            existing = target.exists()
            if existing:
                if not overwrite:
                    return json.dumps({"error": "project already exists; pass overwrite=true to replace its program"})
                project = workspace.get(name)
                project.cpu = cpu
            # Export first to a staging file. Export failures must not change an
            # existing program and leave its old CSV looking like a new artifact.
            with _temporary_workspace(".prepare-", workspace.workspace) as (staging, cleanup_warnings):
                phase = "csv_export"
                staged_csv = write_program_csv(program, Path(staging) / "program.csv",
                                               project_name=name, cpu_label=cpu_label or cpu, locale=locale)
                phase = "project_save"
                project_may_be_modified = True
                if existing:
                    project.save(program)
                else:
                    project = workspace.create(name, cpu=cpu, initial_program=program)
                phase = "csv_publish"
                csv_path = project.root / (name + ".csv")
                os.replace(staged_csv, csv_path)
    except (BuildError, ILParseError, DeviceError, StoreError, CSVFormatError, OSError) as exc:
        return json.dumps({"prepared": False, "error": str(exc), "phase": phase,
                           "projectMayBeModified": project_may_be_modified,
                           "csvUpdated": False, "doNotImport": True,
                           "hint": "retry preparation and inspect the project before importing; no valid new CSV returned"},
                          ensure_ascii=False)
    result: dict[str, Any] = {
        "prepared": True, "project": name, "cpu": cpu,
        "instructions": len(program), "devices": len(program.devices()),
        "validation": validation, "csv": str(csv_path), "encoding": "utf-16-le",
        "imported": False,
    }
    if preview:
        result["il"] = render_il(program)
        result["ascii"] = render_ascii(program)
    if cleanup_warnings:
        result["cleanupWarnings"] = cleanup_warnings
    return json.dumps(result, ensure_ascii=False)


@server.tool()
def gx_program_create(
    name: str,
    cpu: str = "Q",
    title: str = "",
    il: str = "",
    rungs: list[dict[str, Any]] | None = None,
    overwrite: bool = False,
    sfc: dict[str, Any] | None = None,
) -> str:
    """Create a program project.

    Pass either IL text or a list of rungs. A rung looks like:
      {"out": "Y0", "comment": "start", "logic": {"or": [{"and": ["X0", {"not": "X1"}]}, "M10"]}}
    也可传 sfc={initial:'S0', steps:[{state:'S0', actions:[{out:'Y0'}],
    transitions:[{to:'S20', when:'X0'}]}, ...]}，cpu 必须显式为 FX3U。
    定时器回路可用 {out:'T0', preset:'K10', logic:'M0'}。
    The logic tree supports and / or, and operands may be a device string or
    {"not": "X1"}, {"rise": "X2"}, {"fall": "X3"}.
    """

    try:
        if sum((bool(rungs), bool(il), sfc is not None)) > 1:
            raise BuildError("il、rungs、sfc 只能选择一种输入")
        if sfc is not None:
            program = compile_stl(sfc, cpu)
            validation = summarize(validate_program(program, cpu=cpu))
            if not validation["ok"]:
                return json.dumps({"error": "SFC 校验失败", "validation": validation}, ensure_ascii=False)
        elif rungs:
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
def gx_write_project(name: str, path: str, template_path: str = "", block: str = "", confirm: bool = False) -> str:
    """直接写出新的 GXW 原生源码，不启动 GX Works2，不调用 computer use。

    name 是已用 gx_program_create 编写的 FX3U 梯形图或 SFC 工程。
    path 必须为尚不存在的 .gxw 文件；原始模板始终只读。
    写入后自动读回源码，并逐一核实所有其他数据流未变。
    requiresCompile=true 表示仍需在 GX Works2 编译；源码读回不是实体 PLC 验证。
    """
    if not confirm:
        return json.dumps({"ok": False, "error": "请传 confirm=true，写出新的 GXW 文件"}, ensure_ascii=False)
    from .native import write_gxw
    try:
        project = store().get(name)
        template = template_path or str(Path(__file__).with_name("data") / "fx3u-ladder.gxw")
        result = write_gxw(template, path, project.read_program(), cpu=project.cpu, block=block)
    except (GxwError, StoreError, ILParseError, DeviceError, OSError, ValueError) as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False, indent=2)


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
def gx_program_slice(name: str, start: int = 0, limit: int = 50) -> str:
    """Read a bounded instruction slice with stable IDs and a version for local patching.

    IDs refer to instructions, NOT inferred rung boundaries. Follow nextStart for
    more context. External rewrites invalidate IDs; fetch a fresh slice on conflict.
    """
    try:
        return json.dumps(read_patch_view(store().get(name), start=start, limit=limit), ensure_ascii=False)
    except (StoreError, PatchError, OSError, ValueError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)


@server.tool()
def gx_program_patch(name: str, expected_version: str, operations: list[dict[str, Any]],
                     export_csv: bool = True, locale: str = "zh") -> str:
    """WRITE local instruction edits by stable ID, with mandatory version check.

    Each operation has op (insert_before/insert_after/replace/delete), target_id,
    and il (except delete). Read gx_program_slice first. The entire resulting
    program is validated. By default refreshes CSV within the same call; never
    imports it or accesses a PLC. Use versioned patches for cooperative editing;
    old whole-program tools have no stale-version protection. Process crashes
    are not a multi-file transaction; inspect any reported partial-write error.
    """
    if locale not in {"zh", "ja", "en"}:
        return json.dumps({"applied": False, "error": "locale must be zh, ja or en"})
    try:
        project = store().get(name)
        with project_lock(project):
            result = apply_patch(project, expected_version, operations)
            result.update({"applied": True, "imported": False, "csvUpdated": False})
            if export_csv:
                try:
                    fresh = store().get(name)
                    path = write_program_csv(fresh.read_program(), fresh.root / (name + ".csv"),
                                             project_name=name, cpu_label=fresh.cpu, locale=locale)
                    result.update({"csvUpdated": True, "csv": str(path)})
                except (OSError, CSVFormatError, ILParseError) as exc:
                    result.update({"csvError": str(exc), "doNotImport": True})
            else:
                result["doNotImport"] = True
            return json.dumps(result, ensure_ascii=False)
    except VersionConflict as exc:
        return json.dumps({"applied": False, "conflict": True, "error": str(exc),
                           "hint": "read a fresh slice and reconsider the patch; do not blindly retry"})
    except PatchWriteError as exc:
        return json.dumps({"applied": False, "error": str(exc), "rollbackComplete": exc.rollback_complete,
                           "doNotImport": True})
    except (StoreError, PatchError, OSError, ValueError) as exc:
        return json.dumps({"applied": False, "error": str(exc)}, ensure_ascii=False)


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
    if "sfc" in wanted or any(ins.mnemonic == "STL" for ins in program.instructions):
        payload["sfc"] = describe_sfc(program)
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
    cpu_label: str = "",
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
    target = path or str(project.root / (project.name + ".csv"))
    try:
        with project_lock(project):
            project = store().get(name)
            program = project.read_program()
            written = write_program_csv(
                program, target, project_name=project.name, cpu_label=cpu_label or project.cpu, locale=locale
            )
    except (OSError, CSVFormatError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return json.dumps(
        {
            "written": str(written),
            "rows": 3 + sum(max(1, len(item.operands)) for item in program),
            "instructions": len(program),
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


def _decode_gxw_body(project, entry):
    """按源码格式路由，SFC 子程序保持步骤绑定，禁止扁平化导入。"""
    from .sfc_binary import is_sfc_source, read_sfc_block
    body = project.read_stream(entry)
    if entry.name.lower().endswith(".pou") and is_sfc_source(body):
        if project.cpu != "FX3U/FX3UC":
            raise GxwError("原生 SFC 读回目前核验 FX3U/FX3UC")
        return dict(decoded=True, coverage=1.0, representation="native-sfc-graph",
                    recommendedTool="gx_sfc_read", editable=False, runnable=False, **read_sfc_block(body))
    decoder = pou_stream if entry.name.lower().endswith(".pou") else main_res_program
    return decoder(body, cpu=project.cpu, text_encoding=project.text_encoding)


@server.tool()
def gx_gxw_inventory(path: str) -> str:
    """Read a .gxw without GX Works2: identity, inventory, per-program decode status.

    .gxw is an OLE2 container whose _hdb stream nests a second OLE2; the outer
    projectdatalist.xml diffgram maps every data file (iID == inner stream id):
    MAIN.res program bodies, POU .pou bodies, task bindings, COMMENT.qcd,
    parameters and libraries. Q-family MAIN.res bodies are decoded to IL with the
    grammar proven in re-evidence/GRAMMAR.md (verified byte-exact on a known
    round trip); anything unverified stops with a stopReason instead of guessing.
    FX3U 原生 SFC 返回格式识别与图形诊断；完整动作、条件可用 gx_sfc_read 读取。
    未支持的记录仍明确报告读取边界，不据此认定程序为空。
    """

    try:
        project = open_gxw(path)
    except GxwError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    payload = project.summary()
    structure = project.program_structure()
    for entry in structure:
        if entry.get("available") and entry.get("name", "").lower().endswith((".res", ".pou")):
            try:
                decoded = _decode_gxw_body(project, project.find(entry["name"]))
            except GxwError as exc:
                decoded = {"decoded": False, "error": str(exc)}
            entry["decode"] = {
                k: decoded[k]
                for k in ("decoded", "coverage", "il", "stopReason", "error", "edgeFidelityContacts",
                          "representation", "recommendedTool", "topologyVerified", "diagnostics")
                if k in decoded
            }
            if decoded.get("decoded"):
                payload["programBodiesDecoded"] = payload.get("programBodiesDecoded", 0) + 1
    payload["programStructure"] = structure
    project.close()
    return json.dumps(payload, ensure_ascii=False, indent=2)


@server.tool()
def gx_gxw_open(path: str, import_into: str = "", overwrite: bool = False, program: str = "", cpu: str = "") -> str:
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
    summary["decodedPrograms"] = []
    try:
        for entry in project.programs():
            try:
                attempt = _decode_gxw_body(project, entry)
            except GxwError as exc:
                summary["decodedPrograms"].append({"name": entry.name, "decoded": False, "error": str(exc)})
                continue
            summary["decodedPrograms"].append({"name": entry.name, "id": entry.iid, **attempt})
        complete = [p for p in summary["decodedPrograms"] if p.get("decoded") and p.get("coverage") == 1.0]
        summary["programBodiesDecoded"] = len(complete)
        complete = [p for p in complete if "il" in p]
        summary["editable"] = bool(complete)
        if import_into:
            selected = [p for p in complete if p["name"] == program] if program else complete
            if len(selected) != 1:
                raise StoreError("请选择一个完整解码的程序块，传 program=文件清单中的完整名称；部分解码禁止写回")
            parsed = parse_il(selected[0]["il"], title=project.name)
            if overwrite and (store().project_path(import_into) / "program.il").exists():
                target_project = store().get(import_into)
                target_project.cpu = cpu or target_project.cpu
            else:
                target_project = store().create(import_into, cpu=cpu or project.cpu, title=project.name)
            target_project.save(parsed, comments=summary["deviceComments"])
            base = target_project.root
            for entry in project.programs():
                (base / ("program_" + entry.iid + "_" + _safe_name(entry.name) + ".raw.bin")).write_bytes(project.read_stream(entry))
            summary["storeProject"] = import_into
            summary["selectedProgram"] = selected[0]["name"]
            (base / "gxw_manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (GxwError, ILParseError, StoreError, OSError) as exc:
        summary["importError"] = str(exc)
    finally:
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
def gx_diagnose_output(name: str, device: str, snapshot: dict[str, Any] | None = None) -> str:
    """只读诊断FX3U的Y/M输出：解释未满足位条件及16/32位整数比较，保留与/或关系。

    snapshot传原始地址整数值时离线分析；省略则只读现有FX Simulator2。
    不启动/控制/写入模拟器或实体PLC；程序与采样版本对应关系须另行核对。
    """
    from datetime import datetime, timezone
    from .diagnostics import diagnose_output
    try:
        project=store().get(name)
        if project.cpu.upper() not in {'FX3U','FX3UC','FX3U/FX3UC'}:
            raise ValueError('不动作诊断当前仅验证FX3U/FX3UC')
        with project_lock(project):
            program=project.read_program();comments=project.device_comments()
            result=diagnose_output(program,device,snapshot if snapshot is not None else {},comments)
            source=dict(source='provided snapshot',sampledAt=None,atomic=False)
            if snapshot is None:
                from .simulator import read_devices
                source=read_devices(result['requiredDevices'])
                result=diagnose_output(program,device,source['values'],comments)
                source={k:v for k,v in source.items() if k!='values'}
                source.update(sampledAt=datetime.now(timezone.utc).isoformat(),atomic=False)
            result.update(project=name,programSha256=hashlib.sha256(render_il(program).encode('utf-8')).hexdigest(),sampling=source)
            from .handover import remember_evidence
            remember_evidence(project,result['programSha256'],'diagnostic',result)
            return json.dumps(result,ensure_ascii=False)
    except (StoreError, OSError, ValueError, KeyError, TypeError) as exc:
        return json.dumps(dict(ok=False,readOnly=True,physicalPlc=False,error=str(exc),
            hint='核对项目/地址；无仿真时可提供snapshot离线诊断。'),ensure_ascii=False)


@server.tool()
def gx_change_impact(before_name: str, after_name: str, limit: int = 50) -> str:
    """只读比较两个FX3U/FX3UC程序，追踪变更影响的输出/步骤/定时器/连续字并建议复测。

    before_name与after_name为已有程序名；limit限制明细条数，不截断地址汇总。
    不编辑、编译、启动软件或访问PLC；复杂控制流与未知寻址明确扩大候选范围。
    """
    from contextlib import ExitStack
    from .impact import change_impact
    try:
        before=store().get(before_name);after=store().get(after_name)
        if any(p.cpu.upper() not in {'FX3U','FX3UC','FX3U/FX3UC'} for p in (before,after)):
            raise ValueError('改动影响报告当前仅验证FX3U/FX3UC')
        with ExitStack() as locks:
            projects={str(p.root):p for p in (before,after)}
            for key in sorted(projects):locks.enter_context(project_lock(projects[key]))
            result=change_impact(before.read_program(),after.read_program(),limit=limit)
            result.update(beforeProject=before_name,afterProject=after_name,
                          cpuTypes={'before':before.cpu,'after':after.cpu})
            from .handover import remember_evidence
            remember_evidence(before,result['beforeSha256'],'impact',result,role='before')
            remember_evidence(after,result['afterSha256'],'impact',result,role='after')
            return json.dumps(result,ensure_ascii=False)
    except (StoreError,OSError,ValueError,KeyError,TypeError) as exc:
        return json.dumps(dict(ok=False,readOnly=True,physicalPlc=False,error=str(exc),
            hint='先保存修改前后两个程序，再核对程序名和比较范围。'),ensure_ascii=False)


@server.tool()
def gx_handover_export(name: str, path: str = "") -> str:
    """新建调试资料ZIP：IL、I/O注释、步骤置复位、静态检查和本会话匹配版本证据。

    不改源程序、不操作模拟器/PLC；path省略时写工作区handover目录，已有文件拒绝覆盖。
    不含原生GXW全工程，缺失标签/签字保留待填，静态建议与仿真记录不能代替现场验收。
    """
    from .handover import export_handover
    try:return json.dumps(export_handover(store().get(name),path or None),ensure_ascii=False)
    except (StoreError,OSError,ValueError,KeyError,TypeError) as exc:
        return json.dumps(dict(ok=False,error=str(exc),programModified=False,physicalPlc=False),ensure_ascii=False)


@server.tool()
def gx_handover_verify(path: str, name: str = "") -> str:
    """只读核验资料ZIP的文件、CRC、哈希及证据版本；name可选比较工作区CPU和代码。

    不解压、不导入、不访问模拟器/PLC。workspaceMatch为空表示未比较工作区。
    完整性通过不证明来源真实、仿真装载代码一致或现场已验收。
    """
    from .handover import verify_handover
    try:
        return json.dumps(verify_handover(path,store().get(name) if name else None),ensure_ascii=False)
    except (StoreError,OSError,ValueError,KeyError,TypeError,RecursionError) as exc:
        return json.dumps(dict(ok=False,error=str(exc),readOnly=True,programModified=False,
            simulatorAccessed=False,physicalPlc=False),ensure_ascii=False)


@server.tool()
def gx_lint_program(name: str) -> str:
    """Static review: contextual coil writes and write-only devices.

    SET/RST and FX STL state outputs are classified separately. Write-only
    devices are inventory for review, not proof of dead logic (outputs may
    drive external equipment). Structural checks live in gx_program_validate.
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
        except (OSError, CSVFormatError) as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
    if not target:
        return json.dumps({"error": "pass csv_path or project"}, ensure_ascii=False)
    try:
        result = ui.import_program_csv(target, title_hint=title_hint or None, confirm=True)
    except ui.AutomationError as exc:
        return json.dumps({"error": str(exc), "csv": target}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False, indent=2)


@server.tool()
def gx_verify_import(
    name: str,
    readback_path: str = "",
    title_hint: str = "",
    compare_comments: bool = True,
) -> str:
    """Compare stored instructions with a separate GX Works2 CSV readback.

    With readback_path, compares that file only and does NOT claim a live import
    was verified. Without it, exports the currently open GX Works2 program to a
    unique temporary CSV using the existing Windows UI bridge, then compares.
    Never imports, compiles, saves a GXW, or connects to PLC hardware. Matching
    content is not evidence of runtime safety or successful compilation.
    """
    try:
        project = store().get(name)
        before = project.il_path.read_bytes()
        expected = parse_il(before.decode("utf-8"), title=name)
        version = hashlib.sha256(before).hexdigest()

        def verify(path: str, live: bool) -> dict[str, Any]:
            actual = read_program_csv(path).to_program(title=name)
            if project.il_path.read_bytes() != before:
                return {"verified": False, "error": "expected project changed during readback; retry",
                        "expectedVersion": version, "liveReadback": live}
            comparison = compare_programs(expected, actual, compare_comments=compare_comments)
            return {"verified": live and comparison["matches"], "liveReadback": live,
                    "source": "gxworks2_export" if live else "provided_csv",
                    "expectedVersion": version, **comparison,
                    "compiled": False, "hardwareValidated": False,
                    "note": "content comparison only; provided CSV provenance/freshness is not established" if not live
                    else "fresh UI export compared; compilation and runtime validation remain separate"}

        if readback_path:
            result = verify(readback_path, False)
        else:
            from . import ui
            with tempfile.TemporaryDirectory(prefix="gx-readback-") as directory:
                path = str(Path(directory) / "readback.csv")
                try:
                    report = ui.export_program_csv(path, title_hint=title_hint or None)
                except ui.AutomationError as exc:
                    return json.dumps({"verified": False, "error": str(exc), "liveReadback": False})
                if not report.get("ok") or not Path(path).is_file():
                    return json.dumps({"verified": False, "error": "fresh GX Works2 export was not produced"})
                result = verify(path, True)
        return json.dumps(result, ensure_ascii=False)
    except (StoreError, CSVFormatError, ILParseError, DeviceError, OSError, UnicodeError) as exc:
        return json.dumps({"verified": False, "error": str(exc)}, ensure_ascii=False)


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
def plc_list_ports() -> str:
    """被动枚举本机 COM 口，区分已知蓝牙映射；不打开端口或连接、写入 PLC。

    plcVerified=false 表示尚未通过协议读取 CPU 身份。
    不可仅凭 COM 编号或 USB 转串口线品牌选择下载目标。
    """
    from .ports import list_ports
    try:
        return json.dumps(list_ports(), ensure_ascii=False)
    except OSError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


@server.tool()
def plc_find_ports(host: str = "127.0.0.1", timeout: float = 1.0) -> str:
    """只读寻找 MC 端口，并附带未打开的串口登记。

    不打开 COM，不写软元件，不远程 RUN/STOP，也不下载。候选只有 TCP 5000、5555、5556。
    恰好一个候选返回 CPU 型号才给出 selected；没有或多个都不代选。
    5000 不是协议规定的端口。GX Simulator2 不监听这些端口，请改用 gx_simulator_status。
    """
    from .ports import find_ports
    try:
        return json.dumps(find_ports(host, timeout), ensure_ascii=False)
    except (OSError, ValueError, TypeError) as exc:
        return json.dumps({"ok": False, "error": str(exc), "writes": 0, "openedSerialPorts": 0},
            ensure_ascii=False)


@server.tool()
def plc_probe(host: str = "127.0.0.1", port: int = 5000, timeout: float = 2.0, transport: str = "tcp") -> str:
    """Check an MC-protocol endpoint with a CPU-model read. GX Simulator2 uses gx_simulator_status instead."""

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
    confirm: bool = False,
) -> str:
    """写在线 PLC 软元件。写入前必须问用户「现在写入吗？」。

    用户明确同意后才可同时传 confirm=true 与 allow_write=true。
    未确认时不连接、不写入。
    """

    if confirm is not True:
        return json.dumps(
            {
                "ok": False,
                "written": 0,
                "needsUserConfirmation": True,
                "question": "现在写入吗？",
                "error": "写入前必须先问用户「现在写入吗？」。用户明确同意后，再同时传 confirm=true 和 allow_write=true。",
                "host": host,
                "port": port,
                "device": device,
                "values": values,
            },
            ensure_ascii=False,
            indent=2,
        )
    if allow_write is not True:
        return json.dumps(
            {
                "ok": False,
                "written": 0,
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
