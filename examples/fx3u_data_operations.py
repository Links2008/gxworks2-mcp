"""通用数据指令验收：常量与运算期望独立固定，不驱动实体设备。"""


def build_spec():
    def action(op,*args):
        return dict(instruction=op,operands=list(args),logic='M8002')
    common=[
        action('MOV','K-123','D0'),action('DMOV','K-123456','D2'),
        action('MOV','K12','D10'),action('MOV','K3','D11'),
        action('ADD','D10','D11','D12'),action('SUB','D10','D11','D13'),
        action('MUL','D10','D11','D14'),action('DIV','D10','D11','D16'),
        action('DADD','D2','K500','D20'),action('DSUB','D2','K100','D22'),
        action('DMUL','D2','K2','D24'),action('DDIV','D2','K2','D28'),
        action('CMP','D10','D11','M100'),action('DCMP','D2','K-100000','M103'),
        action('MOV','K10','D40'),action('INC','D40'),action('DEC','D40'),
        action('DMOV','K-100000','D42'),action('DINC','D42'),action('DDEC','D42'),
        dict(out='C0',instruction='RST',logic='M8002'),
        dict(out='C200',instruction='RST',logic={'or':['M8002','X6']}),
        dict(out='C0',logic='X3',preset='K3'),dict(out='C200',logic='X4',preset='K2'),
        dict(out='M10',logic='X3'),dict(out='M11',logic='X4'),
        dict(out='T0',logic='X7',preset='K2'),dict(out='T1',logic='X12',preset='K15'),
        dict(out='Y0',logic={'and':['X7',{'not':'X10'}]}),
        dict(out='Y1',logic={'and':['X10',{'not':'X7'}]}),
        dict(out='Y2',logic='T0'),dict(out='Y3',logic={'and':['X12',{'not':'T1'}]}),
        dict(out='Y4',logic={'compare':{'op':'<','left':'D2','right':'K-100000','bits':32}}),
    ]
    # 技能前向样例：X20启动、X21停止、X22故障、X23复位、X24手动、X25点动。
    def a(*x):return {'and':list(x)}
    def n(x):return {'not':x}
    common += [
        dict(out='M304',logic=a(n('M301'),n('X22'))),
        dict(out='M303',logic=a('X20',n('M302'),n('M8002'))),
        dict(out='M305',logic=a('X23',n('X22'),n('X21'))),
        dict(out='M301',logic={'or':['X22',a('M301',n('M305'))]}),
        dict(out='M300',logic=a({'or':['M300','M303']},'M304',n('M301'),n('X21'),n('X24'),n('M8002'))),
        dict(out='Y20',logic=a(n('M301'),n('X21'),{'or':[a('M300',n('X24')),a('X24','X25')]})),
        dict(out='Y21',logic='M301'),dict(out='M302',logic='X20'),
    ]
    return dict(initial='S0',preamble=common,steps=[
        dict(state='S0',actions=[],transitions=[dict(to='S20',when='X0')]),
        dict(state='S20',actions=[dict(instruction='DMOV',operands=['D2','D50'])],
             transitions=[dict(to='S0',when={'and':['X1',{'compare':{'op':'>=','left':'D12','right':'K15'}}]})]),
    ])
