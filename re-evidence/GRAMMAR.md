# MAIN.res 程序体语法（由「已知 IL 对撞真实字节」得出，2026-10-20）

样本：同一工程保存前后两份 MAIN.res（内层流 12）：
- Q_demo1__stream_12.bin = 2步程序（仅 END），108 字节
- D_saved__stream_12.bin = 已知 IL 程序，208 字节（本次由 MCP 闭环写入）:
  LD X0 / ANI X1 / LD Y0 / ANI X1 / ORB / OUT Y0  // motor
  LDP X2 / AND M20 / OUT Y1                       // alarm
  LD X3 / OUT Y2                                  // flag
  END

布局：
- 0x00..0x35 头部（0x22: ea07=2026、0a=10月等保存日期；0x26..0x31 四组 u16）
- 0x36: u32 程序体长度 L；0x3A 起 L 字节 = token 流；其后 00 填充 + 固定名称记录(01|len|'MAIN'utf16|04|01)
- 空程序 token 流 = 04 34 02 04（即 END）

已证实 token 语法（在两份文件里逐字节对撞成立）：
- 触点/线圈记录: 04 <code> <addr> 04（9c=X 9d=Y 90=M；X/Y 用十六进制编号，M 十进制）
- 运算符（包裹为 03 <op> 03，程序开头的 LD 为 03 00 + 紧跟触点）:
  00=LD, 0c=AND, 0d=ANI, 18=ORB, 20=OUT
- 梯级分隔: 04 02 02 04
- 注解(线圈注释): 09 82 <len> <utf8 text> 09（例 09 82 05 'motor' 09）
- END: 04 34 02 04（34=END）

验证（新文件 0x3A 起逐 token）:
03 00 | 04 9c 00 04 -> LD X0; 03 0d 03 | 04 9c 01 04 -> ANI X1;
03 00 03 | 04 9d 00 04 -> LD Y0（第二块）; 03 0d 03 | 04 9c 01 04 -> ANI X1;
03 18 03 -> ORB; 03 20 03 | 04 9d 00 04 -> OUT Y0; 09 82 05 motor 09 -> 注解

未破/未验证：LDP 上升沿编码（X2 梯级在分隔符后直接 04 9c 02 04，边沿标志疑在分隔符变体或独立前缀）;
定时器/计数器；地址 >=256 的 16 位扩展（FX 样本出现过 05 <code> <lo> <hi> 05 变体）; 步数字段 0x26..0x31;
写回（需同步 4 处长度字段与 0982 校验串，尚未尝试）。遇到无法识别 token 一律停止并报 stopReason，绝不猜测。
闭环状态：gx_gxw_open 能把已知工程读成 program.il 并可继续编辑 → gx_import_csv 再写回（步数 2→28 已实测）。

权威字节表见 GH_GRAMMAR.md（取自 gxwlib catalog.rs，已并入 gxw.py 的 _OPERATORS）。

## FX 结构化工程 .pou 的实测边界（2026-10-20 下午）
- gxwlib 的 simple-le-observed-v1 只覆盖**简单程序 MAIN.res**（START=0x4f 门）；
- FX3U_Manipulator 的 Block/Block1 .pou 记录体（28B 帧内）是**步级 token 片段 + 尾部元数据**
  （例如 `03 00 03 05 90 41 1f 05 03 20 03 …` 用 5 字节 M 操作数，后跟指针/长度区），
  pou_program 按整记录判定 coverage 未过——这是**已知未破**，不是解码器缺陷；
- 下一步（需要时）：按片段容错解码 + 元数据区分离，或对 GD2.exe 写 .pou 的函数用 REA 定死。
## .pou（结构化工程）解码已破 —— 2026-10-20

三种真实工程（Q 结构化 / FX3U / 电镀生产线）逐字节对撞得出：

- 记录帧（28 字节）：`01 00 00 00 | size u32 | size u32 | 01 00 00 00 | 0c 00 00 00 | 00*4 | ff*4` + body[size]
- 每个 body = `[token 流] + [元数据尾]`，尾为 `01 00 00 00 [u32]? 21 00 00 00 <UTF-16 哈希串>`
- 元数据尾不含梯形图数据，剥离后 token 流用同一张 _OPERATORS/_CONTACT_CODES 表解码
- 网络之间无 END 记录（这是之前端到端解码失败的原因），逐网络切分拼接
- 简单工程（demo1）的 .pou 仍是连续流 + 单 END，两条路径都支持

实测覆盖率：demo1 1.0；电镀生产线 Block 0.784 / Block1 1.0 / 手动程序 0.5；
FX3U Block 0.895 / Block1 1.0。未解码网络为含未入表记录（如 04 21 03 04 定时器输出），
计数跳过、绝不猜。