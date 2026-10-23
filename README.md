# GX Works2 MCP

**让 AI 把三菱 PLC 程序写进工程，并带着验证结果交付。**

[![MIT License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Windows native](https://img.shields.io/badge/Native-Windows-0078D4)](#支持范围)
[![MCP stdio](https://img.shields.io/badge/MCP-stdio-7C3AED)](#接入-ai-客户端)
[![GX Works2 verified](https://img.shields.io/badge/GX_Works2-1.620_verified-00875A)](docs/原生SFC_1620验证.md)
[![Multi-Agent](https://img.shields.io/badge/Multi--Agent-Collaboration-0E7490)](#多-agent-协作)
[![Local validation](https://img.shields.io/badge/Local_tests-582_passed_%2F_44_skipped-00875A)](docs/公开版验收.md)

面向 **GX Works2 / MELSEC** 的开源 MCP（Model Context Protocol）服务器，覆盖梯形图与 SFC 编写、原生 `.gxw` 读写、GX Simulator2 批量验收、不动作诊断、改动影响分析及调试资料交接。

核心链路已在 **Windows + GX Works2 1.620 + FX3U/FX3UC** 的限定环境验证。AI 负责理解工艺和编写逻辑，MCP 负责确定性操作、检查与证据留存。第三方独立项目，与三菱电机无隶属关系。

[快速开始](#快速开始) · [多 Agent 协作](#多-agent-协作) · [工具导航](#工具导航) · [验证结果](docs/公开版验收.md) · [支持范围](#支持范围)

## 为什么值得用

| 亮点 | 能帮工程人员做什么 |
|---|---|
| **原生工程输出** | 梯形图直接写入新的 `.gxw`；原生 SFC 图形一次完成生成、编译、程序检查、保存和读回，减少界面录入 |
| **真实模拟器验收** | 在 GX Simulator2 中模拟按钮与传感器输入，检查输出、步骤、定时/计数、响应窗口和持续保持 |
| **后台验证流程** | 既有验收入口在独立 Windows 桌面运行 GX，保护前台窗口，不发送全局鼠标键盘操作 |
| **看懂“不动作”** | 按实际程序追踪未满足的常开/常闭、分支、步骤及 16/32 位整数比较条件，列出缺失采样 |
| **看清“改了影响谁”** | 比较前后版本，追踪输出、步骤、定时器、计数器和连续字依赖，给出复测建议 |
| **多 Agent 协作** | 编写、审查和测试可以分工；工程锁、指令 ID 与版本校验为协作提供基础 |
| **交付有据可查** | 导出程序、I/O、注释、置复位清单、匹配版本证据和 SHA256；交接前再次核验资料及版本 |
| **按需加载工程技能** | 内置三菱地址规划、模式/报警审查、工艺场景测试三个技能，避免每道题重复整理规则 |

### 快，来自减少重复工作

- 一次提交完整逻辑，保持 MCP 会话；批量准备与批量验收减少逐条调用。
- 原生 SFC 写入自带编译和读回；工程未变且哈希一致时可复用已有原生结果。
- 回归、后台仿真和打包核对已有统一入口，失败会停止并保留阶段诊断。
- 同输入基准中，普通梯形图源码写入中位耗时 **466 → 167 ms**，STL 顺控 **439 → 163 ms**。这是文件写入函数的历史实测，不包含模型编写或 GX 编译时间。[测量方法](docs/第二轮优化验证.md)

## 快速开始

需要 Python 3.10+。原生工程写入、原生编译和模拟器功能需要 Windows；原生 SFC 编译和仿真还需要自行合法安装兼容版本的 GX Works2。

```powershell
git clone https://github.com/Links2008/gxworks2-mcp.git
cd gxworks2-mcp
py -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[native,ui,dev]"
./.venv/Scripts/python.exe -X utf8 -m gxworks2_mcp
```

最后一行启动 stdio MCP 服务，通常应由 AI 客户端启动。只使用离线编写、分析和 CSV 导出时，可安装 `pip install -e .`；开发测试增加 `.[dev]`。原生写入无需启用界面自动化。

### 接入 AI 客户端

支持标准 **stdio MCP** 接入。下面以采用 `mcpServers` 配置结构的客户端为例；将路径替换为本机实际路径，其他客户端使用其对应的 MCP 配置入口。

```json
{
  "mcpServers": {
    "gxworks2": {
      "command": "C:/tools/gxworks2-mcp/.venv/Scripts/python.exe",
      "args": ["-X", "utf8", "-m", "gxworks2_mcp"],
      "env": {
        "GXWORKS2_MCP_HOME": "C:/plc/mcp-workspace",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

安装采用 editable 模式，升级代码后重新连接 MCP 服务以加载新工具。原生 SFC 可通过 `GXWORKS2_INSTALL_ROOT` 或工具参数指定 GX Works2 安装根目录。

## 从编写到验收

以下是 **MCP 工具调用示意**，由客户端发送，不是可以直接独立运行的 Python 脚本。

### 1. 批量准备梯形图

```python
gx_program_prepare(
    name="motor", cpu="FX3U",
    rungs=[{"out": "Y0", "logic": {"and": ["X0", {"not": "X1"}]}}]
)
gx_program_read(name="motor")
gx_write_project(name="motor", path="C:/plc/motor.gxw", confirm=True)
```

输出目录须存在，输出文件须为新文件。普通梯形图源码写入返回 `requiresCompile=true` 时，仍须在 GX Works2 编译。助记符 IL 是本工具的中间表示，不宣称为 GX Works2 独立支持的源语言。

### 2. 原生 SFC 图形与编译

`gx_sfc_write(spec, path, template_path, installation_root, confirm=True)` 接受 `initial / preamble / steps` 结构；步骤包含 `state / actions / transitions`。

- 一次执行图形创建、动作与条件注册、整工程转换、程序检查、保存和磁盘读回。
- 输出新的 `.gxw`，拒绝覆盖模板；未知库版本、编译错误或读回差异会阻止发布。
- 目前要求兼容的双块 FX3U/FX3UC SFC 模板；支持顺序、双路选择和显式跳转。
- 参考 [顺启逆停](examples/fx3u_sequence.py)、[机械手 SFC](examples/fx3u_handling_sfc.py) 和 [钻孔工位](examples/fx3u_drilling.py)。[原生 SFC 说明](docs/原生SFC_1620验证.md)

### 3. GX Simulator2 批量验收

先在 GX Works2 加载已编译的测试工程，再通过模拟器工具启动、读写虚拟输入和验证动作。下面的期望仅适用于上面的 `motor` 逻辑，不能直接套用到其他工艺。

```python
gx_simulator_start(confirm=True)
gx_simulator_control(action="run", confirm=True)
gx_simulator_verify(steps=[
    {"inputs": {"X0": 0, "X1": 0}, "expect": {"Y0": 0}},
    {"inputs": {"X0": 1}, "expect": {"Y0": 1}, "hold_ms": 200},
    {"inputs": {"X1": 1}, "expect": {"Y0": 0}}
], confirm=True, project_name="motor")
gx_simulator_control(action="stop", confirm=True)
gx_simulator_stop(confirm=True)
```

支持批量输入、响应时间、持续保持和采样禁止组合。只写虚拟 X 输入，读取 X/Y/M/S/T/TN/D/C/CN；未知引擎哈希、多实例或读回失败会拒绝操作。`project_name` 绑定工作区版本，不自动认证模拟器实际装载代码。[模拟器说明](docs/GX_SIMULATOR2.md)

需要不打扰前台时，使用 [统一后台验收入口](docs/通用编写与验收.md#一次调用的完整验收)。普通桌面工具仍可能作用于当前 GX 工程，不应让多个 Agent 同时操作 GUI。

### 4. 诊断、改动评估与交接

```python
gx_diagnose_output(name="motor", device="Y0",
                  snapshot={"X0": 1, "X1": 1, "Y0": 0, "M8000": 1})
gx_change_impact(before_name="motor_before", after_name="motor_after")
gx_handover_export(name="motor")
gx_handover_verify(path="C:/plc/资料包.zip", name="motor")
```

前后版本与 ZIP 路径须替换为真实已保存对象。诊断可使用提供的快照完全离线运行；省略快照时只读现有模拟器。复杂控制流、缺失数据与锁存历史会保留未知，不把猜测当成故障结论。

## 多 Agent 协作

项目提供 **多 Agent 可共用的工程工具与并发保护**，Agent 的创建、消息和任务调度由宿主客户端负责。

| 分工 | 推荐职责 |
|---|---|
| 编写 Agent | 在独立命名的工程中生成梯形图/SFC，提交完整逻辑与工艺假设 |
| 审查 Agent | 读取程序，检查地址、模式切换、置复位和改动影响 |
| 验收 Agent | 按工艺设计正常、异常和恢复场景，集中执行编译与仿真 |
| 交付 Agent | 归档同版本证据、导出资料并核对最终代码版本 |

**协作保护已落到代码和测试：**

- 同工程合作写入使用可重入线程锁与操作系统文件锁，覆盖遵守该锁的多个 MCP 进程。
- `gx_program_slice` 返回指令 ID 和版本；`gx_program_patch` 要求 `expected_version`，旧版本补丁会被拒绝。
- 原生 SFC 在同一工作区内串行使用编译辅助进程；模拟器应交给单一调度者，跨服务进程不提供全局仿真事务。
- 证据缓存属于各自 MCP 进程。需要统一交付时，让诊断、具名验收和导出在同一持久服务会话执行。

推荐 **独立工程并行编写与审查 → 选定版本 → 串行编译与仿真 → 同会话归档**。外部编辑器绕过锁、进程中断时的多文件崩溃原子性不在锁保证范围内。[锁与补丁测试](tests/test_patching.py)

## 工具导航

| 场景 | 主要工具 |
|---|---|
| 编写与增量修改 | `gx_program_prepare`、`gx_program_create`、`gx_program_read`、`gx_program_slice`、`gx_program_patch` |
| 原生工程 | `gx_write_project`、`gx_sfc_write`、`gx_sfc_read`、`gx_gxw_inventory`、`gx_gxw_open`、`gx_gxw_comments` |
| 逻辑检查 | `gx_program_validate`、`gx_where_used`、`gx_trace_device`、`gx_lint_program` |
| 调试与改动评估 | `gx_diagnose_output`、`gx_change_impact` |
| 虚拟 CPU | `gx_simulator_start`、`gx_simulator_status`、`gx_simulator_control`、`gx_simulator_read`、`gx_simulator_inputs`、`gx_simulator_press`、`gx_simulator_verify`、`gx_simulator_stop` |
| 交付资料 | `gx_handover_export`、`gx_handover_verify` |
| 工程技能 | `gx_skill_list`、`gx_skill_read` |
| CSV 与可选 UI 通路 | `gx_program_export_csv`、`gx_program_import_csv`、`gx_import_csv`、`gx_compile_csv`、`gx_verify_import` |
| 独立 MC 协议通路 | `plc_probe`、`plc_read`、`plc_write`、`plc_remote_control` |

MCP 是 AI 工具协议，MC 是三菱设备通信协议。模拟器工具与实体 PLC 通路分开；MC 写入需显式确认及写入授权，不是自动下载工作流。

## 验证与开发

最近完整验收：**2026-10-23，626 项测试中 582 通过、44 项环境门控跳过；53 次真实 MCP 调用完成。** 包含正常动作、异常/恢复、16 种连续输出组合、14 步模式测试，以及交付包同版通过/异版拒绝。

完整自动流程（全量回归 + 后台仿真 + 打包核对）本机实测 **66.644 秒**，不包含新功能编写时间。后台验收未切换桌面，前后前台窗口句柄一致，模拟器已退出。徽章是带日期的本机验收快照，不是持续集成状态。[结果与复现边界](docs/公开版验收.md)

```powershell
py -X utf8 -m pytest
# 原生/现场集成用例按环境变量启用，缺少对应环境时会明确跳过。
```

具备已核验工程与原生报告时，在项目根目录运行完整后台流程（含打包需 Python 3.11+）：

```powershell
py -X utf8 -m tools.run_verification <工程绝对路径> <原生核验JSON绝对路径> <新输出目录> --package
```

源码包含 Python 服务、C# 原生适配源码、测试、工艺示例及文档。GX Works2 安装程序和专有 DLL 不随仓库分发。

## 支持范围

| 能力 | 当前边界 |
|---|---|
| 离线编写与分析 | Python；主要验证 FX3U/FX3UC，部分 Q/L 解析和 MC 能力按各工具说明 |
| 梯形图原生源码写入 | Windows + pywin32；简单梯形图/支持的 STL，生成后仍可能需要 GX 编译 |
| 原生 SFC 编译 | GX Works2 **1.620.0.1 / FX 14.67**，库哈希匹配且使用兼容双块模板 |
| GX Simulator2 | 已核验中文 1.620 的 FX 引擎；按可执行文件 SHA256 限定版本 |
| SFC 图形 | 顺序、双路选择、显式跳转；暂不支持并行分支和任意模板布局 |
| `.gxw` 读取 | 已知记录可解析；未知构造返回停止原因和部分结果，不伪造完整程序 |
| 不动作诊断 | FX3U/FX3UC 的 Y/M 输出；布尔条件和支持的 16/32 位有符号整数比较 |
| 实体 PLC | 独立 MC 通路存在；当前仿真与离线结果不等同于实体设备、接线或机械验收 |

X/Y 使用 FX 八进制规则；取不到的值不补零。交付包不含原生 GXW 全工程，SHA256 校验不等于签字认证；现场信息和验收结论保留待填。

## 文档与源码

- [通用编写与验收](docs/通用编写与验收.md) / [GX Simulator2](docs/GX_SIMULATOR2.md)
- [原生 SFC](docs/原生SFC_1620验证.md) / [工程技能](docs/工程技能.md)
- [版本补丁与导入核验](docs/EDIT_AND_VERIFY.md) / [可靠性说明](docs/RELIABILITY.md)
- [三菱与竞赛参考](docs/PLC竞赛与优秀案例参考_20261022.md) / [研究来源](docs/RESEARCH.md)
- [服务源码](src/gxworks2_mcp) / [测试](tests) / [示例](examples) / [验证工具](tools)

历史技术记录按各文档日期理解，当前支持范围以本页和对应工具的参数校验为准。

## 许可证与致谢

本项目代码采用 [MIT License](LICENSE)。复用的数据表与参考资料分别遵守其原许可证，详见 [第三方资料归属](third-party/README.md) 与 [NetToGXSim2 参考说明](third-party/NetToGXSim2-NOTICE.txt)。感谢相关开源项目提供的接口研究与公开资料。
