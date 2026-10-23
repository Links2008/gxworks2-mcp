"""机械手搬运原生 SFC：十步流程，公共回路集中保护，中文说明保存在工程旁。"""
import json
from pathlib import Path
from examples.fx3u_handling_ladder import IO, STEPS
from gxworks2_mcp.native_sfc import write_sfc_gxw, read_sfc_gxw


def handling_spec():
    def AND(*items): return {'and': list(items)}
    def OR(*items): return {'or': list(items)}
    def NOT(item): return {'not': item}
    def rung(out, logic, **options): return dict(out=out, logic=logic, **options)
    states = ['S0'] + [f'S{i}' for i in range(20, 29)]
    gate = AND('M14', NOT('X1'), NOT('X2'))
    conditions = ['M13', 'X4', 'T250', 'X5', AND('X10', 'X6'), NOT('X12'), 'X7', 'T251', 'X6', 'X11']
    actions = {
        'S21': [rung('T250', gate, preset='K10')],
        'S25': [rung('M51', 'X12')],
        'S26': [rung('T251', gate, preset='K10')],
    }
    steps = [dict(state=s, actions=actions.get(s, []),
                  transitions=[dict(to=states[(i+1) % 10], when=AND(gate, conditions[i]))])
             for i,s in enumerate(states)]
    preamble = [
        rung('M1', OR('X5', 'X6')),
        rung('M2', OR('X4', 'X7')),
        rung('M50', OR(AND('X10', 'X11'), AND('M1', 'M2'))),
        rung('M18', AND('M500', NOT('X2'), NOT('M50'), NOT('M8002'))),
        rung('M100', AND('X0', NOT('M90'), NOT('M8002'))),
        rung('M101', AND('X3', NOT('M91'), 'M18')),
        rung('M105', OR('M8002', 'M101')),
        rung('M51', 'M105', instruction='RST'),
        rung('M11', AND(NOT('X2'), NOT('M500'), NOT('M50'), NOT('M105'))),
        rung('M10', AND('M11', 'M0', NOT('X1'))),
        # 相邻移动步骤交替使用两个保持型定时器，省去转移快照和逐步清零回路。
        rung('M70', OR('S20', 'S22', 'S25', 'S27')),
        rung('M71', OR('S23', 'S28')),
        rung('T252', AND('M70', 'M10'), preset='K100'),
        rung('T253', AND('M71', 'M10'), preset='K100'),
        rung('T252', OR(NOT('M70'), 'M105'), instruction='RST'),
        rung('T253', OR(NOT('M71'), 'M105'), instruction='RST'),
        rung('M500', OR(AND('M500', NOT('M101')), 'X2', 'M50', 'M51', 'T252', 'T253')),
        rung('M12', AND(NOT('X2'), NOT('M500'), NOT('M50'), NOT('M105'))),
        rung('M13', OR(NOT('S0'), AND('S0', 'X5', 'X11'))),
        rung('M0', AND(OR('M0', 'M100'), 'M12', NOT('X1'), 'M13')),
        rung('M14', AND('M0', 'M12', NOT('X1'))),
        rung('T250', OR(NOT('S21'), 'M105', 'M500'), instruction='RST'),
        rung('T251', OR(NOT('S26'), 'M105', 'M500'), instruction='RST'),
        rung('M40', OR('S20', 'S25')),
        rung('M41', OR('S22', 'S27')),
        rung('M42', OR(AND('S20', 'X11', NOT('X4')),
                       AND('S25', 'X10', NOT('X12'), NOT('X7')))),
        rung('M43', OR(AND('S22', 'X11', NOT('X5')),
                       AND('S27', 'X10', NOT('X6')))),
        rung('M45', OR('S21', 'S22', 'S23', 'S24', 'S25')),
        rung('Y0', AND('M14', 'M42', NOT('M41'))),
        rung('Y1', AND('M14', 'M43', NOT('M40'))),
        rung('Y2', AND('M14', 'S23', NOT('X10'), NOT('S28'))),
        rung('Y3', AND('M14', 'S28', NOT('X11'), NOT('S23'))),
        rung('Y4', AND('M12', 'M45', NOT('S26'))),
        rung('Y5', AND('M14', 'S26', NOT('M45'))),
        rung('Y6', 'M500'),
        rung('M90', 'X0'), rung('M91', 'X3'),
    ]
    return dict(initial='S0', reset='M101', steps=steps, preamble=preamble)


def generate(directory, template, installation):
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    spec = handling_spec()
    target = root/'机械手自动搬运_SFC.gxw'
    report = write_sfc_gxw(str(template), str(target), spec, str(installation))
    actual = read_sfc_gxw(str(target))
    for name,data in [('原生编译核验.json', report), ('实际工程读回.json', actual),
                      ('SFC编写输入.json', spec), ('IO与步骤.json', dict(io=IO, steps={s['state']:label for s,label in zip(spec['steps'],STEPS)}))]:
        (root/name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['机械手自动搬运：原生SFC试做版', '', '步骤图：']
    lines += [f'{s["state"]}  {label}' for s,label in zip(spec['steps'],STEPS)]
    lines += ['S28完成后返回S0，自动开始下一循环。', '', 'I/O分配：']
    lines += [f'{d}\t{label}' for d,label in IO.items()]
    lines += ['', '夹紧、松开各暂定1秒；各移动步骤暂定10秒超时。停止暂停，夹紧和累计计时保留。',
              'B台有料时等待，下降遇障碍锁机。急停、限位矛盾及超时需复位。',
              '复位后人工回左上原点并确认夹钳放松，再重新启动。',
              '阀型、保压、防坠落及外部急停/反向硬接线互锁须实机确认；尚未下载到PLC。',
              '为减少PLC程序占用，中文说明放在本表，不为每条指令重复写入PLC注解。']
    (root/'步骤与IO说明.txt').write_text('\n'.join(lines)+'\n', encoding='utf-8-sig')
    (root/'公共保护回路.il').write_text(actual['blocks'][1]['steps'][0]['il']+'\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    import sys
    print(json.dumps(generate(*sys.argv[1:4]), ensure_ascii=False))
