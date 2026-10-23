"""照片题机械手自动搬运：普通短回路梯形图，AI 编写工艺，复用原生写入接口。"""
import json
from pathlib import Path
from gxworks2_mcp.author import compile_rungs
from gxworks2_mcp.gxw import open_gxw, pou_stream
from gxworks2_mcp.il import render_il
from gxworks2_mcp.native import write_gxw


IO = {
    'X0': '启动或暂停后继续', 'X1': '停止暂停，按下为1', 'X2': '急停触发为1',
    'X3': '报警复位', 'X4': 'SQ1：A台下位', 'X5': 'SQ2：A台上位',
    'X6': 'SQ3：B台上位', 'X7': 'SQ4：B台下位', 'X10': 'SQ5：右限位',
    'X11': 'SQ6：左限位', 'X12': 'B台有工件为1',
    'Y0': '下降中间继电器', 'Y1': '上升中间继电器', 'Y2': '右移中间继电器',
    'Y3': '左移中间继电器', 'Y4': '夹紧中间继电器', 'Y5': '松开中间继电器', 'Y6': '报警',
}
STEPS = ['原点待机', 'A台下降取料', '夹紧等待一秒', '夹紧后上升', '右移到B台',
         '等待B台无工件', 'B台下降放料', '松开等待一秒', '放料后上升', '左移返回原点']


def ladder_program():
    def AND(*items): return {'and': list(items)}
    def OR(*items): return {'or': list(items)}
    def NOT(item): return {'not': item}
    rungs = []
    def add(out, logic, note, **options):
        rungs.append(dict(out=out, logic=logic, comment=note, **options))

    add('M1', OR('X5', 'X6'), '任一上位信号')
    add('M2', OR('X4', 'X7'), '任一下位信号')
    add('M50', OR(AND('X10', 'X11'), AND('M1', 'M2')), '矛盾限位报警条件')
    add('M18', AND('M500', NOT('X2'), NOT('M50'), NOT('M8002')), '允许人工复位')
    add('M100', AND('X0', NOT('M90'), NOT('M8002')), '启动上升沿')
    add('M101', AND('X3', NOT('M91'), 'M18'), '复位上升沿')
    add('M105', OR('M8002', 'M101'), '初始化或报警复位')
    for i in range(10):
        add(f'M{200+i}', 'M105', '复位清除当前步骤', instruction='RST')
    add('M200', 'M105', '进入原点待机', instruction='SET')
    for t in ('T250', 'T251', 'T252'):
        add(t, 'M105', '初始化清零定时器', instruction='RST')

    add('M11', AND(NOT('X2'), NOT('M500'), NOT('M50'), NOT('M105')), '计时安全许可')
    add('M10', AND('M11', 'M0', NOT('X1')), '计时运行许可')
    add('M44', OR('M201', 'M203', 'M204', 'M206', 'M208', 'M209'), '当前为移动步骤')
    add('T250', AND('M202', 'M10'), '夹紧确认暂定一秒', preset='K10')
    add('T251', AND('M207', 'M10'), '松开确认暂定一秒', preset='K10')
    add('T252', AND('M44', 'M10'), '移动超时暂定十秒', preset='K100')
    add('M51', AND('M206', 'X12'), '下降时B台出现工件')
    add('M500', OR(AND('M500', NOT('M101')), 'X2', 'M50', 'M51', 'T252'), '故障锁存必须复位')
    # 定时与报警先计算，新故障在本扫描立即切断输出。
    add('M12', AND(NOT('X2'), NOT('M500'), NOT('M50'), NOT('M105')), '输出安全许可')
    add('M13', OR(NOT('M200'), AND('M200', 'X5', 'X11')), '待机启动必须在原点')
    add('M0', AND(OR('M0', 'M100'), 'M12', NOT('X1'), 'M13'), '自动循环运行保持')
    add('M14', AND('M0', 'M12', NOT('X1')), '动作与转步许可')
    add('M15', AND('X6', 'X10', NOT('X12')), 'B台上位且无工件')

    conditions = [AND('X5', 'X11'), 'X4', 'T250', 'X5', 'X10', 'M15', 'X7', 'T251', 'X6', 'X11']
    # 全部转移先取快照，禁止同一扫描跨越多个步骤。
    for i, condition in enumerate(conditions):
        add(f'M{220+i}', AND(f'M{200+i}', 'M14', condition), '转入' + STEPS[(i+1) % 10])
    for i in range(10):
        add(f'M{200+i}', f'M{220+i}', '退出' + STEPS[i], instruction='RST')
        add(f'M{200+(i+1)%10}', f'M{220+i}', STEPS[(i+1) % 10], instruction='SET')
    add('T250', OR(NOT('M202'), 'M500'), '离开夹紧步骤清零', instruction='RST')
    add('T251', OR(NOT('M207'), 'M500'), '离开松开步骤清零', instruction='RST')
    add('M16', OR(*(f'M{220+i}' for i in range(10))), '本扫描发生步骤转换')
    add('T252', OR('M16', NOT('M44'), 'M500'), '每个移动步骤重新计时', instruction='RST')

    add('M40', OR('M201', 'M206'), '下降步骤请求')
    add('M41', OR('M203', 'M208'), '上升步骤请求')
    add('M42', OR(AND('M201', 'X11', NOT('X4')),
                  AND('M206', 'X10', NOT('X12'), NOT('X7'))), '下降位置与防碰许可')
    add('M43', OR(AND('M203', 'X11', NOT('X5')),
                  AND('M208', 'X10', NOT('X6'))), '上升位置与限位许可')
    add('M45', OR('M202', 'M203', 'M204', 'M205', 'M206'), '搬运期间保持夹紧')
    add('Y0', AND('M14', 'M42', NOT('M41')), '下降，禁止同时上升')
    add('Y1', AND('M14', 'M43', NOT('M40')), '上升，禁止同时下降')
    add('Y2', AND('M14', 'M204', NOT('X10'), NOT('M209')), '右移，到右限即停止')
    add('Y3', AND('M14', 'M209', NOT('X11'), NOT('M204')), '左移，到左限即停止')
    add('Y4', AND('M12', 'M45', NOT('M207')), '夹紧，暂停时仍保持')
    add('Y5', AND('M14', 'M207', NOT('M45')), '松开，与夹紧互锁')
    add('Y6', 'M500', '故障报警指示')
    add('M90', 'X0', '记忆启动按钮')
    add('M91', 'X3', '记忆复位按钮')
    return compile_rungs(rungs)


def generate(directory):
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    program = ladder_program()
    target = root/'机械手自动搬运_梯形图.gxw'
    template = Path(__file__).parents[1]/'src/gxworks2_mcp/data/fx3u-ladder.gxw'
    report = write_gxw(str(template), str(target), program)
    p = open_gxw(target)
    try:
        entry = next(e for e in p.programs() if e.name.endswith('.Program.pou'))
        read = pou_stream(p.read_stream(entry), cpu='FX3U', text_encoding=p.text_encoding)
        assert read['decoded'] and read['il'] == render_il(program)
    finally: p.close()
    (root/'机械手_指令表.il').write_text(read['il']+'\n', encoding='utf-8')
    (root/'IO与步骤.json').write_text(json.dumps(dict(io=IO, steps={f'M{200+i}': s for i,s in enumerate(STEPS)}), ensure_ascii=False, indent=2), encoding='utf-8')
    (root/'写入核验.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['机械手自动搬运试做版：FX3U / GX Works2', '', 'I/O 分配（输入有效为1）']
    lines += [f'{d}\t{name}' for d,name in IO.items()]
    lines += ['', '步骤顺序'] + [f'M{200+i}\t{name}' for i,name in enumerate(STEPS)]
    lines += ['', '使用约定', '自动循环；停止为暂停，保留步骤及定时累计值，搬运期间维持夹紧。',
              'B台有料时在右上方等待；下降途中出现工件则锁存故障。',
              '夹紧、松开各暂定1秒，各移动步骤暂定10秒超时，均需现场校准。',
              '急停、矛盾限位、移动超时和下降防碰触发报警。复位回待机，人工回原点后重新启动。',
              '没有夹钳位置反馈，启动前必须人工确认夹钳已放松，左限及A台上位有效。',
              '输出通过中间继电器控制电磁阀。阀型、保压、防坠落、硬接线急停及反向互锁须实物确认。',
              '此为离线试做工程，没有下载到实体PLC，也不包含已核实的电源与阀端子接线图。']
    (root/'编程说明与IO表.txt').write_text('\n'.join(lines)+'\n', encoding='utf-8-sig')
    return report


if __name__ == '__main__':
    import sys
    print(json.dumps(generate(sys.argv[1]), ensure_ascii=False))
