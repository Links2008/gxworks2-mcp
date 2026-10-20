# GX Works2 接缝：官方已确证的事实与刻意的取舍

本文记录设计依据，全部来自三菱官方文档，逐条出处见 [RESEARCH.md](RESEARCH.md)。
分级：**官方** = 三菱手册 / 官网 / FAQ 原文；**源码** = 可对照的第三方实现；
**未证实** = 没有一手来源，代码里按「可降级、不硬编码」处理。

## 0. 一个必须先说的定位事实

**GX Works2 的官方编程语言是 梯形图 / 结构化梯形图·FBD / SFC / ST —— 没有 IL。**
它声明符合 IEC 61131-3（该标准定义 5 种语言，含 IL），但只实现了 4 种。

所以「用 IL 写 GX Works2 程序」这个说法不成立：**IL 不是它的源语言**，只是
「回路块列表显示/编辑」（Ver.1.86Q+）和「写入至 CSV 文件」这两条官方通道的**呈现形式**。

本项目的正确定位因此是：

> 生成 MELSEC 助记符程序 → 静态校验 → 导出成 GX Works2 官方通道能吃下的文本/CSV。

`gx_paste_ladder`（往梯形图窗口粘贴）**没有官方依据**，已降级为实验特性，
默认路径是导出 + 人工/官方通道导入。

## 1. GX Works2 的三条事实通道

| 通道 | 官方依据 | 我们的工具 |
|---|---|---|
| 回路块列表显示/编辑（助记符形态，1.86Q+） | FAQ 14643 / 38649 | 导出 IL（`gx_program_export`） |
| 写入至 CSV 文件（7 列） | 真实导出样例 + 多项目复现 | `gx_program_export_csv` / `gx_program_import_csv` |
| 设备注释 CSV 双向（1.64S+） | FAQ 22643 | `devices.csv`（随工程写出） |

**官方口径的两条边界**：

- 设备注释 CSV 是**双向**的（1.64S+），这是唯一被官方承诺的往返。
- 程序/参数**没有** GX Converter 那类转换功能（FAQ 22643 / 14543）；程序 CSV 的**导出**入口存在，
  **导入**无官方文档 → `gx_program_import_csv` 的方向是 CSV → 我们的 IL 模型，不是 CSV → 梯形图。

程序 CSV 格式（来自真实导出样例）：UTF-16 LE + BOM、Tab 分隔、全字段加引号、7 列
`步号 / 行间语句 / 指令 / 操作数 / 空 / PI语句 / 注释`；无操作数的指令（ANB/ORB/MPS/MRD/MPP）
操作数列为空；步号可以不连续（部分指令占 2 步）。

## 2. MC 协议：官方手册确证的四条

来源：MELSEC Communication Protocol Reference Manual **SH-080008**（500 页）、
SLMP Reference Manual **SH-080956**（244 页）。代码里另有 `tests/crosscheck_pymcprotocol.py`
做逐字节回归。

1. **字节序**：只有子头 `50 00` / `D0 00` 是大端，其余全部小端；请求长度从监视定时器起算，
   响应长度从结束代码起算。
2. **设备引用**：Q/L/QnA 二进制 = 设备号 3 字节（小端）+ 设备码 **1 字节**；iQ-R/iQ-L =
   4 字节号 + 2 字节码。ASCII 帧才是 2 字符设备码。
3. **位打包**：每个点占 **4 bit**（不是 1 bit）——`byte = (b[2k] << 4) | b[2k+1]`，
   奇数点时最低 4 位补 0。官方例子 M10–M14 为 ON/OFF/ON/OFF/ON → `10 10 10`。
   已作为 golden 断言写进测试。
4. **点数上限**：字单位 ≤ 960；位单位 E71 以太网 ≤ 7168、C24 串行 ≤ 3584、ASCII ≤ 7904。
   （上一版资料里的「950」是错的。）

补充：S（步进继电器）= `0x98` 且**只读**，`plc_write` 会直接拒绝；Z = `0xCC`。
X/Y/B/W/SB/SW/DX/DY/ZR 十六进制寻址，其余十进制。

## 3. 两个会让人误判的坑

**远程 RUN 的成功响应不等于真的 RUN。** 官方说明：CPU 的 RUN/STOP 物理开关在 STOP 位置时，
远程 RUN 仍返回正常完成，但 CPU 不会启动。所以 `plc_remote_control` 在 run/stop 之后
**回读 SM0**（CPU 运行状态继电器）并在返回值里给出 `verified` 字段。

**C24 串行二进制会插 DLE(10H)，E71 以太网不会。** 本项目只走 TCP，不受影响；
将来支持串行需要另写分帧逻辑。

## 4. 模拟器与端口

- **GX Simulator2 不能直连 MC**（三菱 FAQ：走 MX Component；模拟器是隔离进程，
  5000/6000 是第三方桥接器自己开的端口）。`plc_probe` 打到模拟器连不上属于预期。
- **端口不是协议规定**：由模块参数「打开设置」决定。Q 系列内置以太网口允许
  1025–4999 / 5010–65534（5000–5009 被排除）。本工具默认 5000（社区惯例），可改。

## 5. 本机环境（已核实）

| 项 | 值 |
|---|---|
| GX Works2 | 1.620W，根目录 `D:新建文件夹GPPW2`，主程序 `GD2.exe` |
| GX Simulator2 | `D:新建文件夹GPPW2GX Simulator2` |
| Python | 3.13.4 |
| MCP SDK | 2.3.0（`MCPServer`；1.x 叫 `FastMCP`，已做兼容层） |
| pywinauto | 0.6.9 |

## 6. 仍未证实 / 需实测

1. GX Works2 **中文版**菜单文案（「回路块列表显示/编辑」「写入至 CSV 文件」的译法）。
2. 「粘贴 IL → 自动生成梯形图」是否成立（决定 `gx_paste_ladder` 存废）。
3. 「CSV → 梯形图」导入是否可行（官方未承诺）。
4. ~~`.gxw` 前 8 字节 OLE2 魔数~~ **已确证**：本机多个真实 .gxw 前 8 字节 = `D0 CF 11 E0 A1 B1 1A E1`（且 _hdb 是第二层 OLE2）。
5. 结束码 `C05B` 等的确切含义（官方 MC/SLMP 手册都指向模块手册）。
6. `0406/1406` 在 CPU 内置以太网口上是否可用（字段结构已确证，可用性未实测）。
7. X/Y 八进制 vs 十六进制的判定与 CPU 参数的关系（`octalInputOutput` 是可配置项）。

## 7. 直接写入路径（2026-10-19 补充，本机实测）

用户目标是「AI 不经 GUI 直接写入」。逐一排查：

| 路径 | 结论 |
|---|---|
| MC/SLMP 写程序文件（1820/1827/1829/182A，QPG/PRG） | 指令存在，但 QPG 是未公开编译二进制；官方原文禁止外部编辑程序文件（要改用 Engineering tool），且 CPU 必须 STOP |
| FTP 搬程序文件 | 能搬字节；写入后 CPU 是否认可 **未证实** |
| MX Component / MELSOFT CLI | 只能读写软元件 / 无 CLI |
| 读写 .gxw | **读取已部分实现**：外层 OLE2 + 内嵌 _hdb OLE2 已由 gxw.py（纯 Python/olefile）解析，diffgram 的 iID==内层流号，MAIN.res/POU/tsk/qcd 全部定位；FX 结构化 POU 锁定 28 字节记录头，锚点能精确显示“一次已知编辑只改第一个记录 size 113→92”。**指令语法仍在逆向**（re-evidence/README.md，配 REA），故 programBodiesDecoded 如实为 0；**写入未解锁**，唯一成熟库 gxwlib 明确只读 |
| 粘贴 IL 进梯形图 | 1.620W 实测无效 |
| **CSV → 编辑「从CSV文件读取」（UIA invoke）** | **实测成立**：2步→7步、梯形图真实绘制、F4 编译无报错；UIA expand()/invoke() 无需鼠标、无需前台焦点 |

**gx_import_csv 采用的不是「computer use」**（不截图、不坐标点击、不抢焦点），而是
**应用可编程性**：GX Works2 的自绘菜单在 UIA 树里以 MenuItem 暴露 Invoke/ExpandCollapse
模式，工具按名字找到「从CSV文件读取」并 invoke()，等同程序化调用菜单命令；对话框、确认、
步数校验（标题栏 2步→7步）全是确定性逻辑。除这一次进程内 invoke 外，生成/校验/编译触发/
保存/PLC 读回验证都不依赖 GUI。

## 8. 未实现（路线图）

- 0406/1406 复数块批量读写（字段结构已知，见 RESEARCH.md B8b）；
- 在线监视（monitor 0801）、时序图；
- ST / SFC / FBD 生成（这些才是 GX Works2 的原生语言）；
- .gxw 指令正文语法逆向 + 写回（容器/清单/记录锚点已完成，见 re-evidence/）；
- 梯形图反编译成逻辑树（目前只有 IL → 逻辑树单向）。

## 9. 2026-10-20 闭环达成（本轮）

- gx_import_csv 用 PID 级 Win32 对话框驱动（#32770 顶层枚举 + 1148 文件名框 WM_SETTEXT + id-1 按钮 BM_CLICK）+ 内嵌「是(Y)」确认，实测完成 CSV→梯形图导入（步数 2→28），F4 编译、保存落盘；
- 保存后的真实 .gxw 即 ground truth：main_res_program 把它逐 token 解码回与写入时完全一致的 IL（含 motor/alarm/flag 注解与 LDP X2），coverage 1.0；
- 于是形成真正双向：AI 写 IL → CSV → GX Works2 编译保存 → 无软件读回 IL → 继续编辑。写入语法（保存时 4 处长度字段/校验）未做，不冒充。