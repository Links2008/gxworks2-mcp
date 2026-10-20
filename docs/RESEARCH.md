# GX Works2 / MC 协议 资料核实清单

> 用途：为 gxworks2-mcp 的实现提供可直接落代码的事实依据。
> 核实时间：2026-10-18（第二轮：已取得三菱官方手册原文，多处结论升级/更正）。
> 可信度：**[官方手册]** = 三菱原厂手册原文；**[官方FAQ/官网]** = 三菱官网；**[源码]** = 可对照的开源实现；**[二手]** = 第三方整理，需实测。
> **本次新增关键证据**：《MELSEC Communication Protocol Reference Manual》(SH-080008, 500 页) 与《SLMP Reference Manual》(SH-080956, 244 页) 全文已抓取，下列 B 节绝大部分结论已从「源码推断」升级为「官方原文」。

---

## ⚠️ 重要更正（相对第一版结论）

| # | 第一版结论 | 更正后 | 依据 |
|---|---|---|---|
| 1 | 设备 `S`、`Z` 代码「未证实」 | **S = 98H（只读）、Z = 0CCH** | [官方手册] SH-080008 第 35 页设备代码表；SH-080956 同表 |
| 2 | `0406/1406` 字段结构「未证实」 | **已完整确证**（字数块数 + 位数块数 + 逐块 设备码/设备号/点数） | [官方手册] SH-080956 p.69–74 |
| 3 | 点数上限「两来源冲突（960 vs 950）」，未定 | **字 1~960 点；位：E71 二进制 1~7168 点、C24 二进制 1~3584 点、ASCII 1~7904 点** | [官方手册] SH-080008 批读章节 |
| 4 | 位打包规则「来自源码」 | **官方原文确认：二进制下每点占 4 bit，ON=1/OFF=0，点数为奇数时最低 4 bit 补 0** | [官方手册] SH-080008「Read data, write data」 |
| 5 | 「IL 是 GX Works2 唯一可靠文本入口」 | **不准确**。GX Works2 的编程语言只有 梯形图 / 结构化梯形图·FBD / SFC / ST，**没有 IL**。IL 不是它的源语言，只是「指令表 CSV 导出」与「回路块列表编辑」的呈现形式 | [官方官网] 语言列表；[官方FAQ] 16267 |

---

## A. GX Works2 程序文本交互

### A1. 梯形图窗口能否「粘贴 IL 文本自动生成梯形图」？

**结论：❌ 未证实，且从官方语言清单看不成立。**

**关键事实（本次新增，一手）**：GX Works2 支持的编程语言**不含 IL（指令表）**。

官方产品页把语言分为：

- 图形语言：**梯形图语言**、**结构化梯形图 / FBD 语言**、**SFC 语言**
- 文本语言：**仅 ST 语言（结构化文本）**

原文（英文版）：“Graphical language: Ladder language … Structured ladder/FBD language … SFC language. **Text language: ST (structured text) language**”。
来源：https://www.mitsubishielectric.com/fa/products/cnt/plceng/smerit/gx_works2/program/programming.html 
日文版同页：https://www.mitsubishielectric.co.jp/fa/products/cnt/plceng/smerit/gx_works2/program/programming.html 
官方 FAQ 16267「構造化プロジェクトのサポート言語について」同列：ラダー / 構造化ラダー・FBD / SFC / **ST**，并注明「FXCPUはラダー言語、SFC言語は対応していません」。
来源：https://fa-faq.mitsubishielectric.co.jp/faq/show/16267 

IEC 61131-3 定义了 5 种语言（LD/FBD/SFC/ST/**IL**），GX Works2 声明符合 IEC 61131-3，但**只实现其中 4 种，缺 IL**。这直接解释了为什么网上找不到「GX Works2 里写 IL」的正规入口。

**但 GX Works2 确实有两条「指令表形态」的通道**（都成立，且都只是呈现/导出，不是源语言）：

1. **回路块列表显示/编辑**（回路ブロックリスト表示/編集）：按回路块为单位，用助记符列表形式**显示并编辑**程序。**Ver.1.86Q 以后**支持。
   原文：「回路ブロック単位のプログラムをリスト形式で表示／編集することができます。GX Works2 Ver.1.86Q以降で対応しています。」
   来源：[官方FAQ] https://fa-faq.mitsubishielectric.co.jp/faq/show/14643 ；另一条 https://fa-faq.mitsubishielectric.co.jp/faq/show/38649 （标题「回路ブロックのリスト表示/編集について」）
   → 注意官方用词是**显示/编辑**，**不是**「粘贴文本自动生成梯形图」。
2. **指令表 CSV 导出**：右键程序名（如 MAIN）→ **「写入至 CSV 文件」/「Write to CSV File」**，格式选 Instruction List。详见 A3。

**「粘贴 IL 文本 → 自动生成梯形图」：❌ 未证实。** 搜遍三菱官方手册/FAQ 未见此功能描述；GX Works2 便利机能集（官方 PDF）目录里也**没有**「IL 批量输入 / 文本粘贴生成回路」这一项，只有：指令·标签输入支援、罫線編輯、回路块连续粘贴、行间语句树显示、内联 ST、回路块折叠、Undo。
来源：[官方] https://dl.mitsubishielectric.co.jp/dl/fa/document/catalog/plceng/l08275/l08275a.pdf 

> 结论：**粘贴方案不能作为产品主干**。可行的技术路线是「生成指令表 CSV → 人工/UI 导入」或「回路块列表编辑」，且两者都需实测。

**粘贴文本格式规则：❌ 未证实**（每行一条指令 / 是否带步号 / 注释写法 / 分隔符，均无官方出处）。可确证的替代事实是 **CSV 指令表的列结构**（见 A3）。

### A2.「列入力 / List input」/ 批量输入

- **[官方FAQ]** 存在「回路块列表显示/编辑」（Ver.1.86Q+），是最接近「列入力」的官方功能。见 A1 来源。
- **[官方]** 另有「**连续贴り付け**（连续粘贴）」：右键 → 连续粘贴 → 指定粘贴个数与各软元件增量（increment），批量生成相似回路。
  来源：https://dl.mitsubishielectric.co.jp/dl/fa/document/catalog/plceng/l08275/l08275a.pdf 
- **[官方]** GX Works2 有「命令·标签输入支援」：输入首字母即弹出指令候选并显示参数说明。这是**逐条输入辅助**，不是批量文本导入。同 PDF。

### A3. CSV / TXT 导入「程序」与设备注释（**本项目最关键的一条**）

**官方口径（确证）**：

- **[官方FAQ]** 「From version **1.64S**, GX Works2 can import/export **device comments** in CSV format but **does not support the function for parameters** equivalent to that of GX Converter.」
  → **设备注释 CSV 双向支持；程序/参数没有 GX Converter 那种转换功能。**
  来源：https://fa-faq.mitsubishielectric.com/faq/show/22643 （打印版：https://fa-faq.mitsubishielectric.com/print/faq/22643?category_id=1993 ）
- **[官方FAQ]** 被问「能否把梯形图以列表形式导出到文本/Excel、再从文件读回」时，答复指向 **GX Developer + GX Converter（SW D5C-CNVW）**：即**程序**的文本/CSV 双向转换要靠这套老工具，GX Works2 自身不提供。
  来源：https://fa-faq.mitsubishielectric.co.jp/faq/show/14543 （标题：「ラダー回路のファイル出力」）

**但程序 CSV 的「导出」确实存在，且列结构可确证**（这是本项目可落地的事实基线）：

**[源码/真实样例]** GX Works2 实际导出的 CSV（UTF-16、**Tab 分隔**、每格带双引号）：
https://raw.githubusercontent.com/ito-soft-design/ladder_converter/master/test/files/src/MAIN.csv 

```
"FX2N"
"PC情報:"  "FXCPU FX2N/FX2NC"
"ステップ番号" "行間ステートメント" "命令" "I/O(デバイス)" "空欄" "PIステートメント" "ノート"
"0"  ""  "LD"   "X000" "" "" ""
"1"  ""  "OUT"  "Y000" "" "" ""
"20" ""  "ANB"  ""     "" "" ""
```

→ **7 列**：步号 / 行间语句(注释) / 指令 / 操作数 / 空列 / PI语句 / 笔记。无操作数的指令（ANB/ORB/MPS/MRD/MPP）操作数列为空。
→ 步号**允许不连续**（样例里 29→31、32→34，因为部分指令占 2 步）。

**[源码]** 同一格式被独立项目复现：https://github.com/shw98-jp/gxworks2-io-list-lab （README 明确 `encoding: utf-16, delimiter: tab`，列为 ステップ番号/命令/I-O(デバイス)/ノート）；
**[二手/文档]** 导出步骤说明：https://deepwiki.com/ito-soft-design/ladder_converter/5.1-mitsubishi-gx-works2-projects （写作 Edit > Write to CSV File，选 “Instruction List”；限制：不含标签、仅梯形图指令导出干净）。

**设备注释 CSV 格式**：UTF-16 LE + BOM、Tab 分隔、全字段加引号；Label Editor 列示例：
`Class / Label Name / Data Type / Constant / Device / Address / Comment`
来源：**[二手]** https://raw.githubusercontent.com/Serhioromano/gxw2-skill/main/skills/gxw2-st/references/csv-variables.md （其「GX Works2 拒绝非 UTF-16 LE」的强断言未获官方确证，但 UTF-16+Tab 与上面的真实样例一致）。

> **结论**：**程序可经 CSV 导出**（官方入口存在，格式已确证）；**CSV → 梯形图 的导入无官方文档支持，❌ 未证实**，必须实测。这也意味着产品应保留「导出 IL/CSV 供人工导入」的降级路径。

### A4. GPPW2.EXE / GX Works2 命令行参数

- **❌ 未证实**。三菱公开手册/FAQ 中检索不到任何 GX Works2 命令行参数（`/silent`、直接打开 .gxw 等）。官方仅提到安装后会在 Windows 启动项加入 “GX Works2 Startup”（用于预加载优化），那不是 CLI。
- 实现上应视为**没有命令行接口**：要么 UI 自动化（pywinauto），要么文件级操作。

### A5. .gxw 工程文件内部结构

- **[官方FAQ]** `.gxw` = **单文件格式（single file format）**的工程；**GX Works2 Ver.1.62Q 以后**支持。
  来源：https://fa-faq.mitsubishielectric.com/faq/show/22508 
- **[二手]** 内部结构存在两种说法，**未定论**：
  1) 「binary compound document (Microsoft OLE2 Structured Storage)，内含 checkout.xml / dataprotection.xml / history.xml + 二进制梯形图数据」——https://deepwiki.com/ito-soft-design/ladder_converter/5.1-mitsubishi-gx-works2-projects 
  2) 同一页引用的实机样例 `test/files/src/FX2N.gxw` 又被按「文本、1–1021 行」引用，说明至少 FX 系列单文件工程含大量可读 XML 文本。
  → **建议**：对真实 .gxw 跑 hexdump，前 8 字节若为 `D0 CF 11 E0 A1 B1 1A E1` 即 OLE2 复合文档。**未实测**。
- **可批处理的文本格式 = CSV（见 A3）**，不是 .gxw 本身。

---

## B. MC 协议（MELSEC Communication Protocol）3E 帧二进制

### B6. 3E 二进制帧字节布局 **[官方手册确证]**

**请求帧**（访问本站 / 直连 CPU）：

| 偏移 | 字段 | 字节数 | 典型值 | 线上字节序 |
|---|---|---|---|---|
| 0–1 | 子头 Subheader | 2 | 5000H | **大端** → `50 00` |
| 2 | 网络号 Network No. | 1 | 00 | — |
| 3 | PC 号（目标站号） | 1 | FF | — |
| 4–5 | 请求目标模块 I/O 号 | 2 | 03FFH | **小端** → `FF 03` |
| 6 | 请求目标模块站号 | 1 | 00 | — |
| 7–8 | 请求数据长度 | 2 | 0C 00 | 小端 |
| 9–10 | 监视定时器 | 2 | 10 00 | 小端（×250ms） |
| 11–12 | 指令 Command | 2 | 0401 → `01 04` | 小端 |
| 13–14 | 子指令 Subcommand | 2 | 0000 → `00 00` | 小端 |
| 15… | 请求数据（设备号+设备码+点数 …） | 可变 | | 小端 |

**响应帧**：子头 **D000H**（线上 `D0 00`）+ 回显 4 字段（网号/PC号/IO号/站号，共 5 字节，偏移 2–6）+ 响应数据长度(2, 小端, 偏移 7–8) + **结束代码(2, 小端, 偏移 9–10)** + 响应数据/错误信息(偏移 11 起)。

**长度字段的起算点（官方原文，最容易搞错）**：

- 请求数据长度 = **从「监视定时器」起到「请求数据」末尾**的字节数。
  原文：“Request data length: Specify the data length **from the monitoring timer to the request data**.”
- 响应数据长度 = **从「结束代码」起到「响应数据（正常）或错误信息（异常）」末尾**的字节数。
  原文：“The data length **from an end code to a response data** (at normal completion) or an error information (at abnormal completion) is stored.”
来源：[官方手册] SH-080008《MELSEC Communication Protocol Reference Manual》，第 42–44 页「5.3 Details of Setting Data」；PDF：https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh080008/sh080008ab.pdf 

**手册样例（读 M100–M131，字单位 2 点）**，请求 21 字节：
`50 00 | 00 | FF | FF 03 | 00 | 0C 00 | 10 00 | 01 04 | 00 00 | 64 00 00 | 90 | 02 00`
（M100 → 设备号 100=64H，3 字节小端；M 的设备码 90H；点数 2 → `02 00`）
响应 15 字节：`D0 00 | 00 FF FF 03 00 | 06 00 | 00 00 | 34 12 | 02 00`
来源：https://mcpx.yktmr.jp/en/learn/3e-frame （二手，逐字节讲解，与本手册一致）；字段速查：https://testbench.tools/tools/mc-protocol-decoder/ 

**4E 帧**（需要序号时用）：子头 5400H / 响应 D400H，子头后追加 2 字节 Serial No.（0000H–FFFFH），其余同 3E。来源同上手册第 42 页。

**指令 / 子指令表 [官方手册确证]**

| 功能 | Command | Subcommand |
|---|---|---|
| 批量读（Device Read） | **0401** | **0000**=字单位（Q/L）、**0001**=位单位（Q/L）、**0002**=字单位（iQ-R/iQ-L）、**0003**=位单位（iQ-R/iQ-L） |
| 批量写（Device Write） | **1401** | 同上 0000/0001/0002/0003 |
| 随机读（Read Random） | **0403** | 0000 / 0002 |
| 随机写（Write Random） | **1402** | 0000 / 0001 / 0002 / 0003 |
| 复数块批量读（Read Block） | **0406** | 0000 / 0002 |
| 复数块批量写（Write Block） | **1406** | 0000 / 0002 |
| 登记监视（Entry Monitor Device） | 0801 | 0000 |
| 执行监视（Execute Monitor） | 0802 | 0000 |
| 读 CPU 型号（Read Type Name） | **0101** | 0000 |
| 远程 RUN | **1001** | 0000 |
| 远程 STOP | **1002** | 0000 |
| 远程 PAUSE | **1003** | 0000 |
| 远程 latch clear | **1005** | 0000 |
| 远程 RESET | **1006** | 0000 |
| 远程密码 解锁 / 上锁 | 1630 / 1631 | 0000 |
| 回环测试（Loopback test） | **0619** | 0000 |
| 清错误信息 | 1617 | 0000 |

**子指令选用规则（官方原文）**：“Use the subcommand **0001 or 0000** when the access destination or connected station is a MELSEC-Q series or MELSEC-L series module. Use **0003 or 0002** when … MELSEC iQ-R series or MELSEC iQ-L series. Use 0001 and 0000 to acquire compatibility with Q/L.”
来源：SH-080008 第 35 页 / SH-080956 第 35 页「5.2 Device (Device Access)」。

> ⚠️ **实测陷阱**：给 Q 系列发 iQ-R 的 `0002/0003` 会返回 **C059**（指令/子指令错误）。

### B7. 设备代码表（二进制） **[官方手册确证，两份官方手册一致]**

**先纠正一个常见误解**：「设备代码 2 字节」只对 **ASCII 帧**与 **iQ-R 二进制**成立。

| 帧格式 | 设备号 | 设备代码 | 合计 |
|---|---|---|---|
| Q/L/QnA 二进制 | **3 字节**（小端） | **1 字节** | 4 字节 |
| iQ-R/iQ-L 二进制 | **4 字节**（小端） | **2 字节**（小端，高位补 0） | 6 字节 |
| ASCII（Q/L 子指令 0001/0000） | **6 位**十进制/十六进制数字 | **2 字符** | 8 字符 |
| ASCII（iQ-R 子指令 0003/0002） | **8 位**数字 | **4 字符** | 12 字符 |

来源：[官方手册] SH-080008 第 35–40 页；SH-080956「Device code / Head device No. / Number of device points」；[源码] pymcprotocol `_make_devicedata()` 行为一致：https://raw.githubusercontent.com/senrust/pymcprotocol/main/src/pymcprotocol/type3e.py 
ASCII 补位规则（官方原文）：「If the device code has three digits or less, add `*` (2AH) or a space (20H) after the device code. Specify a device code in two digits for the subcommand 0001 or 0000.」

**完整二进制设备代码表**（ASCII 码 / 二进制码）：

| 设备 | 名称 | 位/字 | ASCII 码 | **二进制码** | 地址进制 |
|---|---|---|---|---|---|
| SM | 特殊继电器 | 位 | SM | **91H** | 十进制 |
| SD | 特殊寄存器 | 字 | SD | **A9H** | 十进制 |
| X | 输入 | 位 | X* | **9CH** | 十六进制 |
| Y | 输出 | 位 | Y* | **9DH** | 十六进制 |
| M | 内部继电器 | 位 | M* | **90H** | 十进制 |
| L | 锁存继电器 | 位 | L* | **92H** | 十进制 |
| F | 报警器 | 位 | F* | **93H** | 十进制 |
| V | 边沿继电器 | 位 | V* | **94H** | 十进制 |
| B | 链接继电器 | 位 | B* | **A0H** | 十六进制 |
| D | 数据寄存器 | 字 | D* | **A8H** | 十进制 |
| W | 链接寄存器 | 字 | W* | **B4H** | 十六进制 |
| TS | 定时器触点 | 位 | TS | **C1H** | 十进制 |
| TC | 定时器线圈 | 位 | TC | **C0H** | 十进制 |
| TN | 定时器当前值 | 字 | TN | **C2H** | 十进制 |
| STS | 累计定时器触点 | 位 | SS | **C7H** | 十进制 |
| STC | 累计定时器线圈 | 位 | SC | **C6H** | 十进制 |
| STN | 累计定时器当前值 | 字 | SN | **C8H** | 十进制 |
| CS | 计数器触点 | 位 | CS | **C4H** | 十进制 |
| CC | 计数器线圈 | 位 | CC | **C3H** | 十进制 |
| CN | 计数器当前值 | 字 | CN | **C5H** | 十进制 |
| SB | 链接特殊继电器 | 位 | SB | **A1H** | 十六进制 |
| SW | 链接特殊寄存器 | 字 | SW | **B5H** | 十六进制 |
| **S** | **步进继电器** | 位 | S* | **98H** | 十进制 |
| DX | 直接访问输入 | 位 | DX | **A2H** | 十六进制 |
| DY | 直接访问输出 | 位 | DY | **A3H** | 十六进制 |
| **Z** | **变址寄存器** | 字 | Z* | **CCH** | 十进制 |
| R | 文件寄存器 | 字 | R* | **AFH** | 十进制 |
| ZR | 文件寄存器（连续编号访问） | 字 | ZR | **B0H** | 十六进制 |

**iQ-R / iQ-L 专有**（仅子指令 0003/0002 可用）：

| 设备 | ASCII | 二进制码 |
|---|---|---|
| LTS 长定时器触点 | — | **51H** |
| LTC 长定时器线圈 | — | **50H** |
| LTN 长定时器当前值 | — | **52H** |
| LSTS 长累计定时器触点 | — | **59H** |
| LSTC 长累计定时器线圈 | — | **58H** |
| LSTN 长累计定时器当前值 | — | **5AH** |
| LCS 长计数器触点 | — | **55H** |
| LCC 长计数器线圈 | — | **54H** |
| LCN 长计数器当前值 | — | **56H** |
| LZ 长变址寄存器 | — | **62H** |
| RD 刷新数据寄存器 | — | **2CH** |

来源：[官方手册] SH-080008「Device code」表（第 35–37 页）；SH-080956 第 35–37 页同表；两份互相印证。
交叉验证：[源码] pymcprotocol `mcprotocolconst.py`（https://raw.githubusercontent.com/senrust/pymcprotocol/main/src/pymcprotocol/mcprotocolconst.py ）与 node 版 mcprotocol.js（含 `S = 0x98`）完全一致。

**重要备注（官方原文）**：

- **S（步进继电器）是「只读」**：“Step relay … Can be specified with an S device supported model. (**Read only**)”。
- **L / V / F / TS / TC / TN / CS / CC / CN / SB / SW / Z 等「本地软元件不可访问」**：“Local devices cannot be accessed.”——若 CPU 参数里把某软元件设为本地，外部设备读写会失败。
- **R 与 ZR 的区别**：R 是**块切换方式**（十进制地址）；ZR 是**连续编号访问方式**（十六进制地址）。官方建议：若文件寄存器由多块组成，**必须用 ZR**。
- **X/Y/B/W/SB/SW/DX/DY/ZR 为十六进制寻址，其余十进制**（见上表最后一列）。
- ⚠️ **X/Y 的八进制坑**：FX 系列（及部分老 A 系列用法）中 `X010` 的前导 0 表示**八进制**（=8），而 `X10`（无前导 0）常按十六进制（=16）。这与 CPU 型号/参数相关。**[二手]** https://aiot.do/en/docs/melsec-plc-address-reference （正文需 VIP，仅要点可见）；**[源码]** node mcprotocol.js 中有 `octalInputOutput` 开关，默认对 X/Y 做 `parseInt(offset, 8)`，印证该行为确实存在且**是可配置项**。

### B8. 位 / 字单位读写的请求与响应数据格式 **[官方手册确证]**

**字单位（子指令 0000/0002）**

- 请求数据：`设备号(3B) + 设备码(1B) + 点数(2B)`（iQ-R 为 `4B + 2B + 2B`）
- 每点 **2 字节，小端**（低字节在前）。即 D350=56ABH 线上为 `AB 56`。
- 响应数据 = N×2 字节。

**位单位（子指令 0001/0003）—— 二进制打包规则（官方原文，此前只能从源码推断）**

> “Data communication in **binary code**: Represent the ON/OFF status of each device in **4-bit per 1 point**. • For ON: '1' • For OFF: '0'. **When the number of points is odd number, the lowest 4 bits are set to '0'.**”

逐点规则：**第 1 点（偶数下标 0）放字节的高 4 位（bit4），第 2 点放低 4 位（bit0）**，其余 3 位恒为 0。
即：`byte = (b[2k] << 4) | (b[2k+1])`，最终字节数 = `ceil(点数/2)`。

官方例子（M10~M14，共 5 点，ON/OFF/ON/OFF/ON）→ 3 字节：`10H 10H 10H`
（M10 在 0x10、M11 在 0x00 → `10`；M12 在 0x10、M13 在 0x00 → `10`；M14 在 0x10、奇偶补 0 → `10`）

**这与 pymcprotocol 的实现完全一致**（源码注释：「if i//2==0, bit value is 4th bit … Even index's value turns on or off 4th bit, odd index's value turns on or off 0th bit.」）。
来源：[官方手册] SH-080008「8.1 Data to be Specified in Commands → Read data, write data → For bit units」；[源码] type3e.py `batchread_bitunits`。

**位单位 ASCII**：每点 1 个字符，ON=`'1'`(31H)、OFF=`'0'`(30H)，**不打包**。

**点数上限 [官方手册确证，解决了第一版两来源冲突]**

| 访问对象 | 字单位 | 位单位（ASCII） | 位单位（C24 二进制） | 位单位（E71 二进制） |
|---|---|---|---|---|
| iQ-R / iQ-L / Q / L | 1~960 点 | 1~7904 点 | 1~3584 点 | **1~7168 点** |
| QnA 系列模块 / 经 QnA 网络访问他站 | 1~480 点 | 1~3952 点 | 1~1792 点 | 1~3584 点 |
| A 系列模块 | 1~64 点 | 1~256 点 | — | — |

来源：[官方手册] SH-080008「Batch read in bit units (command: 0401)」的 Number of device points 表。
→ **常用值：E71 以太网 + Q/L 时，字读写 ≤ 960 点，位读写 ≤ 7168 点。**
→ 第一版提到的「950 words」是错的（应为 960）。

### B8b. 0406 / 1406 复数块批量读写 **[官方手册确证，第一版标为未证实]**

**用途**：把 n 点字设备或 n 点位设备（**1 点按 16 bit 算**）作为一个「块」，一次请求读写**多个不连续的块**。
官方描述：“reads data by treating n points of word devices or bit devices (**one point is equivalent to 16 bits**) as one block and specifying multiple blocks. This can be specified with **inconsecutive device No.**”

**0406 请求数据布局（二进制）**：

```
指令(2B)=0406  子指令(2B)=0000/0002
  ├─ 字数块数(1B)   Number of word device blocks
  ├─ 位数块数(1B)   Number of bit device blocks
  ├─ [字数块 1] 设备号(3B) + 设备码(1B) + 点数(2B)
  ├─ [字数块 2] …
  ├─ [位数块 1] 设备号(3B) + 设备码(1B) + 点数(2B)
  └─ [位数块 2] …
```

**关键约束（官方原文）**：

- **总点数**：`字数块各块点数之和 + 位数块各块点数之和 ≤ 960`
- **块数上限**：子指令 **0002**（iQ-R/iQ-L）→ 字数块数 + 位数块数 **≤ 60**；子指令 **0000**（Q/L）→ **≤ 120**
- **顺序**：设备必须**先写字数块、再写位数块**（“Specify the device number in order from the word device to the bit device.”）
- 定时器/计数器/累计定时器的**触点、线圈**要按**位块**指定
- **不可用**的设备：LT/LST/LC 的触点与线圈、**LZ**
- 若访问目标为 iQ-R/iQ-L 且使用**子指令 008□**（设备扩展指定），**块数要 ×2**

**0406 响应数据**：先按顺序给出字数块数据，再给位数块数据（每块按各自的设备/点数顺序）。

**1406 请求数据布局**：与 0406 相同，但在每个块的 `点数` 之后再跟**写数据**（字数块 N×2 字节；位数块按位打包）。

**官方通信例子**（0406）：字数块1 = D0~D3（4 点）、字数块2 = W100~W107（8 点）；位数块1 = M0~M31（2 点）、块2 = M128~M159（2 点）、块3 = B100~B12F（3 点）。

来源：[官方手册] SH-080956《SLMP Reference Manual》第 69–74 页「Read Block (command: 0406)」「Write Block (command: 1406)」；PDF：https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh080956eng/sh080956engm.pdf 

> ⚠️ **可用性警告**：0406/1406 在部分模块上不可用，会返回 **C059**。McpX 作者提示「CPU 单元内置以太网口上 0406 可能不可用」。实现时应把它做成「可降级为多次 0401」的可选优化。

### B9. 远程 RUN / STOP / PAUSE / LATCH CLEAR / RESET **[官方手册确证]**

| 功能 | 指令 | 子指令 | 请求数据 |
|---|---|---|---|
| 远程 RUN | **1001** | 0000 | `模式(2B)` + `清除模式(1B)` + `固定值 00H(1B)` |
| 远程 STOP | **1002** | 0000 | `固定值 0001H(2B)` |
| 远程 PAUSE | **1003** | 0000 | `模式(2B)` |
| 远程 latch clear | **1005** | 0000 | `固定值 0001H(2B)` |
| 远程 RESET | **1006** | 0000 | `固定值 0001H(2B)` |
| 远程密码解锁 | 1630 | 0000 | 密码长度(2B) + 密码 |
| 远程密码上锁 | 1631 | 0000 | 密码长度(2B) + 密码 |

**远程 RUN 的字段取值（官方原文）**：

- **模式 Mode**：`0001H` = 不强制执行（Do not execute forcibly）；`0003H` = 强制执行（Execute forcibly）
  → 含义：当目标模块**已被其他设备远程操作中**时，是否强行覆盖。
- **清除模式 Clear mode**：`00H` = 不清除；`01H` = 只清除 latch 范围以外；`02H` = 全部清除
- **固定值**：固定为 `0`

**重要语义（官方原文）**：“The command can be executed when the switch of the access target module is RUN. **In the STOP status, the command is completed normally, however, the access target CPU will not be in RUN status.**”
→ 即：**远程 RUN 在物理开关处于 STOP 时也会返回「正常完成」，但 CPU 不会真的进入 RUN**。实现上不能只靠 end code 判断成败，必须回读状态。

**响应数据**：以上远程控制命令**均无响应数据**（只有结束代码）。

**可用性矩阵（官方附录）**：

- 远程 RUN(1001) / STOP(1002) / RESET(1006)：对 CPU 模块、Q/L/QnA/A/QS/H 等可执行；对**远程 I/O、头模块不可执行**。
- 远程 PAUSE(1003) / latch clear(1005)：对 iQ-R/iQ-L/L/Q 可执行；对 **QnA、A、QS、H 不可执行**。

来源：[官方手册] SH-080008 第 11 章「CONTROL MODULE OPERATION → 11.2 Remote Operation」（第 172–178 页），及附录「Accessible Modules for Each Command」。

### B10. 响应结束代码（end code）

**位置**：响应帧偏移 9–10（2 字节，**小端**）。
官方原文：「**Data communication in binary code: Send 2-byte numerical value from the lower byte (L: bits 0 to 7).** Ex. Normal completion `0000` / Error code `C051H` → 二进制为 `51H C0H`」。
→ 即 `C051` 在线上是 **`51 C0`**，读取时按小端解成 0xC051。

**错误信息结构（官方原文）**：“Store the information of a station on which an error occurred and information of a command. • **Access route**: The information of a station which sent an error response is stored. **It may differ from the contents of a request message.** • **Command, subcommand**: The command and the subcommand when an error occurred are stored.”

→ 异常响应 = 子头 + Access route(**) + 响应数据长度 + 结束代码 + **错误信息(出错站 4 字段 + 指令 + 子指令)**。
→ **解析时绝不能假设异常响应定长**；也必须用响应里的 Access route，而非请求里的（可能不同，因为可能是中间站报错）。
来源：[官方手册] SH-080008 第 44 页「End code」「Error information」。

**关于具体错误码值**：⚠️ **SH-080008 与 SH-080956 本身都不含错误码明细表**，两本手册都明确写「For the content of error code and its corrective action, refer to **the user's manual of the module used**」（并列出 QCPU/LCPU 硬件手册、Q/L 以太网模块手册、iQ-R 串行通信模块手册）。

因此下列错误码含义来自 **[二手] 高质量整理 + 官方模块手册转述**，请以所用模块手册为准：

| 结束代码 | 含义 | 处置 |
|---|---|---|
| **0000** | 正常完成 | — |
| C050 | ASCII 模式收到无法转成二进制的字符 | 检查发送字符 |
| C051 | 字单位读写**点数超范围** | 点数 ≤960 |
| C052 | 位单位读写**点数超范围** | 点数 ≤7168(E71) |
| C054 | 随机读写点数超范围 | 减点数 |
| C056 | 读写**地址超出最大地址** | 改首地址/点数 |
| C058 | ASCII→二进制转换后长度与数据数不符 | 检查请求数据长度 |
| **C059** | **指令/子指令错误，或连接对象不支持该功能** | 检查指令、子指令（如给 Q 发 0002/0003）、目标模块是否支持（如 0406） |
| **C05B** | ⚠️ **未证实** | 本次所有可查来源均未列出该码，**不要凭空实现其含义** |
| C05C | 请求数据内容错误 | 检查请求数据 |
| C05E | 监视定时器超时（目标未响应） | 加长监视定时器/查网络与路由 |
| C061 | **请求数据长度与实际数据数不符** | 检查请求数据长度字段 |
| C0B2 | 收发缓冲无空位 | 等上次响应再发 |
| C2xx（C200/C201/C204） | 远程密码相关（密码错/端口锁定/异机解锁） | 走 1630 解锁 |
| 4031 | CPU 侧：设备号超范围或 CPU 不支持该设备 | 检查设备号与软元件分配 |

来源：[二手] https://mcpx.yktmr.jp/learn/error-codes （作者自述取自 MELSEC iQ-R Ethernet 用户手册，并提示 Q/L 系列代码范围可能不同）。

### B11. ⚠️ 串行通信模块（C24）与以太网模块（E71）的二进制差异 **[官方手册确证]**

官方手册在多处区分「**Binary code (For C24)**」与「**Binary code (For E71)**」，二者**不是同一格式**：

- C24（串行通信模块）的二进制报文中会插入 **DLE（10H）附加码**作为控制字符；E71（以太网模块）**没有** DLE。
  官方原文：“For C24, the additional code may be added. (Page 35 Additional code (10H))”，并在报文示例中标注 `DLE 10H`。
  此外 C24 还有 sum check code（和校验）与字符区等，属于串行帧的概念。
- **对本项目的意义**：gxworks2-mcp 走的是 **3E over TCP（E71 / CPU 内置以太网口）**，**不需要 DLE、不需要和校验**。若将来支持串行 C24，必须另写一套分帧逻辑。

来源：[官方手册] SH-080008 第 35 页「Additional code (10H)」及第 8 章各命令的通信示例（区分 C24 / E71 两列）。

---

## C. 模拟器

### C11. GX Simulator2 能否被外部 MC 协议客户端连接？

**结论：原生 ❌ 不能；也不存在固定的 MC 端口。**

**[源码/项目文档]** 第三方桥接项目 NetToGXSim2 的 README 明确陈述：
> “Traditionally, Mitsubishi's GX Simulator 2 runs only as an isolated local process (**SimManager.exe**), making it **impossible for external HMIs, SCADA packages, or IoT gateways to communicate with the simulated PLC** without physical hardware.”

该项目的做法是：**直接读写模拟器进程内存**，然后**自己**在 **TCP/UDP 5000 / 6000** 上开一个 MC 协议服务器。
→ 反推：**5000/6000 是桥接软件自己的端口，不是 GX Simulator2 的端口**。
来源：https://raw.githubusercontent.com/ismaillowkey/NetToGXSim2/main/README.md 

**[官方FAQ]** GX Works2 连接模拟器的方式是在「连接目标 / 接続先指定」里选 **GX Simulator2**（并选版本），走三菱私有接口，**不是 IP+端口的 MC 协议**。
佐证：GT SoftGOT2000 与 GX Simulator2 连接步骤 https://fa-faq.mitsubishielectric.co.jp/faq/show/20619 ；第三方驱动文档「GX Simulator / GX Works シミュレータとの接続…ユニットの通信設定ダイアログから対象バージョンを選択」 https://docs.roboticsware.com/ja/6.0.16/pa-panel/contents/cmn_plc_mitsubishi_close_gxsimulator.html 

**[官方FAQ]** **MX Component 可以连 GX Simulator2**：
> 「MX Component で下記シミュレータを使用してデバッグできます。・GX Simulator2 ・GX Simulator3 ・MT Simulator2 ・MELSOFT Mirror」
→ 模拟器确实有可编程访问通道，**但是 MX Component（ActiveX/DLL），不是裸 MC 协议**。
来源：https://fa-faq.mitsubishielectric.co.jp/faq/show/13399 

**实际端口号**：GX Simulator2 本身 **❌ 未证实**存在任何公开 MC 监听端口。

**对实现的建议**：`plc_probe` 在模拟器场景应明确报「不支持直连」，文档写清「模拟器验证需 MX Component 或第三方桥接（如 NetToGXSim2）」。

### C12. 真实 PLC（Q 系列 / FX 系列以太网模块）MC 端口 5000 / 5001

**核心结论：MC 协议本身不约定端口号。** 端口是模块参数里「打开设置 / Open Settings」自行配置的。

**[二手]** Q 系列内置以太网口配置示例：TCP 用 1100、UDP 用 1200（**示例值，非默认**）；可选端口范围 **1025–4999、5010–65534**——注意 **5000–5009 段在「自局端口号」里是被排除的**。
来源：https://momomo-97.com/gx-works2-plc-mc-protocol-communication-settings/ 

**[官方FAQ]** FX 系列：FX3U-ENET 端口在模块设置工具里配置；**FX3U-ENET-ADP 需把端口改成 5556**（暗示 FX3U-ENET 常用 5555）。
来源：https://fa-faq.mitsubishielectric.co.jp/faq/show/25027 

**关于「5000 = MC over TCP、5001 = ?」**：

- **❌ 未证实** 5001 有任何官方定义。
- 5000 是业界**约定俗成**值（大量库默认 5000），**不是协议规定**。
- 一个常见误解需要澄清：某些资料说「5000/5001 是 MC 默认端口」，实际上**是否可用完全取决于 PLC 参数里开了哪个端口**。若 PLC 没在 5000 上开 MC 连接，连 5000 必然失败。

> 实现建议：`plc_probe` 应支持端口列表探测 + 明确提示「端口须与 PLC 参数中的打开设置一致」，不要写死 5000。

---

## D. 对 gxworks2-mcp 现行实现的具体影响（审查结论）

| 项 | 现状/假设 | 依据 | 建议 |
|---|---|---|---|
| 产品定位「用 IL 写程序」 | README 称「IL 是 GX Works2 唯一可靠的文本入口」 | GX Works2 官方语言表**无 IL** | 措辞改为：**生成 MELSEC 指令表（助记符）文本/CSV**，并说明它不是 GX Works2 的源语言 |
| `gx_paste_ladder`（粘贴 IL 自动生成梯形图） | 作为核心卖点 | **无任何官方依据** | 降级为实验特性，标注「需人工验证」；或改为「写入 CSV → 用户手动导入」 |
| CSV 导入（CSV→梯形图） | 被当作可行路径 | 官方只承诺**注释** CSV 双向；程序 CSV 仅确认**导出** | 必须实测；产品要有「只导出、不导入」的降级路径 |
| 设备代码表 | — | 官方两本手册已给全 | 直接用 B7 表；注意 **S 只读**、**本地软元件不可访问**、**R vs ZR** |
| 位打包 | 若按「每点 1 bit」实现会错 | 官方：**每点 4 bit**，奇偶补 0 | 按 B8 的 `(b0<<4)|b1` 实现 |
| 点数上限 | 若写 950 会保守但非错 | 官方：字 960、位 7168(E71) | 用 B8 的上限表，并注意 C24 vs E71 差异 |
| 子指令 | 若统一用 0000/0001 | iQ-R 必须用 0002/0003 | 按 PLC 系列自动选择，否则 C059 |
| 远程 RUN 成败判断 | 若只看 end code 会误判 | 官方：STOP 开关下也返回正常 | 远程 RUN 后必须**回读状态**确认 |
| `plc_probe` 默认端口 5000 | 可能误导用户 | 端口由 PLC 参数决定 | 探测 + 明确提示如何配置 |
| 模拟器支持 | README 暗示可「在模拟器上验证」 | 原生不可直连 | 明确写「需 MX Component 或第三方桥接」 |

---

## E. 本机实测（2026-10-18，GX Works2 1.620W 中文版）

> 本节全部来自**真实运行**，不是文档推断。环境：Windows 10 中文版，GX Works2 1.620W，
> 安装于 `D:\\新建文件夹\\GPPW2`，主程序 **`GD2.exe`**（不是 GPPW2.EXE），窗口类 **`GXW2FrameWnd`**。

### E1. ✅ 已实测确证

#### (1) GX Works2 的程序语言下拉里**没有 IL** —— 一手实测

「新建工程」对话框（Ctrl+N，窗口类 `#32770`，标题「新建」）的控件：

| 控件 | control_id | 内容 |
|---|---|---|
| 系列(&S) | 10002 | QCPU(Q模式) / LCPU / FXCPU / QCPU(A模式) / QSCPU / QnACPU / ACPU / 运动控制器(SCPU) / CNC(M6/M7) |
| 机型(&T) | 10003 | Q00J / Q00UJ / Q00 / … / Q03UDV … |
| 工程类型(&P) | 10001 | 简单工程 / 结构化工程 |
| **程序语言(&G)** | **10070** | **梯形图 / SFC** ← 仅两项，**无 IL** |

这从 UI 层面**独立验证**了 A1 的结论：GX Works2 不能以 IL 作为程序语言新建程序。

#### (2) 中文版「编辑」菜单的完整条目（实测抓取）

```
撤消(U) Ctrl+Z        恢复(R) Ctrl+Y        剪切(T) Ctrl+X
复制(C) Ctrl+C        粘贴(P) Ctrl+V        连续粘贴(Q)... Ctrl+Alt+V
删除(D) Del           恢复到梯形图转换后的状态(V)
行插入(W) Shift+Ins   行删除(E) Shift+Del    列插入(N) Ctrl+Ins   列删除(M) Ctrl+Del
NOP批量插入(H)...     NOP批量删除(A)
划线写入(I) F10       划线删除(L) Alt+F9
TC设定值更改(G)...
梯形图编辑模式(Z) ▸   梯形图符号(S) ▸       内嵌ST(X) ▸
FB实例名编辑(B)...    文档创建(O) ▸         简易编辑(Y) ▸
从CSV文件读取(J)...   ← ★ 官方**导入**入口（第一版资料未确认）
写入至CSV文件(K)...   ← ★ 官方**导出**入口（第一版只确认了这个）
```

**关键更正**：中文版确实存在 **「从CSV文件读取(J)...」**。第一版 A3 结论写的是
「CSV → 梯形图 导入无官方文档支持 ❌未证实」——**UI 层面这个入口是存在的**，
只是官方 FAQ 没有承诺它可用于「程序」（FAQ 22643 只承诺设备注释）。
这必须通过实测确认它的真实行为，目前**行为未验证**（见 E3）。

#### (3) 粘贴 IL 到梯形图窗口 —— **实测无效**

操作：新建 QCPU(Q模式)/Q03UDV/简单工程/梯形图 → 聚焦 MDI 子窗口（`Afx:00400000:...`）
→ 点击第 0 格 → 剪贴板放入 `LD X0 / OUT Y0 / LD X1 / OUT Y1 / END` → Ctrl+V。

结果：**梯形图没有任何变化**，仍是初始的 `0 … END`，标题栏步数仍为「2步」。
→ **「粘贴 IL 自动生成梯形图」实测不成立。** `gx_paste_ladder` 的核心假设被证伪。

> 说明：本次测试时窗口焦点曾受干扰（见 E4），因此结论以「至少不是开箱即用的 Ctrl+V」为准；
> 但结合 (1) 中「无 IL 语言」+ 官方无任何文档描述该功能，判定为**不可依赖**。

#### (4) 主程序、窗口类与安装路径

| 项 | 实测值 |
|---|---|
| 安装目录 | `D:\\新建文件夹\\GPPW2` |
| 主程序 | **`GD2.exe`**（目录内**没有** GPPW2.EXE） |
| 其他 exe | `GX Works2 FastBoot.exe` / `GX Works2 Progress.exe` / `GX Works2 Service.exe` / `GX Works2 Startup.exe` / `MELHLP.exe` / `GD2IntelliToolsSCPrtcl.exe` |
| 主窗口类 | **`GXW2FrameWnd`** |
| 子编辑器 | `MDIClient` → `Afx:00400000:...`（MDI 子窗口，标题形如 `[PRG]写入 MAIN 2步`） |
| GX Simulator2 目录 | `D:\\新建文件夹\\GPPW2\\GX Simulator2` |

**对现有代码的影响**：`ui.py` 的 `_INSTALL_HINTS` 只搜索
`C:\\Program Files (x86)\\MELSOFT\\GPPW2` 等路径，**覆盖不到本机**；
`_EXE_NAMES` 里虽含 `GD2.exe`（正确），但目录列表需要补上安装路径探测
（建议加注册表 `InstallLocation` + `D:` 盘搜索，或读 `GX Works2 Startup.exe` 的路径反推）。

#### (5) GX Works2 的菜单是**自绘**的，不是标准 Win32 菜单

- `GetMenu(frame)` 返回 **0**；`GetMenuBarInfo` 失败 → 无原生 HMENU，无法用 `WM_COMMAND` + 菜单 ID 驱动。
- 菜单实为 DevExpress `XTPToolBar`（标题「菜单栏」）自绘，**只能**靠 UIA 枚举 `MenuItem` 或键盘加速键。
- 用 **UIA 后端**能枚举到全部 `MenuItem`（含中文标题与坐标），这是唯一可编程读取菜单的途径：
  `Application(backend="uia").connect(process=PID)` → `descendants(control_type="MenuItem")`。

### E2. 可用自动化矩阵（实测）

| 手段 | 结果 | 备注 |
|---|---|---|
| UIA 枚举菜单项/控件 | ✅ 可用 | 能拿到中文标题 + rectangle，可 `click_input()` |
| Win32 读菜单 / `WM_COMMAND` | ❌ 不可用 | 自绘菜单，无 HMENU |
| `send_keys("^n")` 弹出新建对话框 | ✅ 曾成功 | 需 GX Works2 在前台 |
| `send_keys("^v")` 粘贴 | ⚠️ 会发送但**无效果** | 梯形图不接受 IL 文本 |
| `SetForegroundWindow` / `AttachThreadInput` | ❌ 拒绝访问 | 前台被其他窗口长期占用 |
| `SetCursorPos`（鼠标点击） | ❌ 失败 | 输入注入受限 |
| `keybd_event` 全局注入 | ❌ 无效果 | 同上 |

### E3. ✅ 已闭环：CSV → 梯形图 全链路实测成功（**本轮最重要结论**）

> 本轮在真实 GX Works2 1.620W 中文版上**完整跑通了「CSV → 梯形图 → 编译」**，
> 全程通过 UIA `expand()` + `invoke()` 驱动，**不依赖前台焦点、不需要鼠标**。

#### (1) 已实测成功的完整链路

```
新建工程(QCPU Q模式 / Q03UDV / 简单工程 / 梯形图)
  → 工程另存为 demo1.gxw            ← 成功，919040 字节
  → 编辑 → 写入至CSV文件 → MAIN_template.csv   ← 成功，拿到本机真实模板
  → 构造含真实指令的 CSV
  → 编辑 → 从CSV文件读取 → 确认           ← 成功
  → 标题栏「2步」→「7步」，梯形图真实生成   ← ★ 决定性证据
  → 转换/编译(C) → 转换(B) F4            ← 成功，无报错
```

#### (2) 本机真实 CSV 模板（**与第三方推断的差异**）

```
"demo1"
"PLC信息:"	"QCPU (Q mode) Q03UDV"
"步号"	"行间声明"	"指令"	"I/O(软元件)"	"空白栏"	"PI声明"	"注解"
"0"	""	"END"	""	""	""	""
```

- **编码：UTF-16 LE + BOM（`FF FE`）** ← 实测确认，第一版只能标 ⚠️
- **分隔符：Tab**，全字段双引号
- 第 1 行：工程名；第 2 行：`"PLC信息:"` + CPU 机型（**中文版是「PLC信息:」**，
  日文版是「PC情報:」，**英文版/日文版列头不同，必须按语言适配**）
- 中文列头：`步号 / 行间声明 / 指令 / I/O(软元件) / 空白栏 / PI声明 / 注解`
  （日文：ステップ番号 / 行間ステートメント / 命令 / I/O(デバイス) / 空欄 / PIステートメント / ノート）
- 无操作数指令（END/ANB/ORB）的 I/O 列留空

#### (3) 导入实测结果（决定性）

注入的 CSV：

```
"demo1"
"PLC信息:"	"QCPU (Q mode) Q03UDV"
"步号"	"行间声明"	"指令"	"I/O(软元件)"	"空白栏"	"PI声明"	"注解"
"0"	""	"LD"	"X0"	""	""	""
"1"	""	"OUT"	"Y0"	""	""	""
"2"	""	"LD"	"X1"	""	""	""
"3"	""	"ANI"	"X2"	""	""	""
"4"	""	"OUT"	"Y1"	""	""	""
"5"	""	"END"	""	""	""	""
```

结果：
- 标题栏 **`[[PRG]写入 MAIN 2步` → `[[PRG]写入 MAIN 7步`**
- 梯形图编辑器**真实绘制出**：`X0 —| |— (Y0)`、`X1 —| |— X2 —|/|— (Y1)`、`END`
- 随后 F4 转换/编译**无报错**

→ **「CSV → 梯形图」在这台机器上完全成立**。第一版 A3 写的
「导入无官方文档支持 ❌未证实」应更正为：**官方 FAQ 未承诺，但 UI 与功能实测可用**。

#### (4) 导入时的官方提示（实测抓取，说明行为边界）

「从CSV文件读取」确认框原文：

> 读取指定的文件内容。确定吗？
> ·必要时进行扩展设置。(默认/有更改)
> **注意**
> ·**当前程序编辑器的设置内容将被放弃。**
> ·当前打开的窗口过多，读取可能会花费较长时间。
> ·**读取后请执行程序检查。**

→ 即：**导入是「整块替换当前程序编辑器内容」**，不是追加。工具实现时必须以
「一个 CSV = 一个完整程序块」为单位生成，并提示用户导入后执行程序检查。

#### (5) .gxw 魔数（实测）

保存出的 `demo1.gxw` 前 8 字节 = `d0 cf 11 e0 a1 b1 1a e1`
→ **确认为 OLE2 复合文档（Compound File Binary Format）**。第一版的「未定论」已解决。

#### (6) 关键自动化技术（本轮验证有效，可直接用于实现）

| 能力 | 方法 | 是否需前台 |
|---|---|---|
| 读全部菜单项 | UIA `descendants(control_type="MenuItem")` | ❌ 不需要 |
| **展开菜单** | `menuitem.expand()` | ❌ **不需要** |
| **点击菜单项** | `menuitem.invoke()` | ❌ **不需要** |
| 填写对话框 | `child_window(control_id=N).select()/set_edit_text()` | ❌ 不需要 |
| 点按钮 | `child_window(class_name="Button").click()` | ❌ 不需要 |
| 读标题栏验证步数 | `frame.window_text()` 解析「N步」 | ❌ 不需要 |

**这是本轮最重要的工程发现**：GX Works2 的菜单虽是自绘（无 HMENU，`WM_COMMAND` 不可用），
但 **UIA 的 ExpandCollapse + Invoke 模式可以完全无焦点地驱动它**。
之前 `send_keys` / 鼠标方案的失败，全部可以绕过。

#### (7) 实测中遇到的非阻塞性弹窗（实现时需处理）

| 弹窗 | 触发时机 | 处理 |
|---|---|---|
| 「用户认证模式设置为了启用，但未启用操作履历的参数设置」 | 新建工程后 | 点「确定」 |
| 「启用工程的安全性检查为禁用…是否启用并保存？」 | 保存工程时 | 点「否」 |
| 「将当前显示的梯形图写入至CSV文件。确定吗？」 | 导出 CSV | 点「是」 |
| 「读取指定的文件内容。确定吗？」 | 导入 CSV | 点「是」 |
| 「程序存储器传送进度」 | 多次操作后伴随出现 | 不影响，可忽略/关闭 |

### E4. 结论：**可以真正把程序写进 GX Works2 了**

| 路径 | 状态 | 依据 |
|---|---|---|
| **7 列 CSV →「从CSV文件读取」→ 梯形图 → F4 编译** | ✅ **全链路实测成功** | E3(1)(3) |
| IL 粘贴 → 梯形图 | ❌ 实测失败 | E1(3) |
| 新建工程并选 IL 作为语言 | ❌ 不存在该选项 | E1(1) |
| 回路块列表显示/编辑 | ⚠️ 官方存在（1.86Q+），未实测 | A1 |
| 写 7 列 CSV（导出） | ✅ 实测成功，模板已取得 | E3(2) |
| 设备注释 CSV | ✅ 官方双向承诺（1.64S+） | FAQ 22643 |
| 保存 .gxw | ✅ 实测成功（OLE2） | E3(5) |

**产品路线（已实测，可以写进 README 当承诺）：**

```
MCP 工具（逻辑树 / IL）
   → 生成 7 列 CSV（UTF-16 LE + BOM + Tab）
   → 用户在 GX Works2 里：编辑 → 从CSV文件读取 → 是
   → 梯形图生成
   → 转换/编译（F4）
   → 保存 .gxw
```

**关键约束（实测得出，必须写进工具行为）：**

1. **导入是整块替换**（官方提示「当前程序编辑器的设置内容将被放弃」），
   所以 **一个 CSV 必须是一个完整程序块**，不能分片导入。
2. **列头随界面语言变化**：中文「步号/指令/I-O(软元件)/注解」、日文「ステップ番号/命令/...」、
   英文未知（**未实测**）。生成 CSV 时要么按目标语言出表头，要么做成可配置。
3. 导入后官方建议**执行程序检查**，工具应在返回结果里提示。
4. CSV 编码必须 **UTF-16 LE + BOM**，否则大概率读不进（实测模板即为该编码）。

**不再宣称**「把 IL 粘贴进梯形图自动生成程序」——那是错的；
**改为**「生成 GX Works2 原生可导入的 7 列程序 CSV，一键导入即得梯形图」。


---

## 附：仍未证实 / 需实测的清单

1. GX Works2 **中文版**具体菜单文案（「回路块列表显示/编辑」「写入至 CSV 文件」的中文译法）。
2. ~~「粘贴 IL → 自动生成梯形图」是否成立~~ → **已实测：不成立**（见 E1(3)）。
3. ~~「CSV → 梯形图」导入是否可行~~ → **已实测：可行**（见 E3(3)）。
4. ~~`.gxw` 前 8 字节魔数~~ → **已实测：OLE2**（见 E3(5)）。
5. 结束代码 `C05B` 及部分 `C05x` 的确切含义（官方 MC/SLMP 手册都指向模块手册，未拿到模块手册错误码表）。
6. GX Simulator2 是否存在任何文档未公开的监听端口。
7. X/Y 八进制 vs 十六进制的判定与 CPU 参数的关系（已知存在 `octalInputOutput` 这类开关，但默认值随型号而变）。
8. `0406/1406` 在「CPU 内置以太网口」上是否可用（McpX 提示可能不支持）。

---

## 参考来源汇总

### 三菱官方手册（一手，本次已抓取全文）

- **MELSEC Communication Protocol Reference Manual (SH-080008)**，500 页 — 3E/4E 帧格式、设备代码、位打包、点数上限、远程控制、错误信息结构
  https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh080008/sh080008ab.pdf 
- **SLMP Reference Manual (SH-080956)**，244 页 — 指令表、设备代码、**0406/1406 完整字段结构**
  https://dl.mitsubishielectric.co.jp/dl/fa/document/manual/plc/sh080956eng/sh080956engm.pdf 

### 三菱官方 FAQ / 官网

- GX Works2 支持语言（**无 IL**）：https://www.mitsubishielectric.com/fa/products/cnt/plceng/smerit/gx_works2/program/programming.html ；日文：https://www.mitsubishielectric.co.jp/fa/products/cnt/plceng/smerit/gx_works2/program/programming.html 
- FAQ 16267 構造化プロジェクトのサポート言語：https://fa-faq.mitsubishielectric.co.jp/faq/show/16267 
- FAQ 14643 / 38649 回路块列表显示·编辑（Ver.1.86Q+）：https://fa-faq.mitsubishielectric.co.jp/faq/show/14643 ；https://fa-faq.mitsubishielectric.co.jp/faq/show/38649 
- FAQ 22643 CSV 导入导出能力（仅注释，1.64S+）：https://fa-faq.mitsubishielectric.com/faq/show/22643 
- FAQ 14543 梯形图→文本/CSV（需 GX Developer + GX Converter）：https://fa-faq.mitsubishielectric.co.jp/faq/show/14543 
- FAQ 22508 `.gxw` 单文件格式（1.62Q+）：https://fa-faq.mitsubishielectric.com/faq/show/22508 
- FAQ 13399 MX Component 支持 GX Simulator2：https://fa-faq.mitsubishielectric.co.jp/faq/show/13399 
- FAQ 20619 GT SoftGOT2000 连 GX Simulator2：https://fa-faq.mitsubishielectric.co.jp/faq/show/20619 
- FAQ 25027 FX 系以太网端口号：https://fa-faq.mitsubishielectric.co.jp/faq/show/25027 
- GX Works2 便利机能集（连续粘贴等）：https://dl.mitsubishielectric.co.jp/dl/fa/document/catalog/plceng/l08275/l08275a.pdf 

### 一手代码 / 数据

- pymcprotocol `type3e.py`、`mcprotocolconst.py`（设备代码、位打包、远程控制报文）：
  https://raw.githubusercontent.com/senrust/pymcprotocol/main/src/pymcprotocol/type3e.py 
  https://raw.githubusercontent.com/senrust/pymcprotocol/main/src/pymcprotocol/mcprotocolconst.py 
- node mcprotocol.js（3E 帧构造、含 `S=0x98`、`octalInputOutput` 开关）：https://raw.githubusercontent.com/plcpeople/mcprotocol/master/mcprotocol.js 
- **GX Works2 真实导出 CSV 样例**：https://raw.githubusercontent.com/ito-soft-design/ladder_converter/master/test/files/src/MAIN.csv 
- GX Works2 CSV 解析项目：https://github.com/shw98-jp/gxworks2-io-list-lab 
- NetToGXSim2（GX Simulator2 不能直连的说明）：https://raw.githubusercontent.com/ismaillowkey/NetToGXSim2/main/README.md 
- gxw2-skill CSV 变量格式参考：https://raw.githubusercontent.com/Serhioromano/gxw2-skill/main/skills/gxw2-st/references/csv-variables.md 

### 二手但质量较高的技术整理

- MC 协议 3E 帧逐字节：https://mcpx.yktmr.jp/en/learn/3e-frame 
- MC 协议错误码表：https://mcpx.yktmr.jp/learn/error-codes 
- MC 指令 vs SLMP 名称对照：https://mcpx.yktmr.jp/en/learn/what-is-mc-protocol 
- 3E 帧字段速查/解码器：https://testbench.tools/tools/mc-protocol-decoder/ 
- GX Works2 工程结构（deepwiki）：https://deepwiki.com/ito-soft-design/ladder_converter/5.1-mitsubishi-gx-works2-projects 
- 内置以太网口 MC 参数配置：https://momomo-97.com/gx-works2-plc-mc-protocol-communication-settings/ 
- Q 系列远程控制指令页：https://www.manualslib.com/manual/1647180/Mitsubishi-Melsec-Q-Series.html?page=204 
- MC 软元件地址/代码参考：https://aiot.do/en/docs/melsec-plc-address-reference 
