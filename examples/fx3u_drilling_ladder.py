"""题目3的普通梯形图：M200..M207 表示步骤，M220..M227 为转移快照。"""
import json
from pathlib import Path
from examples.fx3u_drilling import drilling_spec, IO, STEPS
from gxworks2_mcp.author import compile_rung
from gxworks2_mcp.il import Program, parse_il, render_il
from gxworks2_mcp.native import write_gxw
from gxworks2_mcp.gxw import open_gxw, pou_stream


def ladder_program():
    spec = drilling_spec()
    states = {step['state']: f'M{200+i}' for i, step in enumerate(spec['steps'])}
    gate = spec['steps'][0]['transitions'][0]['when']['and'][0]
    clear_gate = spec['steps'][-1]['transitions'][0]['when']['and'][0]
    def AND(*items): return {'and': list(items)}
    def OR(*items): return {'or': list(items)}
    def NOT(item): return {'not': item}
    def replace(value):
        if value == gate: return 'M31'
        if value == clear_gate: return 'M33'
        if isinstance(value, str): return states.get(value, value)
        if isinstance(value, list): return [replace(v) for v in value]
        if isinstance(value, dict): return {k: replace(v) for k, v in value.items()}
        return value
    lines = []
    def add(out, logic, note, **options):
        rung = dict(out=out, logic=logic, comment=note, **options)
        lines.extend(render_il(Program(compile_rung(rung))).splitlines())
    add('M30', AND(NOT('X2'), NOT('M500'), NOT('M101'), NOT('M8002')), '步骤安全许可')
    add('M33', AND('M30', NOT('X1'), NOT('X10')), '步骤推进许可')
    add('M31', AND('M33', 'M0'), '加工计时许可')
    add('M32', AND('X3', 'X6'), '工件钻头均就绪')
    for step in spec['steps']:
        for action in step['actions']:
            rung = replace(action)
            rung['logic'] = {'and': [states[step['state']], rung['logic']]}
            rung['comment'] = STEPS[step['state']]
            lines.extend(render_il(Program(compile_rung(rung))).splitlines())
    # 先计算全部转移，再修改步骤位，防止同一扫描连跳。
    for i, step in enumerate(spec['steps']):
        edge = step['transitions'][0]
        when = AND('M31', 'M32') if i == 0 else replace(edge['when'])
        add(f'M{220+i}', AND(states[step['state']], when), '转入' + STEPS[edge['to']])
    for i, step in enumerate(spec['steps']):
        target = states[step['transitions'][0]['to']]
        add(states[step['state']], f'M{220+i}', '结束当前步骤', instruction='RST')
        add(target, f'M{220+i}', STEPS[step['transitions'][0]['to']], instruction='SET')
    add('M34', OR('M8002', 'M101'), '首次扫描或报警复位')
    for state in states.values():
        add(state, 'M34', '复位清除步骤', instruction='RST')
    add('M200', 'M34', '复位回到待机', instruction='SET')
    add('M40', AND(NOT('X1'), NOT('X2'), NOT('X10'), NOT('M500')), '启动按钮许可')
    add('M41', AND('M500', NOT('X2'), NOT('M8002')), '报警复位许可')
    short = {
        'M100': AND('X0', NOT('M10'), 'M40', NOT('M8002')),
        'M101': AND('X10', NOT('M11'), 'M41'),
        'M0': AND(OR('M0', 'M100'), 'M43', OR(NOT('M207'), 'X7')),
        'Y0': AND('M42', 'M22', NOT('M206')),
        'Y1': AND('M21', 'M203', 'M23'),
        'Y2': AND('M21', 'M205', 'M24'),
        'Y3': AND('M21', 'M206', NOT('M22')),
    }
    notes = dict(IO, M100='启动上升沿', M101='复位上升沿', M501='夹紧超时锁存',
                 M502='下钻超时锁存', M503='急停报警锁存', M500='总报警锁存',
                 M0='运行保持与暂停', M10='启动按钮记忆', M11='复位按钮记忆')
    for original in spec['preamble']:
        rung = replace(original)
        out = rung['out']
        if out == 'M0':
            # 报警更新后重新计算，保证故障当扫描立即切断输出。
            add('M42', AND(NOT('M500'), NOT('X2'), NOT('M101'), NOT('M8002')), '输出安全许可')
            add('M43', AND('M42', NOT('X1'), NOT('X10')), '运行保持许可')
        if out == 'Y0':
            add('M21', AND('M42', 'M0', NOT('X1'), NOT('X10')), '运动输出许可')
            add('M22', OR('M201', 'M202', 'M203', 'M204', 'M205'), '需要保持夹紧')
            add('M23', AND('X3', 'X4', NOT('X5'), NOT('M205')), '下钻工艺联锁')
            add('M24', AND(NOT('X6'), NOT('M203')), '上行工艺联锁')
        if out in short: rung['logic'] = short[out]
        rung['comment'] = notes.get(out, '清零保持型定时器')
        lines += render_il(Program(compile_rung(rung))).splitlines()
    return parse_il('\n'.join(lines + ['END']))


def generate(directory):
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    program = ladder_program()
    assert not any(i.mnemonic in {'STL', 'RET'} for i in program.instructions)
    target = root / '题目3_钻孔工位_梯形图.gxw'
    template = Path(__file__).parents[1] / 'src/gxworks2_mcp/data/fx3u-ladder.gxw'
    report = write_gxw(str(template), str(target), program)
    p = open_gxw(target)
    try:
        entry = next(e for e in p.programs() if e.name.endswith('.Program.pou'))
        read = pou_stream(p.read_stream(entry), cpu='FX3U', text_encoding=p.text_encoding)
        assert read['decoded'] and read['il'] == render_il(program)
    finally: p.close()
    (root/'梯形图指令表.il').write_text(read['il']+'\n', encoding='utf-8')
    (root/'写入核验.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    import sys
    print(json.dumps(generate(sys.argv[1]), ensure_ascii=False))
