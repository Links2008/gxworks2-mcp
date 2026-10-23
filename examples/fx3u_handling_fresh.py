"""按照片和已确认 I/O 独立重写的机械手 SFC，不导入上一版工艺定义。"""
import json
import sys
from pathlib import Path
from gxworks2_mcp.native_sfc import write_sfc_gxw, read_sfc_gxw


def build_spec():
    def a(*x): return {'and': list(x)}
    def o(*x): return {'or': list(x)}
    def n(x): return {'not': x}
    def r(device, condition, **kw): return dict(out=device, logic=condition, **kw)
    states = ['S0', 'S20', 'S21', 'S22', 'S23', 'S24', 'S25', 'S26', 'S27', 'S28']
    # 步骤使用上一扫描运行许可，并直接检查本扫描停止和急停输入。
    advance = a('M9', n('X1'), n('X2'))
    finish = ['M10', 'X4', 'T250', 'X5', a('X10', 'X6'), n('X12'), 'X7', 'T251', 'X6', 'X11']
    steps = []
    for i, state in enumerate(states):
        action = [r('T250', advance, preset='K10')] if state == 'S21' else []
        if state == 'S26': action = [r('T251', advance, preset='K10')]
        steps.append(dict(state=state, actions=action,
                          transitions=[dict(to=states[(i+1) % 10], when=a(advance, finish[i]))]))
    common = [
        r('M1', o('X5', 'X6')), r('M2', o('X4', 'X7')),
        r('M3', o(a('X10', 'X11'), a('M1', 'M2'))),
        r('M11', a('M500', n('X2'), n('M3'), n('M8002'))),
        r('M110', a('X0', n('M90'), n('M8002'))),
        r('M111', a('X3', n('M91'), 'M11')),
        r('M5', o('M8002', 'M111')),
        r('M6', a(n('X2'), n('M500'), n('M3'), n('M5'))),
        r('M7', a('M6', 'M0', n('X1'))),
        # 垂直移动与水平移动交替计时，退出该类步骤时清零，暂停保留累计值。
        r('M30', o('S20', 'S22', 'S25', 'S27')),
        r('M31', o('S23', 'S28')),
        r('T252', a('M30', 'M7'), preset='K100'),
        r('T253', a('M31', 'M7'), preset='K100'),
        r('T252', o(n('M30'), 'M5'), instruction='RST'),
        r('T253', o(n('M31'), 'M5'), instruction='RST'),
        # 保留上一扫描下降阶段，避免到下限与障碍同时出现时漏报。
        r('M4', a(o('S25', 'M40'), 'X12')),
        r('M500', o(a('M500', n('M111')), 'X2', 'M3', 'M4', 'T252', 'T253')),
        r('M8', a(n('X2'), n('M500'), n('M3'), n('M5'))),
        r('M10', o(n('S0'), a('X5', 'X11'))),
        r('M0', a(o('M0', 'M110'), 'M8', n('X1'), 'M10')),
        r('M9', a('M0', 'M8')),
        r('T250', o(n('S21'), 'M5', 'M500'), instruction='RST'),
        r('T251', o(n('S26'), 'M5', 'M500'), instruction='RST'),
        r('M20', o('S21', 'S22', 'S23', 'S24', 'S25')),
        r('M21', o(a('S20', 'X11', n('X4')), a('S25', 'X10', n('X7'), n('X12')))),
        r('M22', o(a('S22', 'X11', n('X5')), a('S27', 'X10', n('X6')))),
        r('M23', o('S22', 'S27')), r('M24', o('S20', 'S25')),
        r('Y0', a('M9', 'M21', n('M23'))), r('Y1', a('M9', 'M22', n('M24'))),
        r('Y2', a('M9', 'S23', n('X10'), n('S28'))),
        r('Y3', a('M9', 'S28', n('X11'), n('S23'))),
        r('Y4', a('M8', 'M20', n('S26'))), r('Y5', a('M9', 'S26', n('M20'))),
        r('Y6', 'M500'), r('M40', 'S25'), r('M90', 'X0'), r('M91', 'X3'),
    ]
    return dict(initial='S0', reset='M111', steps=steps, preamble=common)


def generate(directory, template, installation):
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    spec = build_spec()
    report = write_sfc_gxw(template, str(root/'机械手_独立重写_SFC.gxw'), spec, installation)
    actual = read_sfc_gxw(str(root/'机械手_独立重写_SFC.gxw'))
    for name, value in [('独立工艺定义.json', spec), ('原生核验.json', report), ('工程读回.json', actual)]:
        (root/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    print(json.dumps(generate(*sys.argv[1:4]), ensure_ascii=False))
