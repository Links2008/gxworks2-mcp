# REA 逆向任务包：GX Works2 .gxw 程序正文记录格式

目的：解析 FX3U 简单工程 .gxw 内部 _hdb（嵌套 OLE2/CFB）中的程序数据流，
搞清「指令记录」与「SFC 步进记录」的字节格式（record framing / opcode / operand 编码 / trailer）。
用途为互操作学习：让开源工具在不打开 GX Works2 的情况下读懂工程。

## 样本（已知差分关系）

- A_orig / B_123456 / C_backup：同一工程三个版本（文件后缀 .bin 名为 <样本>__stream_<内层流号>.bin）。
  B 相对 A 的已知修改：程序中加入 LD X2 / AND X4 / LD X3 / AND X5 / ANB 支路，
  且 SFC 新增步 S0..S14（C 即该编辑前的备份）。因此 A 与 C 的程序流应完全一致，
  A→B 的字节增量 = 该编辑的编码结果（流12 +908B，变化起于 offset 54；流49 尾部变化）。
- Q_demo1：QCPU(Q模式) 简单工程，MAIN 已知：LD X0/ANI X1/OUT Y0、LD X1/OUT Y1、END（7步）。
  用于对照 Q 与 FX 记录族。

## 已确证（纯 Python + olefile 实测，见 gxworks2-mcp/docs/DIRECT_WRITE.md / RESEARCH.md）

1. .gxw = OLE2；流 _hdb 本身是完整 OLE2（46~48 个数字命名流，全部刻出在本目录）。
2. 程序实体不在外层 XML（projectdatalist 等只是 MS Access diffgram 清单）。
3. 内层流 31/32/33 = Standard Jet DB（带口令；ACE 引擎空口令被拒，已实测）。
4. 流 12/49 中反复出现 0x90/0x9c/0x9d —— 与 MC 协议 M/X/Y 设备码一致；
   频繁模式：05 6c xx xx xx / 04 9c xx xx / 03 xx xx（疑似 tag 03/04/05 TLV，
   但 body[0]=0x10 使朴素长度模型无法从头走通；帧起点/长度规则待逆向）。
5. 流 12 头 54 字节含 07ea（=2026 小端）等字段；流 29 首字节疑似计数。

## 需要 REA 给出

- 流 12 的记录帧结构：头布局、指令记录切分、operand 编码（设备类+号→90/9c/9d+地址字节；
  K/H 常量；T/C 记录）。用 A→B 差分与已知编辑（X2 AND X4 / X3 AND X5 / ANB）交叉验证。
- 流 49 的 SFC 记录：step(S0..S14)/transition(action) 如何编码、与流 12 的 STL/RET 关系。
- 长度/trailer：POU 长度字段与收尾记录如何校验与更新（为将来写入做准备）。
- 输出格式：字段表 + 每条结论标（确证/推断）+ 可复跑的验证思路（Python 优先）。

## 已知同类工作（避免重复造轮子）

- gxwlib（Rust，只读）docs：记录族命名 simple-le-observed-v1 / v2-trailer24；
  支持子集 LD/LDI/AND/ANI/OR/ORI/OUT/SET/RST/ANB/ORB/MPS/MRD/MPP/END；
  X/Y 八进制紧凑地址、M/D 一/两字节紧凑地址、K 0-127 紧凑形式。
  https://github.com/monozukuri-ai/gxwlib/blob/main/docs/instructions.md
## REA 实测进展（2026-10-19 20:3x，rea@6.7.0 已装并注册进 DSH）

- REA 对 .gxw 报 unsupported binary format（预期：它面向可执行文件）。
  正确用法改为两条：① 逆向 GD2.exe/ed_*.dll（记录格式的实现者）——需要深度引擎，
  Windows 上 Hopper 自动安装不支持，要 Ghidra 12.1.x+JDK 或 IDA（本机都没有，未擅自安装）；
  ② 用 REA 无关的字节级分析 + 已知差分，自己解（已出结果，见下）。
- 字符串扫描（免引擎）：GD2.exe 内确认存在 SystemLabel/CallTree/STL/LD 等表名与助记符字符串；
  ed_st.dll 含 STL/RET；ed_sfc.dll 无任何明纹（SFC 数据纯二进制）。
- **锚点即记录**：A_orig stream16(Block.Program.pou) 第一个记录 size=20；B_123456 同一 offset 同一记录 size=36。
  B 多出的 16 字节正是已知编辑的指令编码，逐字节可见：
  `03 00 | 03 05 90 41 1f 05 | 03 20 | 03 05 90 1f 03 05`（对应 LD X2/AND X4/LD X3/AND X5/ANB 类支路，
  03=短记录 04=带操作数记录 05=块分隔；0x90/0x9d 与 MC 协议 M/Y 设备码一致，X=0x9c）。
  A 的原始 20 字节记录 = 同一 TLV 语法的更短前缀；两文件中其余 18 个锚点字节内容逐一对应。
- Q 项目（demo1）：MAIN.Program.pou 仅 107 字节（Q 的指令在 MAIN.res=stream12，16.4KB），
  stream12 的 54 字节头 + TLV 记录在 A/B 差分中同样吻合 => Q/FX 同族语法、不同容器位置。

### 下一步（已可离线做，不依赖引擎）
1. 把 03/04/05 TLV 语法写成解码器：03 <u8> | 04 <code> <addr...> | 05 <n>，用 A→B 与 Q-demo1-CSV→gxw 三向验证；
2. 遇到解不动的记录保留原字节并降 coverage（学 REA 的 evidence/limitations 契约）；
3. 解码器进 gxw.py（pou_anchors -> pou_instructions），README 的 programBodiesDecoded 才开始 >0。