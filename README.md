# gxworks2-mcp

MCP 服务器：让 AI 编写 **FX3U 梯形图与 SFC 步进程序，直接输出原生 `.gxw`**。
独立目录为 `E:\trae_projects\gxworks2-mcp`。原生写入不启动 GX Works2，不调用
computer use、鼠标、键盘、截图或浏览器；软件打开、编译、实体 PLC 验证分别验收。

> 定位：**给 AI 一个「写三菱 PLC 程序」的可校验管道**——把结构化逻辑树编译成 MELSEC 助记符程序，
> 做静态校验，编码为原生程序流，写入新的 `.gxw`；自动读回源码并校验其他数据流不变。
>
> ⚠️ 一个必须说清的事实：**GX Works2 的官方编程语言只有 梯形图 / 结构化梯形图·FBD / SFC / ST，
> 没有 IL**（它声明符合 IEC 61131-3，但只实现了其中 4 种）。所以 IL 不是 GX Works2 的源语言，
> 只是它「回路块列表显示/编辑」与「写入至 CSV 文件」的**呈现形式**。本工具产出的 IL/CSV 需要经由
> 这两条通道也能带进 GX Works2。新的 `gx_write_project` 直接写二进制源码，不经过 CSV 导入。

## 快速编写与顺启逆停示例

```python
gx_program_create(name="motor", cpu="FX3U", rungs=[{"out": "Y0", "logic": "X10"}])
gx_program_validate(name="motor")
gx_write_project(name="motor", path=r"E:\工程序列\motor.gxw", confirm=True)
gx_gxw_open(path=r"E:\工程序列\motor.gxw")
```

输出目录须存在，文件须为新文件；工具不会覆盖模板或已有工程。默认使用附带的 FX3U
空白梯形图模板。已有工程需是已验证的简单梯形图源码格式，可显式指定 `template_path`
和完整 `block` 名称。只修改该源码流及对应历史元数据，其他所有数据流逐字节核验。
`requiresCompile=true` 表示生成的是原生源码，必须在 GX Works2 编译后才能用于下载。

`examples/fx3u_sequence.py` 提供完整三路顺启逆停示例：X0 上升沿启动、X1 停止优先；
Y0→Y1→Y2，每段 1 秒；逆停从最后已启动的一路开始立即关闭，再每隔 1 秒关闭下一路。
启动途中可停止，停止过程中忽略启动，持续按住启动不会在停止后自动重启。

```powershell
py examples/fx3u_sequence.py E:\工程序列\顺启逆停
```

生成普通梯形图与 STL/RET 顺控两份 `.gxw`、对应 `.il` 和步骤 JSON。
测试包含独立扫描执行器的时序与边界检查；执行器仅覆盖示例使用的基本指令及 T0..T3，
不等于实体 PLC 或完整 GX Simulator2。软件验收边界见 `docs/原生写入与验证.md`。

## 它解决什么问题

工程文件 `.gxw` 是二进制复合文档，目前提供以下通路：

0. **原生源码写入（默认）**：FX3U 梯形图或结构化顺控 → 校验 → 编码 → IStorage 封装 →
   源码与非目标流读回校验。不依赖 GX Works2 是否运行；Windows 需要 pywin32。

1. **文本/CSV 路线（写程序）**：GX Works2 有两条「助记符形态」的官方通道——
   「回路块列表显示/编辑」（Ver.1.86Q+）与「写入至 CSV 文件」（7 列、UTF-16 LE、Tab 分隔）。
   本服务器把「结构化逻辑树 → MELSEC 助记符程序 → 静态校验 → CSV」这条链做完整，并把
   **CSV → GX Works2 的导入做成了可自动化的已验证通路**（gx_import_csv，UIA expand()/invoke()，
   无需鼠标/前台焦点，用标题栏步数变化验证生效）。
3. **无软件直读 .gxw（新主线）**：gx_gxw_inventory / gx_gxw_open / gx_gxw_comments 用纯
   Python（olefile）直接打开 .gxw——外层 OLE2 + 内嵌 _hdb OLE2 + diffgram XML 数据清单
   （iID==内层流号，MAIN.res/POU/任务/参数全部定位），Q 系列的 MAIN.res 程序体已能解码回 IL
   （语法经「写入→保存→读回」闭环逐字节对撞验证，见 re-evidence/GRAMMAR.md；
   未知构造一律停止并报 stopReason，绝不编造指令）；
   简单梯形图源码和部分网络记录可解码；任何不完整网络均返回 stopReason 和 partialIl，
   不会被物化成可覆盖原工程的完整源码。原生 SFC 图形布局仍需继续解析。
2. **MC 协议路线（连 PLC）**：QnA 兼容 3E 帧，字节序/设备码/位打包已与参考实现逐字节比对，
   并已按三菱官方手册 SH-080008 校验。直接读写软元件、远程 RUN/STOP/PAUSE/latch clear。

第 1 条**不依赖 GX Works2 是否安装**；第 2 条只需要 PLC 的网络端口。

## SFC（顺控程序）支持

GX Works2 的 FX 系列顺控程序在指令表里就是 **STL 步进梯形**（`STL S0 … RET`），
本服务器全链路支持：

- `gx_program_create(name, cpu, il='STL S0\nOUT Y10\nLD X10\nOUT S8\nRET\n…\nEND')`
- 支持 `sfc={initial, preamble, steps:[{state, actions, transitions}]}` 结构化生成。
- 定时器动作使用 `{out:"T0", preset:"K10"}`；条件复用梯形图 and/or/not/rise/fall。
- 校验（gx_program_validate）→ 原生写入（gx_write_project）→ 源码读回（gx_gxw_open）
- 从 .gxw 解码回来的顺控程序、以及新建的顺控程序，都能用
  gx_where_used / gx_trace_device（转移条件追踪）/ gx_lint_program 分析

SFC 语言编辑器（图形块）的原生 .pou 记录格式仍在逆向边界内，见 re-evidence/GRAMMAR.md。

## 安装

需要 Python 3.10+。

```bash
cd gxworks2-mcp
py -m pip install -e ".[native,dev]"     # Windows 原生写入与测试
# 界面自动化只在明确需要时安装 .[ui]，原生写入不需要它
```

验证：

```bash
py -m pytest -q                          # 离线测试，不需要 PLC / GX Works2
py tests/test_stdio_e2e.py              # 真实 MCP 握手、梯形图/SFC 原生写入与读回
```

## 接入 DSH（DeepSeek Harness）

DSH 用 @deepseek-ai/dsh-mcp-client 桥接外部 MCP 服务器，每台服务器一条配置项，
工具以 mcp__<serverName>__<tool> 命名。把下面这段加到 DSH 的配置层
（~/.dsh/cordis.patch.yml，或你的 profile 的 cordis.patch.yml）：

```yaml
- id: mcp-gxworks2
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: gxworks2
    transport: stdio
    command: py
    args: ['-m', 'gxworks2_mcp']
    cwd: E:/trae_projects/gxworks2-mcp
    env:
      PYTHONPATH: E:/trae_projects/gxworks2-mcp/src
      PYTHONIOENCODING: utf-8
      GXWORKS2_MCP_HOME: E:/trae_projects/gxworks2-mcp/.mcp-cache
    toolCallTimeoutMs: 120000
```

重启 DSH 后，模型就能看到 mcp__gxworks2__gx_program_create 等工具。

> 注意：command 用绝对路径的 python 更稳（py 是启动器）。插件配置请写在 profile 的
> cordis.patch.yml，不要写进安装目录。

## 工具一览

### 写程序（核心）

| 工具 | 作用 |
|---|---|
| gx_program_create | 建工程 + 用「逻辑树」或 IL 初始化程序 |
| gx_write_project | 无窗口直接输出新的 FX3U `.gxw`，自动读回并逐流校验 |
| gx_program_add_rungs | 追加梯形图行（逻辑树自动编译成 IL） |
| gx_program_replace | 用整段 IL 覆盖程序，返回 diff 与校验结果 |
| gx_program_read | 读回 IL / ASCII 梯形图 / 布尔逻辑 / 结构 |
| gx_program_validate | 结构+软元件校验（线圈重复、ANB/ORB 不配对、越界、跳转缺标号…） |
| gx_program_export | 导出可直接粘贴的 IL、ASCII 梯形图或 JSON |
| gx_program_import_csv | 读入 GX Works2 导出的程序 CSV（CSV → IL，不是 CSV → 梯形图） |
| gx_gxw_inventory | **无软件直读 .gxw**：容器/内层流/数据文件清单（iID→流号），程序正文记录锚点 |
| gx_gxw_open | 深读 .gxw + 物化为 store 工程（raw.bin + gxw_manifest.json，程序体解码进度如实上报） |
| gx_gxw_comments | 从 .gxw 提取 label 表注释（COMMENT.qcd 为带口令 Jet DB，诚实标注未解） |
| gx_program_export_csv | 输出 GX Works2 形状的程序 CSV（UTF-16 LE / Tab / 7 列） |
| gx_project_list / gx_device_usage / gx_device_describe | 工程列表、软元件引用清单、地址解释 |
| gx_where_used | 交叉引用：每个软元件在哪被写/被读（分析思路取自 gx3-cli-mcp） |
| gx_trace_device | 「这个线圈为什么/怎么才能 ON」：递归展开到 X/边界输入，环与深度显式报告 |
| gx_lint_program | 静态审查：多线圈驱动、只写不读的死逻辑 |

### 送进 GX Works2（可选，Windows）

| 工具 | 作用 |
|---|---|
| gx_workspace_status | GX Works2 是否运行、UI 自动化是否可用 |
| gx_import_csv | **已实测通路**：UIA 驱动 编辑→「从CSV文件读取」，导入 CSV 重建梯形图（无需鼠标/前台焦点；需 confirm=true，整块替换） |
| gx_compile_csv | UIA 触发 转换/编译（F4） |
| gx_paste_ladder | 遗留：剪贴板粘贴 IL —— 1.620W 实测**无效**，保留仅供参考 |
| gx_save_project | Ctrl+S |
| gx_open_project | 打开 .gxw |

### 直连 PLC / 模拟器（MC 协议 3E 帧）

| 工具 | 作用 |
|---|---|
| plc_probe | 探测 host:port 是否是 MC 协议端点 |
| plc_read | 读 D/M/X/Y/T/C/ZR 等（支持 D100-D109, M0-M15） |
| plc_write | 写软元件（需 allow_write=true） |
| plc_remote_control | 远程 RUN / STOP / PAUSE / LATCH CLEAR |

## 模型怎么用（示例）

> 「写一个三菱 Q 系列程序：X0 启动、X1 停止，Y0 自锁输出；X2 报警时 Y1 常亮。」

模型会这样调用：

```json
{"name": "gx_program_create",
 "arguments": {
   "name": "line1",
   "cpu": "Q",
   "rungs": [
     {"out": "Y0", "comment": "电机自锁",
      "logic": {"or": [{"and": ["X0", {"not": "X1"}]}, {"and": ["Y0", {"not": "X1"}]}]}},
     {"out": "Y1", "comment": "报警灯", "logic": {"and": ["X2", {"not": "X1"}]}}
   ]}}
```

得到：

```
LD X0
ANI X1
LD Y0
ANI X1
ORB
OUT Y0  // 电机自锁
LD X2
ANI X1
OUT Y1  // 报警灯
END
```

和 ASCII 预览：

```
|[X0]----[/X1]----(Y0)---|
|[Y0]----[/X1]----(Y0)---|
|~~~~----[END]
```

## 逻辑树语法

```json
{"and": ["X0", {"not": "X1"}, {"rise": "M0"}, {"fall": "M1"}]}
{"or":  [{"and": ["X0", "X1"]}, {"and": ["X2", "X3"]}]}
```

- and / or 可嵌套；数组第一个元素生成 LD/LDI，后续元素生成 AND/ANI/ANDP/ANDF 或 OR/ORI/ORP/ORF；
- 嵌套超过一层时自动插入 ANB/ORB 块连接；
- 操作数：X0、{"not": "X1"}（常闭）、{"rise": "M0"}（上升沿）、{"fall": "M1"}（下降沿）。

助记符严格按 MELSEC/GX Works2 写法：常闭与 = ANI（不是 ANDI），常闭或 = ORI，
上升沿 = ANDP/ORP，下降沿 = ANDF/ORF。

## 数据放在哪

默认 ~/.gxworks2-mcp/projects/<工程名>/：

```
program.il      # 当前程序（指令表）
program.json    # 结构快照：rungs、软元件、ASCII、校验结果
devices.csv     # 软元件注释（可导入 GX Works2）
```

可用环境变量 GXWORKS2_MCP_HOME 改根目录。

## 已知边界（重要）

- **解析范围按程序体报告**：简单梯形图源码可完整读回；结构化网络和原生 SFC 图形遇到
  未知结构立即停止，不把 partialIl 作为完整程序保存。应使用 `.Program.pou` 读取当前源码；
  `.res` 可能是上次编译缓存，源代码修改后两者不一定一致。
- **默认写入为无窗口 GXW 源码通路**：只创建新文件，拒绝覆盖；其他流逐字节核验。
  编译缓存仍需由 GX Works2 更新。原生 SFC 图形画布的直接新建尚未实现。
- **旧 CSV 通路保留为备选**：1.620W 实测 Ctrl+V 无效（gx_paste_ladder 遗留）。
  gx_import_csv 驱动 编辑→「从CSV文件读取」：实测梯形图 2 步变 7 步、F4 编译无报错。
  官方对话框明说导入是**整块替换**，一个 CSV = 一个完整程序块，confirm=true 才执行。
- **CSV 导入是「实测可用」而非「官方承诺」**：官方 FAQ 只承诺设备注释 CSV 双向（1.64S+），
  对程序 CSV 导入没有任何书面承诺——但 UI 与功能在本机 1.620W 中文版实测成立。列头按语言变化
  （中文「步号/指令」，日文「ステップ番号/命令」），gx_program_export_csv 的 locale 参数控制。
  导入后务必执行 程序检查（F4 + gx_program_validate 复核）。
- **软元件限制**：MC 外部通信的 S 步进继电器写入被拒绝；PLC 程序内的 SET S/STL S 正常支持。
  FX3U 原生源码的 X/Y 严格按八进制处理，X8、Y9 等直接报错；原 MC 客户端仍使用 Q/L 规则，
  本轮未扩展 FX3U MC 寻址，不能拿它的 Q/L 数字解释直接驱动 FX3U。
- **GX Simulator2 不能直连 MC 协议**（三菱 FAQ：需 MX Component；模拟器是隔离进程），
  所以 plc_probe 对着模拟器端口连不上属于预期。
- **远程 RUN 不可只看结束码**：三菱手册说明 CPU 的 RUN/STOP 物理开关在 STOP 时，
  远程 RUN 仍返回正常完成。因此 plc_remote_control 在 run/stop 后会**回读 SM0** 确认状态。
- **MC 协议写入**默认被拦截（allow_write=false），因为写错地址会真的动设备。
- 设备代码表按 QnA 兼容 3E 帧整理；FX 系列走同一套代码，个别机型扩展区需按手册核对。
- 未实现：在线监视、ST 生成、原生 SFC 图形画布新建、通用厂商编译器替代。
  已实现 FX3U 简单梯形图和 STL/RET 顺控的原生源码写入。

## 事实出处

设计与协议细节都标注了来源，见 [docs/RESEARCH.md](docs/RESEARCH.md)（每条事实附 URL 与
「确证 / 未证实」分级；依据 SH-080008、SH-080956 两本官方手册与多个官方 FAQ）与
[docs/GXWORKS2_NOTES.md](docs/GXWORKS2_NOTES.md)（设计取舍）。

MC 帧与设备码另有一份可重复的交叉验证脚本：

```bash
py -m pip install pymcprotocol      # 仅用于交叉验证，不是运行时依赖
py tests/crosscheck_pymcprotocol.py # 逐字节比对帧 + 逐个比对设备码
```

## 开发

```
src/gxworks2_mcp/
  devices.py     软元件解析 + MC 设备码
  il.py          指令表解析/渲染
  blocks.py      IL → 梯形图逻辑还原（ANB/ORB/MPS/MRD/MPP）
  author.py      逻辑树 → IL 编译器
  validate.py    程序校验
  store.py       工程存储
  mcprotocol.py  MC 协议 3E 帧客户端（小端，1 字节设备码）
  csvio.py       GX Works2 程序 CSV / 设备注释 CSV 读写
  ui.py          GX Works2 窗口自动化（可选）
  compat.py      MCP SDK 1.x / 2.x 兼容层
  server.py      MCP 工具定义
tests/           离线回归、顺控扫描时序、原生 GXW 写入与 stdio 端到端验证
```

## License

MIT
