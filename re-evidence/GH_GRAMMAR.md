# GitHub 已有项目提炼的 .gxw 程序体语法（权威表，2026-10-20）

主源：monozukuri-ai/gxwlib  crates/gxw-core/src/formats/simple/catalog.rs
  https://github.com/monozukuri-ai/gxwlib （Rust，read-only；对固定 FX2N GXW/CSV 配对逐条对齐）
  以及 decoder.rs 的 operand() 设备/常量编码。

## 运算符记录（字节 -> 助记符, 元数）—— 全部源码确证
3字节 03 <op> 03 :  00=LD 01=LDI 0c=AND 0d=ANI 06=OR 07=ORI 19=ANB 18=ORB 20=OUT 23=SET 24=RST 1a=MPS 1b=MRD 1c=MPP 33=FEND 34=END
4字节 04 <op> 02 04 : 02=LDP 03=LDF 0e=ANDP 0f=ANDF 08=ORP 09=ORF ; 04 25/26 02 04=PLS/PLF ; 04 34 02 04=END
5/6字节应用指令：MOV/ADD/SUB/MUL/DIV、D 前缀 32 位变体、INC/DEC/DINC/DDEC、CJ/CALL/FMOV/BMOV/FOR/NEXT/SRET 及 P 脉冲变体（全表已并入 gxw.py 的 _OPERATORS）

## 操作数记录
触点 4B  04 <code> <addr> 04 ; >=256 的 5B  05 <code> <hi> <lo> 05 （地址按大端反读，见 gxwlib operand()）
设备码同 MC：9c=X 9d=Y a0=B b4=W a1=SB b5=SW b0=ZR 90=M 92=L 94=F 98=S a8=D af=R c2=TN c5=CN …
常量 04 e8 <v> 04=K16, 04 e9 <v> 04=K32, 05 ea <hi> <lo> 05=H16
注解记录 len+4 82 <utf8 len> len+4 （我们实测），附于线圈之后

## 与我们此前单样本推断的对照
- 我曾称的「梯级分隔符 04 02 02 04」实为 LDP 指令本体（gxwlib catalog 确证）。净效果一致（分隔符后裸触点即 LDP 梯级），但依据从「一次闭环推断」升级为「源码确证」，且 LDP/LDF/ANDP/ANDF/ORP/ORF/PLS/PLF 一并纳入。
- 03 op 03 运算符族、04 code addr 04 触点、04 34 02 04 END：一致，确认。
- FX2N 家族：gxwlib 走内层 .pou + 头判定 + tokenize，body 长度字段偏移与我们 Q 文件的 0x36 不同；我们的 Q 往返走 0x36 且 coverage 1.0。FX 老工程暂不强解（诚实报 stopReason）。
- K/H 常量、T/C 计数值记录：gxwlib 明确「binary oracle 不足、未支持」，我们同样不猜。

其它：ito-soft-design/ladder_converter 的 test/files/src/MAIN.csv 佐证 7 列 CSV 格式；gxwlib 是 read-only，我们未移植其 Rust，仅采用其语法表并用纯 Python 重实现于 gxw.py。