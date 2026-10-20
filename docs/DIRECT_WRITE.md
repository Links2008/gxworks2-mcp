# 无 GUI 直接写入三菱程序：可行性核实（DIRECT_WRITE）

> 目标：回答「AI 能不能不经窗口自动化，直接把程序写进 GX Works2 / PLC」。
> 核实时间：2026-10-19。标注：**确证** = 有一手来源（三菱官方手册/官方 FAQ）或本机实测复现；**未证实** = 找不到可引用来源，不要当事实用。
> 本机环境：GX Works2 **1.620W 中文版**，安装于 `D:\新建文件夹\GPPW2`（主程序 `GD2.exe`）。

---

## A. 三菱生态里有没有「不经 GUI 把程序写进 PLC」的官方途径？

### A1. MC 协议 / SLMP 是否含程序读写指令？

**结论：确证——MC 协议确实有文件级读写指令，而且文件类型表里明确列出了「顺序程序文件」（MELSEC-Q/L: `*.QPG`，iQ-R: `*.PRG`）。但官方同时明文写「改程序请用 Engineering tool」。**

**MC 协议指令表中与程序相关的部分（SH-080008 第 12 章 FILE CONTROL）**：

| 功能 | 指令 | 说明 |
|---|---|---|
| 读文件列表 | 1810 | Read directory/file information |
| 搜索文件 | 1811 | Search directory/file information |
| **新建文件** | **1820** | Create new file（仅登记文件名并预留容量） |
| 删除文件 | 1822 | Delete file |
| 复制文件 | 1824 | Copy file |
| 修改属性 / 时间 | 1825 / 1826 | Modify file attribute / date |
| **打开文件** | **1827** | Open file（加锁，禁止他设备访问） |
| **读文件** | **1828** | Read file |
| **写文件** | **1829** | Write to file |
| 关闭文件 | 182A | Close file |

**官方给出的「文件类型 → 扩展名」对照表（确证，原文）**：

```
File type                  MELSEC-Q/L   MELSEC iQ-R
Header statement file         DAT           DAT
Sequence program file         QPG           PRG      <- 程序文件
Device comment file           QCD           DCM
Device initial value file     QDI           DID
FB file                        -            PFB
```

**官方写文件的标准流程（确证，原文步骤 Procedure to overwrite files）**：

```
1. 1810 / 1811  确认文件存在
2. 1827  Open file   （加锁）
3. 1829  Write to file
4. 182A  Close file  （解锁）
```

新建文件时另有约束（原文）：临时文件扩展名**不能**用 DAT/PRG/QPG/PFB/QCD/DCM/QDI/DID；
iQ-R 的 `*.PRG` / `*.PFB` 必须先删除目标文件再新建。

来源：[官方手册] MELSEC Communication Protocol Reference Manual **SH-080008**，第 12 章 FILE CONTROL（约第 190-194 页）
PDF：https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh080008/sh080008ab.pdf

**但官方同时明确划了红线（确证，原文 12.2 Considerations）**：

> If the files such as **program files** and parameters which affect the system are read from the CPU module, keep the files for backup. **Do not edit the data in the file on an external device. If the programs or parameters are required to be changed, use an Engineering tool.**

以及：

> The executing file **cannot be modified while the CPU module is in RUN**. When data write or delete command is executed to a file being executed, **place CPU module in the STOP status**. If the command is executed during RUN, the command completes abnormally.

> **判定**：MC 协议**技术上能**写文件（含程序文件），但：
> 1. **格式是障碍**：QPG/PRG 是未公开的二进制格式（见 B 节），AI 无法凭空生成合法字节；
> 2. 官方明确要求「改程序用 Engineering tool」；
> 3. 写程序文件必须 **CPU 处于 STOP**。

**SLMP 侧**：SLMP Reference Manual（SH-080956）的指令表只有设备访问 / 标签访问 / 文件控制，
其中文件控制指令与 MC 协议同一套（1810/1811/1820/1822/1824/1825/1826/1827/1828/1829/182A）。
**没有**「下载梯形图 / 编译程序」这类高级指令。
来源：https://dl.mitsubishielectric.co.jp/dl/fa/document/manual/plc/sh080956eng/sh080956engm.pdf （第 5 章 Command List）

**结论（A1）**：**确证** —— 有文件级读写指令，无「结构化程序下载」指令；能不能真正用取决于能否生成合法 QPG/PRG（B 节结论：不能）。

### A2. FTP 服务器能否上传 / 下载程序文件？

**结论：FTP 功能存在且官方说「能读写文件」——确证；但「能否写程序文件并被执行」未证实。**

**确证部分（E71 官方手册原文）**：

> **File transfer (FTP server) function** —— Data can be **read/written** in files from connected devices using an exclusive FTP command.

来源：[官方手册] MELSEC-Q/L Ethernet Interface Module User's Manual (Basic) **SH-080009**，第 3.3.1 节功能表（约第 34 页）
PDF：https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh080009/sh080009x.pdf
该手册的 FTP 细节另见 MELSEC-Q/L Ethernet Interface Module User's Manual (Application) **SH-080010**（本次未取得全文）。

**FX5U**：官方手册明确列出 FTP server 与 FTP client 功能（CC-Link IE field network Basic, **FTP server, FTP client**, SNTP client, Web server…）。
来源：MELSEC iQ-F FX5 User's Manual (Ethernet Communication) **JY997D56201**
PDF：https://dl.mitsubishielectric.com/dl/fa/document/manual/plcf/jy997d56201/jy997d56201u.pdf

**未证实部分（重要）**：

- **FTP 写入程序文件后 CPU 是否认可并执行** —— **未证实**。没有找到官方明文，也没有实测记录。
- 在 Q/L 的 CPU 文件系统中，程序文件（`*.QPG`）是**编译后的中间代码**，不是文本；FTP 只是搬字节，不会触发「重新编译 / 校验」。即使写入成功，也可能出现「文件在、但 CPU 不认」的状态。
- iQ-R 的 `$MELPRJ$` 目录官方另有警告（见下），更不宜直接写。

**相关官方警告（确证，SH-080008 12.2）**：

> **Access for $MELPRJ$ folder of RCPU** —— The $MELPRJ$ folder of RCPU is the folder that controls the data written from an Engineering tool. **Do not access the $MELPRJ$ folder other than the purpose of data backup or restoration.** … If only a part of $MELPRJ$ folder is changed, **it may not operate properly**.

> **判定**：FTP 是「整包备份 / 恢复」通道，**不是**「写程序的通道」。
> 若一定要用，正确姿势是：**在别处生成完整工程 → 整体 FTP 覆盖 → 重启 / 复位 CPU**，而不是改单个文件。
> 而「别处生成完整工程」这件事，恰恰卡在 B 节（.gxw 无法可靠生成）。

### A3. 有没有「文件传输 / file transfer」相关的 SLMP 专用指令？

- **确证**：SLMP/MC 协议里与文件相关的是 **1810/1811/1820/1822/1824/1825/1826/1827/1828/1829/182A** 这一组（见 A1 表），没有单独的「file transfer」指令。
- **确证**：「FTP client」是 PLC 侧的功能（FX5U 支持 FTP client，可用指令主动 PUT/GET），但它是 **PLC 主动把文件推给外部**，方向对「AI 写进去」不合适。
- **未证实**：任何「通过 SLMP 直接下载已编译程序」的官方指令。

### A4. MX Component 的能力边界

**结论：确证 —— MX Component 是「设备读写 + 通信控制」库，不是编程 / 下载库。**

- 官方定义（三菱产品页）：MX Component is the ActiveX control/.NET control library enabling **communication from a PC to a programmable controller** … Complicated programs for serial and Ethernet communication can be developed with simple steps.
  来源：https://www.mitsubishielectric.com/fa/products/cnt/plceng/smerit/mx_component/index.html
- 其 API 面向**软元件读写、监视、通信设置**；官方 FAQ 只说明它能连 GX Simulator2 做调试（见 docs/RESEARCH.md C11 已引 FAQ 13399）。
- **未证实**：MX Component 提供「写程序 / 下载工程」的 API。本次检索未找到任何官方证据。在 MX Component Programming Manual（SH-081085 等）中，功能分类是 Device / Monitor / Communication，无 Program 类。

> **判定**：MX Component **不能**用来写程序。它只能改软元件值 —— 这不算「写程序」。

### A5. MELSOFT 有没有 CLI / 批处理接口？

**结论：未证实有公开 CLI。**

- GX Works2 / GX Works3 / iQ Works 均未见公开的命令行 / 批处理编译下载接口（本轮与上轮均检索不到；docs/RESEARCH.md A4 已记录「GX Works2 无命令行参数」）。
- GX Works2 目录下的 `GX Works2 FastBoot.exe` / `GX Works2 Progress.exe` / `GX Works2 Service.exe` / `GX Works2 Startup.exe` 是**自身启动与预加载组件**，不是对外 CLI。
- 相关但不同：MELSOFT 有 **MX Sheet / MX Data Logger** 等数据工具，与编译下载无关。

> **判定**：**没有**可用的官方 CLI。要自动化只能靠 GUI 驱动（即用户明确不想要的方案）或绕过 GUI 直接生成工程文件。

---

## B. .gxw 工程文件能否被程序直接生成 / 修改？

### B6. .gxw 的真实结构与内部 stream（**本机实测，确证**）

我用本机 GX Works2 保存了一个真实工程 `demo1.gxw`（919,040 字节）并解析：

```
magic       = d0 cf 11 e0 a1 b1 1a e1     -> OLE2 复合文档（CFB）
sector      = 512 字节
mini sector = 64 字节
FAT 扇区数  = 15
目录起始扇区 = 1

Root Entry 下的 stream：
    checkout.xml        378 字节
    dataprotection.xml  52,529 字节
    history.xml         28,124 字节
```

→ **确证**：.gxw 是 **OLE2 Structured Storage（Compound File Binary）**，与第三方文档说法一致；上述三个 XML stream 的名字也**逐一实测确认**。

**但关键点**：这三个 XML 都是**元数据**（数据库版本、保护信息、变更履历）。**梯形图 / 程序实体并不在这三个 XML 里**，而是在其他 stream 中，且是**专有二进制编码**。本次未进一步逆向（超出本轮时间范围）。

**参考**：第三方文档称程序数据在 Binary ladder data / Embedded in binary format；来源（二手）：https://deepwiki.com/ito-soft-design/ladder_converter/5.1-mitsubishi-gx-works2-projects

> **判定**：`.gxw` **能被程序读**（见 B7 的真实库），但**写**需要生成专有二进制梯形图编码，工程量大且无规格。

### B7. GitHub 上有没有真实可用的 .gxw 读写库？

| 仓库 | 能力 | 读写范围 |
|---|---|---|
| **monozukuri-ai/gxwlib** | Rust core + Python API，解析 .gxw | **只读（read-only）**：能读 stream、解码部分 simple-ladder 指令与 CSV、渲染 SVG/HTML、解析结构化 FBD、有限仿真 |
| **ito-soft-design/ladder_converter** | 梯形图转 Keyence | 通过 **CSV 导出**间接读，不直接读 .gxw |
| **Arienax/gxworks-agent** | MCP + 原生会话 | **声称**能读写 .gxw 中的 ST Program / ST FB / FBD（read_st_project / create_st_candidate / execute_native_engineering），但**依赖真实 GX Works2 进程**（见 D10） |

**gxwlib 原文（确证）**：

> The package is **read-only**. Unsupported data remains inspectable with diagnostics. Whole-project execution, structured POU execution and GX Simulator2 compatibility are not supported.

来源：https://raw.githubusercontent.com/monozukuri-ai/gxwlib/main/README.md ；仓库：https://github.com/monozukuri-ai/gxwlib

> **判定（B 节总结）**：截至本次核实，**不存在**公开的、能「从零生成合法 .gxw」的库。最强的是 gxwlib，但它**明确只读**。因此「AI 直接生成 .gxw 写进磁盘」**目前做不到**（未证实有任何可行实现）。

---

## C. 结构化工程（workspace 目录形式）能否直接写？

### C8. GX Works2 的 workspace 工程目录结构 / ST 是否以纯文本存放？

**确证部分**：

- GX Works2 有「workspace 形式」与「单文件（.gxw）形式」两种保存方式。**Ver.1.56J 以后**可以不用 workspace，直接用 1 文件形式的工程保存。来源：[官方FAQ] https://fa-faq.mitsubishielectric.co.jp/faq/show/14657 （「ワークスペース形式のプロジェクト保存について」）
- workspace 与单文件的差别是「工程的组织方式」，不是「语言」；GX Works2 的 ST 仍属于工程数据的一部分。是否以**明文 .st / .txt** 落盘 —— **未证实**。未找到任何官方文档或实测记录说明 ST 源码以纯文本形式独立存放。

**未证实且需警惕**：即使 workspace 目录里存在疑似文本文件，GX Works2 打开工程时读的**未必**是它；GX Works2 有内部数据库 / 缓存（checkout.xml 等元数据、以及工作区管理文件），**直接改目录里的文件，很可能被忽略或被覆盖**。

### C9. 直接往工程目录写 ST / 程序文件，GX Works2 会识别吗？

**未证实。找不到任何实测记录证明可行。**

相反方向的旁证：Arienax/gxworks-agent 虽然有「GXW 原生 ST 工程读写」，但它的实现方式是**启动一个真实的 GX Works2 进程并驱动它**（见 D10），**不是**直接改文件。这本身就是「直接改文件行不通」的强旁证。

> **判定**：C 节两条（C8 的 ST 明文、C9 的直接写目录）**都不成立 / 未证实**，不应作为方案。

---

## D. 现有 MCP / 自动化项目怎么做的？

### D10. Arienax/gxworks-agent（GX Works2 的 MCP server）

**它用的方案（确证，来自其 README）**：

1. **产物层**：生成 Ladder JSON / PLC IR / SVG / 由 IR 派生的 ST / **GX Works2 导入用 CSV**（MAIN + 软元件注释）。→ **和我们已经验证成功的 CSV 路线一致**（见 docs/RESEARCH.md E3）。
2. **交付层**：`execute_native_engineering(mode="live")` —— **在同一个持续打开的 GX Works2 进程里**跨多次调用推进修改。明确要求：「原生工程会话要求 **Windows 可交互桌面**、GX Works2 1.635.0.1 以及操作员配置的执行许可」。
3. **它抄了 / 绕过了什么**：抄的 —— CSV 导入格式、ST 文本语法、软元件模型；绕过的 —— 不自己解析 .gxw 二进制，而是**让 GX Works2 自己去读**；不试图「直接写 PLC」，而是产出工程产物 + 驱动原生进程。

来源：https://github.com/Arienax/gxworks-agent （README.md / README.zh-CN.md）；raw：https://raw.githubusercontent.com/Arienax/gxworks-agent/main/README.zh-CN.md

> **对我们的意义**：即使是有 42 个工具、专门做这件事的项目，**最终交付仍然要靠「可交互桌面 + 真实 GX Works2 进程」**。也就是说，**纯无 GUI 写 GX Works2 这条路，连最成熟的同类项目都没走通**。

### D11. Bibin-VR/GXBridge-MCP（GX Works3）

**未证实**：本次未能取得该仓库内容（GitHub raw 匿名访问被限流 / 阻止，jina 代理返回 401）。不对其方案做任何断言。

**关于「GX Works3 是否比 GX Works2 更好自动化」——未证实**。已知：GX Works3 同样没有公开 CLI；其工程为文件夹形式（.gx3 是目录），理论上比单文件 OLE2 **更可读**，但**没有任何来源证明可直接写文件让 GX Works3 认可**。

### D12. 还有别的「无 GUI 写三菱程序」的仓库吗？

本轮 GitHub 检索（melsec program write slmp 等）**没有**发现实现「无 GUI 直接写程序」的项目。找到的都是：读工程（gxwlib，只读）、转格式（ladder_converter，走 CSV）、MCP 包装（gxworks-agent，驱动 GUI）。

---

## 结论：在当前机器上，哪条路最可能真正做到「AI 直接写入、不用 GUI」？

### 先说不能走的路（都有确证或强旁证）

| 路 | 结论 | 依据 |
|---|---|---|
| 直接生成 .gxw 文件 | **不可行** | OLE2 + 专有梯形图二进制；唯一成熟库 gxwlib 明确只读（B7） |
| 直接改 workspace 目录里的 ST | **未证实，且大概率被忽略** | C8/C9；同类项目也不这么做（D10） |
| MX Component 写程序 | **不行** | 只提供设备读写 / 通信控制（A4） |
| 官方 CLI 批处理编译下载 | **不存在** | A5 |
| MC/SLMP 直接下载程序 | **技术上能写文件，但格式不可生成** | 有 1829 写文件、QPG/PRG 类型（A1），但 QPG 是未公开二进制 |
| FTP 写程序文件 | **能搬字节，但 CPU 是否认可未证实** | A2；且 RCPU 的 $MELPRJ$ 官方警告不要碰 |

### 再说最可能成功的路

**排序（可行性从高到低）：**

**① 生成 GX Works2 原生可导入的 7 列程序 CSV，由用户 / 脚本一次性导入**

- 这是**我们已经亲手验证成功**的（docs/RESEARCH.md E3：CSV -> 梯形图 -> F4 编译 -> 保存 .gxw，步数 2 变 7，梯形图真实绘制）。
- 「不用 GUI」的部分：**CSV 的生成完全可以无 GUI**（纯文本，AI 直接产出）。
- 只有**最后一步导入**需要 GX Works2。若接受「一条命令导入」，可用 UIA 的 `invoke()` 完成 —— **不需要鼠标、不需要前台焦点**（本轮已验证）。

**② 若能换 PLC 为 iQ-R/iQ-F 且接受整包备份 / 恢复：FTP 整体覆盖**

- 但「先有完整工程包」仍绕不开 ①；且「写进去能不能跑」未证实。**不建议作为首选**。

**③ MC 协议只做「验证 / 调试」**

- 用 plc_read / plc_write 读写软元件、远程 RUN/STOP，确认程序逻辑正确 —— 这部分**完全无 GUI**，且已实现。
- 但**它不负责「把程序写进去」**。

### 可实测的验证步骤（30 分钟内可完成）

```
步骤 1  纯无 GUI：用仓库代码生成 CSV
        py -c "sys.path.insert(0,'src'); from gxworks2_mcp.csvio import write_program_csv; ..."
        验证：文件为 UTF-16 LE + BOM、Tab 分隔、7 列、第二行是「PLC信息:」
        -> 这一步 100% 不碰 GUI，可以确证。

步骤 2  半自动（无鼠标、无前台焦点的 UIA）：导入并编译
        Application(backend='uia').connect(handle=帧窗口)
        menuitem('编辑(E)').expand()  ->  menuitem('从CSV文件读取').invoke()
        文件对话框 set_edit_text(csv路径) -> 点「打开」-> 确认「是」
        验证：标题栏步数变化（如 2步 -> 9步）
        -> 本轮已验证这套 UIA 调用可行（docs/RESEARCH.md E3(6)）。

步骤 3  纯无 GUI 复核：MC 协议读回
        plc_read('M0-M15, D0-D9') 确认程序产生的值
        -> 这一步也 100% 不碰 GUI。
```

### 一句话结论

> **在 GX Works2 1.620W + 有网口的 PLC 这台机器上，「完全不用 GUI、由 AI 直接写程序」目前没有可行路径**。
> 三菱生态里唯一完整可行的是「**AI 无 GUI 生成 CSV -> 自动化导入 GX Works2（UIA invoke，非鼠标）-> 无 GUI 用 MC 协议验证**」。
> 若必须做到字面意义的「零 GUI」，唯一现实出路是**更换目标平台**：换用支持程序下载的第三方运行时（如 CODESYS / 其他品牌），或等三菱开放编程接口 —— **这不是本项目能在 GX Works2 上解决的**。

---

## 参考来源汇总

**三菱官方手册**

- MELSEC Communication Protocol Reference Manual **SH-080008**（第 12 章 FILE CONTROL、12.2 Considerations）https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh080008/sh080008ab.pdf
- SLMP Reference Manual **SH-080956**（Command List，无程序下载指令）https://dl.mitsubishielectric.co.jp/dl/fa/document/manual/plc/sh080956eng/sh080956engm.pdf
- MELSEC-Q/L Ethernet Interface Module User's Manual (Basic) **SH-080009**（FTP server 功能表）https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh080009/sh080009x.pdf
- MELSEC iQ-F FX5 User's Manual (Ethernet Communication) **JY997D56201**（FX5U 的 FTP server/client）https://dl.mitsubishielectric.com/dl/fa/document/manual/plcf/jy997d56201/jy997d56201u.pdf

**三菱官方 FAQ / 官网**

- FAQ 14657 workspace 形式与单文件形式（1.56J+）https://fa-faq.mitsubishielectric.co.jp/faq/show/14657
- MX Component 产品页（ActiveX/.NET 通信库）https://www.mitsubishielectric.com/fa/products/cnt/plceng/smerit/mx_component/index.html

**GitHub**

- monozukuri-ai/gxwlib（.gxw 只读解析库）https://github.com/monozukuri-ai/gxwlib ；https://raw.githubusercontent.com/monozukuri-ai/gxwlib/main/README.md
- Arienax/gxworks-agent（MCP；CSV 产物 + 原生会话驱动 GUI）https://github.com/Arienax/gxworks-agent ；https://raw.githubusercontent.com/Arienax/gxworks-agent/main/README.zh-CN.md
- ito-soft-design/ladder_converter（走 CSV，不直接读写 .gxw）https://github.com/ito-soft-design/ladder_converter

**本机实测（本次）**

- .gxw OLE2 头与 Root Entry stream 列表：见 B6 的实测输出。
- CSV -> 梯形图 -> 编译：见 docs/RESEARCH.md E3。
- UIA expand() / invoke() 无需前台焦点可驱动菜单：见 docs/RESEARCH.md E3(6)。
