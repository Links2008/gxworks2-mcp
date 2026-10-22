"""FX3U 原生 SFC 的布局与独立连接读回。"""

import base64
import hashlib
import json
import os
import struct
import subprocess
import shutil
import tempfile
import threading
import time
from types import SimpleNamespace
from pathlib import Path

from .sfc import compile_stl
from .gxw import GxwError, open_gxw
from .locking import project_lock
from .devices import parse_device

_native_lock = threading.Lock()


def native_batch(blocks: list[dict], install_root: str, *, directory: str = "") -> dict:
    """串行运行一个限时 x86 辅助进程；编译器只在源码变化时运行。"""
    if os.name != "nt":
        raise GxwError("本机原生 SFC 适配需要 Windows")
    root = Path(install_root).resolve()
    dll_dir = root / "Easysocket" / "CodeGenerator"
    dll = dll_dir / "ECCodeGeneratorFX2.dll"
    if hashlib.sha256(dll.read_bytes()).hexdigest() != "7915fec25ecf9ab0a2a9ff7072ec23deb6187d83dbe86b885747ba29e4be99b8":
        raise GxwError("仅验证 GX Works2 1.620 的 FX 14.67 原生库，当前文件不匹配")
    source = Path(__file__).with_name("data") / "NativeFxSfc1620.cs"
    workspace_source = source.with_name("WorkspaceSfc1620.cs")
    digest = hashlib.sha256(source.read_bytes() + workspace_source.read_bytes()).hexdigest()
    cache = Path(os.environ.get("GXWORKS2_MCP_HOME", Path.home() / ".gxworks2-mcp")) / "native"
    cache.mkdir(parents=True, exist_ok=True)
    executable = cache / ("fx-sfc-" + digest[:20] + ".exe")
    with _native_lock, project_lock(SimpleNamespace(root=cache / "fx-sfc-runtime"), timeout=20):
        try:
            if not executable.exists():
                compiler = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
                with tempfile.TemporaryDirectory(prefix="compile-", dir=cache) as scratch:
                    candidate = Path(scratch) / "native.exe"
                    done = subprocess.run([str(compiler), "/nologo", "/platform:x86",
                        "/reference:System.Web.Extensions.dll", "/reference:System.Windows.Forms.dll",
                        "/out:" + str(candidate), str(source), str(workspace_source)],
                        capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                    if done.returncode:
                        raise GxwError("原生辅助程序编译失败：" + done.stdout.decode("utf-8", errors="replace"))
                    os.replace(candidate, executable)
            started = time.perf_counter()
            request = {"blocks": blocks}
            if directory:
                request["projectDirectory"] = directory
            try:
                done = subprocess.run([str(executable), str(dll_dir)],
                    input=json.dumps(request).encode("utf-8"), capture_output=True,
                    timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            finally:
                if directory:
                    _cleanup_native_temp(Path(directory))
            if done.returncode:
                raise GxwError("原生 SFC 拒绝：" + done.stderr.decode("utf-8", errors="replace"))
            result = json.loads(done.stdout)
            if not result.get("ok") or len(result.get("blocks", [])) != len(blocks):
                raise GxwError("原生返回结构无效")
            for supplied, observed in zip(blocks, result["blocks"]):
                if observed["bodies"] != supplied["bodies"]:
                    raise GxwError("原生清空重读的动作或条件与输入不一致")
            result["nativeMs"] = round((time.perf_counter() - started) * 1000, 2)
            return result
        except subprocess.TimeoutExpired as exc:
            raise GxwError("原生进程超过 15 秒，已终止并回收") from exc


def _cleanup_native_temp(directory: Path):
    """只移除本次已退出子进程登记且带相同归属标记的临时目录。"""
    pointer = directory / "native-temp-path.txt"
    if not pointer.exists():
        return
    path = Path(pointer.read_text(encoding="utf-8-sig")).resolve()
    base = Path(os.environ["LOCALAPPDATA"]) / "MITSUBISHI/SWnDN-GPPW2/Project/DZTempData"
    owner = path / "gxworks2-mcp-owner.txt"
    if path.parent != base.resolve() or not path.name.isdecimal() or not owner.is_file():
        raise GxwError("原生临时目录归属不匹配，保留现场")
    if Path(owner.read_text(encoding="utf-8-sig")).resolve() != directory.resolve():
        raise GxwError("原生临时目录标记不同，保留现场")
    shutil.rmtree(path)


def _body(program):
    from .il import parse_il, render_il
    from .native import encode_program
    return encode_program(parse_il(render_il(program) + "\nEND"))[:-3]


def sequence_blocks(spec: dict) -> tuple[dict, list[dict]]:
    """将结构化步骤与条件绑定到原生引用；启动回路独立成扫描块。"""
    from .author import compile_rung, compile_logic
    from .il import Program, parse_il
    layout = layout_sfc(spec)
    bodies = [dict(kind="action", number=i, body=base64.b64encode(_body(
        Program(instructions=[ins for action in s.get("actions", []) for ins in compile_rung(action)])
    )).decode()) for i, s in enumerate(spec["steps"])]
    bodies += [dict(kind="transition", number=t["number"], body=base64.b64encode(
        _body(parse_il("\n".join(compile_logic(t["when"]))))).decode()) for t in layout["transitions"]]
    reset = [line for step in spec["steps"] for line in ("LD M8002", "RST " + step["state"])]
    initial = parse_il("\n".join(reset + ["LD M8002", "SET " + spec.get("initial", spec["steps"][0]["state"])]))
    initial.instructions.extend(ins for rung in spec.get("preamble", []) for ins in compile_rung(rung))
    startup = dict(tokens=base64.b64encode(bytes.fromhex(
        "056c02170504980004056c021d0504990004056202000504980004056c010a05")).decode(),
        bodies=[dict(kind="action", number=0, body=base64.b64encode(_body(initial)).decode()),
                dict(kind="transition", number=0, body="")])
    return layout, [dict(symbols=layout["symbols"], bodies=bodies), startup]


def layout_sfc(spec: dict) -> dict:
    """生成顺序及双路选择图；顺序保持输入转移的优先级。"""
    pending, nodes = [(spec, 0)], 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if depth > 24 or nodes > 32768:
            raise GxwError("SFC 输入深度或节点数量超界")
        if isinstance(value, dict):
            pending.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            pending.extend((v, depth + 1) for v in value)
    if not isinstance(spec, dict) or set(spec) - {"initial", "steps", "preamble"}:
        raise GxwError("SFC 输入含未知字段")
    if not isinstance(spec.get("steps"), list) or not 1 <= len(spec["steps"]) <= 64:
        raise GxwError("原生 SFC 需要 1..64 个步骤")
    if not isinstance(spec.get("preamble", []), list):
        raise GxwError("preamble 必须为回路数组")
    for step in spec["steps"]:
        if not isinstance(step, dict) or set(step) - {"state", "actions", "transitions"}:
            raise GxwError("SFC 步骤含未知字段")
        if not isinstance(step.get("actions", []), list) or not isinstance(step.get("transitions", []), list):
            raise GxwError("actions 和 transitions 必须为数组")
        for edge in step.get("transitions", []):
            if not isinstance(edge, dict) or set(edge) != {"to", "when"}:
                raise GxwError("SFC 转移必须只含 to 和 when")
    rungs = [*spec.get("preamble", []), *(a for s in spec["steps"] for a in s.get("actions", []))]
    for rung in rungs:
        if not isinstance(rung, dict) or set(rung) - {"out", "logic", "preset", "comment"}:
            raise GxwError("SFC 回路含未知字段；支持 out、logic、preset、comment")
        parse_device(rung.get("out"))
    logic = [r["logic"] for r in rungs if r.get("logic") is not None]
    logic += [e["when"] for s in spec["steps"] for e in s.get("transitions", [])]
    while logic:
        value = logic.pop()
        if isinstance(value, str):
            parse_device(value)
        elif isinstance(value, dict) and len(value) == 1:
            key, children = next(iter(value.items()))
            if key in {"and", "or"} and isinstance(children, list) and children:
                logic.extend(children)
            elif key in {"not", "rise", "fall"} and isinstance(children, str):
                parse_device(children)
            else:
                raise GxwError("SFC 条件仅支持软元件和 and/or/not/rise/fall")
        else:
            raise GxwError("SFC 条件结构无效")
    compile_stl(spec, "FX3U")
    steps = spec["steps"]
    states = [s["state"].upper() for s in steps]
    if spec.get("initial", states[0]).upper() != states[0]:
        raise ValueError("原生图形的初始步骤必须排在第一位")
    points, x, y = [], 0, 0
    for index, step in enumerate(steps):
        points.append((x, y))
        edges = step.get("transitions", [])
        if not 1 <= len(edges) <= 2 or any(not isinstance(e["to"], str) for e in edges):
            raise ValueError("原生图形目前支持每步一至两路选择转移")
        if index < len(steps) - 1:
            targets = [e["to"].upper() for e in edges]
            if states[index + 1] not in targets:
                raise ValueError("步骤排列必须包含通往下一步骤的转移")
            x += 2 * targets.index(states[index + 1])
            y += 6 if len(edges) == 2 else 3
    if x + 2 >= 10 or y + 6 >= 306:
        raise ValueError("图形超出已验证的原生画布范围")
    symbols = [
        dict(kind="step", x=x, y=y, type=26 if i == 0 else 4,
             action=int(state[1:]), number=i)
        for i, (state, (x, y)) in enumerate(zip(states, points))
    ]
    transitions = []
    for index, (step, (x, y)) in enumerate(zip(steps, points)):
        edges = step["transitions"]
        two = len(edges) == 2
        if two:
            symbols.append(dict(kind="branch", start=x, end=x + 2, y=y + 1))
        for branch, edge in enumerate(edges):
            target = states.index(edge["to"].upper())
            column = x + 2 * branch
            number = len(transitions)
            transitions.append(dict(number=number, source=states[index],
                                    target=states[target], when=edge["when"]))
            symbols.append(dict(kind="transition", x=column, y=y + (2 if two else 1),
                                number=number))
            symbols.append(dict(kind="line", x=column, start=y + (3 if two else 2),
                                end=y + (5 if two else 2)))
            if target != index + 1:
                symbols.append(dict(kind="step", x=column, y=y + (6 if two else 3),
                                    type=15, action=0, number=target))
    return dict(steps=[dict(state=s, number=i) for i, s in enumerate(states)],
                transitions=transitions, symbols=symbols)


def graph_edges(cells: list[dict]) -> list[tuple[int, int, int]]:
    """仅按实际格点的连接端口恢复源步骤、转移、目标步骤。"""
    grid = {(c["x"], c["y"]): c for c in cells}
    if len(grid) != len(cells):
        raise ValueError("图形连接包含重叠格点")
    # Validate the entire canvas, not only paths reached from known steps.
    # Otherwise an unsupported parallel branch or unknown symbol can disappear
    # from the readback while the supported subset still compares equal.
    step_numbers, transition_numbers = set(), set()
    for cell in cells:
        kind = cell["result"]
        if kind == 1:
            if cell["type"] not in (4, 10, 20, 26, 15):
                raise ValueError("图形连接包含尚未支持的步骤类型")
            if cell["type"] != 15:
                if cell["number"] in step_numbers:
                    raise ValueError("图形连接包含重复步骤编号")
                step_numbers.add(cell["number"])
        elif kind == 2:
            if cell["number"] in transition_numbers:
                raise ValueError("图形连接包含重复转移编号")
            transition_numbers.add(cell["number"])
        elif kind == 3:
            if cell["branch"] & 0xF0 not in (0, 0x50):
                raise ValueError("图形连接包含尚未支持的分支类型")
        else:
            raise ValueError("图形连接包含尚未支持的格点类型")
    steps = {c["number"]: c for c in cells
             if c["result"] == 1 and c["type"] in (4, 10, 20, 26)}
    if not steps:
        raise ValueError("图形连接没有步骤")
    result = []
    covered = {(c["x"], c["y"]) for c in steps.values()}
    for number, source in steps.items():
        pending = [(source["x"], source["y"] + 1, 2, None)]
        visited = set()
        found = 0
        while pending:
            x, y, entry, transition = pending.pop()
            key = x, y, entry, transition
            if key in visited:
                continue
            visited.add(key)
            cell = grid.get((x, y))
            if cell is None:
                raise ValueError(f"图形连接中断于 {x},{y}")
            covered.add((x, y))
            kind = cell["result"]
            if kind == 1:
                if entry != 2 or transition is None or cell["number"] not in steps:
                    raise ValueError("图形连接缺少转移或引用未知步骤")
                result.append((number, transition, cell["number"]))
                found += 1
                continue
            if kind == 2:
                if entry != 2 or transition is not None:
                    raise ValueError("图形连接含连续转移")
                pending.append((x, y + 1, 2, cell["number"]))
                continue
            if kind != 3 or cell["branch"] & 0xF0 not in (0, 0x50):
                raise ValueError("图形连接包含尚未支持的分支类型")
            ports = cell["branch"] & 15
            if not ports & entry:
                raise ValueError("图形连接端口不匹配")
            if not ports & (1 | 4 | 8) & ~entry:
                raise ValueError("图形连接包含没有出口的分支")
            for bit, dx, dy, opposite in ((1, 0, 1, 2), (4, 1, 0, 8), (8, -1, 0, 4)):
                if ports & bit and bit != entry:
                    pending.append((x + dx, y + dy, opposite, transition))
        if not found:
            raise ValueError("步骤没有有效连接")
    if covered != set(grid):
        raise ValueError("图形连接包含未读取的孤立格点")
    return sorted(result)


def _check_graph(layout, observed):
    numbers = {s["state"]: s["number"] for s in layout["steps"]}
    expected = sorted((numbers[t["source"]], t["number"], numbers[t["target"]]) for t in layout["transitions"])
    if graph_edges(observed["cells"]) != expected:
        raise GxwError("原生读回的步骤连接与输入不一致")
    actual = {c["number"]: c["substep"] for c in observed["cells"]
              if c["result"] == 1 and c["type"] in (4, 10, 20, 26)}
    if actual != {s["number"]: int(s["state"][1:]) for s in layout["steps"]}:
        raise GxwError("原生读回的状态软元件与输入不一致")


def read_sfc_gxw(path: str) -> dict:
    """纯文件读取原生 SFC 图形引用、动作与转移，无原生进程。"""
    from .sfc_binary import parse_source, parse_graph, decode_body
    started = time.perf_counter()
    project = open_gxw(path)
    try:
        if project.cpu != "FX3U/FX3UC":
            raise GxwError("原生 SFC 读回目前核验 FX3U/FX3UC")
        blocks = []
        for entry in project.programs():
            if not entry.name.endswith(".Program.pou"):
                continue
            source = parse_source(project.read_stream(entry))
            graph = parse_graph(source["graph"])
            if {s["action"] for s in graph["steps"]} != set(source["actions"]) or {e["transition"] for e in graph["edges"]} != set(source["transitions"]):
                raise GxwError("图形引用与动作、条件注册不一致")
            blocks.append(dict(name=entry.name, scanBlock=graph["scanBlock"], steps=[
                {**step, "il": decode_body(source["actions"][step["action"]]["body"])} for step in graph["steps"]],
                transitions=[{**edge, "whenIl": decode_body(source["transitions"][edge["transition"]]["body"])} for edge in graph["edges"]]))
        if not blocks:
            raise GxwError("未发现可完整读取的原生 SFC 程序块")
        return dict(ok=True, path=project.path, cpu=project.cpu, representation="native-sfc-graph",
                    blocks=blocks, sourceReadback=True, computerUseCalls=0, nativeProcessCount=0,
                    readMs=round((time.perf_counter() - started) * 1000, 2))
    finally:
        project.close()


def write_sfc_gxw(template: str, output: str, spec: dict, install_root: str) -> dict:
    """单个原生进程完成图形、源码、严格编译检查和保存；核验后另存。"""
    from .sfc_binary import parse_source, words
    started = time.perf_counter()
    source, target = Path(template).resolve(), Path(output).resolve()
    if target == source or target.exists() or target.suffix.lower() != ".gxw" or not target.parent.is_dir():
        raise GxwError("输出必须是已有目录中尚不存在的 .gxw 文件")
    layout, blocks = sequence_blocks(spec)
    project = open_gxw(source)
    names = ["Block.Program.pou", "Block1.Program.pou"]
    try:
        if project.cpu != "FX3U/FX3UC":
            raise GxwError("原生 SFC 模板必须是 FX3U/FX3UC")
        entries = {e.name: e for e in project.programs()}
        if set(entries) != {*names, "MAIN.res"}:
            raise GxwError("目前仅验证 Block 图形、Block1 启动回路和 MAIN.res 的双块 SFC 模板")
        for i, name in enumerate(names):
            parsed = parse_source(project.read_stream(entries[name]))
            if i == 1 and parsed["graph"] != base64.b64decode(blocks[1]["tokens"]):
                raise GxwError("模板 Block1 不是已验证的连续扫描启动块")
            size = 16 + len(parsed["graph"]) + len(parsed["cache"])
            blocks[i].update(name=name.split(".")[0],
                graphTemplate=base64.b64encode(words(size, 0, 0, 4 + len(parsed["graph"])) + parsed["graph"] + parsed["cache"]).decode(),
                actionNumbers=[int(s["state"][1:]) for s in layout["steps"]] if i == 0 else [0])
        before = {"/".join(n): project._inner.openstream(n).read() for n in project._inner.listdir()}
        data_names = {e.stream_id: e.name for e in project.data_files}
        snapshot = project._raw
    finally:
        project.close()
    with tempfile.TemporaryDirectory(prefix="gx-sfc-", ignore_cleanup_errors=True) as scratch:
        directory = Path(scratch)
        (directory / "input.gxw").write_bytes(snapshot)
        native = native_batch(blocks, install_root, directory=str(directory))
        _check_graph(layout, native["blocks"][0])
        check = open_gxw(directory / "output.gxw")
        try:
            if check.cpu != "FX3U/FX3UC":
                raise GxwError("保存后的 CPU 不一致")
            saved_entries = {e.name: e for e in check.programs()}
            if set(saved_entries) != set(entries):
                raise GxwError("原生保存改变了程序块清单")
            for i, name in enumerate(names):
                parsed = parse_source(check.read_stream(saved_entries[name]))
                actions = {blocks[i]["actionNumbers"][b["number"]]: base64.b64decode(b["body"])
                           for b in blocks[i]["bodies"] if b["kind"] == "action"}
                transitions = {b["number"]: base64.b64decode(b["body"])
                               for b in blocks[i]["bodies"] if b["kind"] == "transition"}
                if (parsed["graph"] != base64.b64decode(native["blocks"][i]["graph"]) or
                        {n: a["body"] for n, a in parsed["actions"].items()} != actions or
                        {n: t["body"] for n, t in parsed["transitions"].items()} != transitions):
                    raise GxwError("导出文件的图形、动作或条件与本次生成内容不一致")
            res = check.read_stream(saved_entries["MAIN.res"])
            compiled = base64.b64decode(native["program"])
            if len(res) < 58 or struct.unpack_from("<I", res, 54)[0] != len(compiled) or res[58:58 + len(compiled)] != compiled:
                raise GxwError("原生整工程编译结果与独立 SFC 转换结果不一致")
            after = {"/".join(n): check._inner.openstream(n).read() for n in check._inner.listdir()}
            allowed = {*names, "MAIN.res", "Param.lup", "DeviceAssignment.dat", "ESCompiler.stg", "CallTree.dat", "SystemLabel.sld"}
            changed = [data_names.get(sid, sid) for sid in before.keys() | after.keys() if before.get(sid) != after.get(sid)]
            if set(before) != set(after) or any(name not in allowed for name in changed):
                raise GxwError("原生保存改变了未识别的数据流，已拒绝交付：" + repr(changed))
        finally:
            check.close()
        # 在目标目录完成同盘原子发布，已有文件始终拒绝覆盖。
        with tempfile.TemporaryDirectory(prefix="gx-publish-", dir=target.parent, ignore_cleanup_errors=True) as publish:
            staged = Path(publish) / "project.gxw"
            shutil.copyfile(directory / "output.gxw", staged)
            output_sha = hashlib.sha256(staged.read_bytes()).hexdigest()
            if staged.read_bytes() != (directory / "output.gxw").read_bytes():
                raise GxwError("发布文件读回不同")
            os.rename(staged, target)
    return dict(ok=True, path=str(target), sha256=output_sha, representation="native-sfc-graph",
        steps=len(layout["steps"]), transitions=len(layout["transitions"]), sourceReadback=True,
        compiledReadback=True, nativeBindingsReadback=True, nativeProjectCompile=native["project"],
        changedStreams=sorted(changed), unrecognizedStreamsPreserved=True, requiresProjectCompile=False,
        nativeMs=native["nativeMs"], totalMs=round((time.perf_counter() - started) * 1000, 2),
        computerUseCalls=0, maxConcurrentNativeProcesses=1, nativeProcessCount=1, physicalPlcVerified=False)
