"""比较条件使用有符号整数与原始字快照，不能将缺字补零。"""
import pytest
from gxworks2_mcp.diagnostics import diagnose_output
from gxworks2_mcp.il import parse_il


def run(source,values):
    return diagnose_output(parse_il(source+'\nOUT Y0\nEND'),'Y0',{'Y0':0,'M8000':1,**values})


@pytest.mark.parametrize('prefix,bits',[('LD',16),('LDD',32)])
@pytest.mark.parametrize('op,expected',[('=',False),('<>',True),('>',False),('>=',False),('<',True),('<=',True)])
def test_all_comparators_explain_actual_operands(prefix,bits,op,expected):
    r=run(f'{prefix}{op} D0 K10',{'D0':9,'D1':0})
    e=r['writers'][0]['evidence']
    assert e['kind']=='comparison' and e['bits']==bits and e['value'] is expected
    assert (e['leftActual'],e['rightActual'])==(9,10)


@pytest.mark.parametrize('source,values,expected',[
    ('LD< D0 K0',{'D0':65535},-1),
    ('LD< D0 K0',{'D0':32768},-32768),
    ('LDD< D0 K0',{'D0':0,'D1':32768},-2147483648),
    ('LDD< D2 K-100000',{'D2':7616,'D3':65534},-123456),
    ('LD< HFFFF K0',{},-1),
    ('LDD< HFFFFFFFF K0',{},-1),
])
def test_signed_boundaries_and_hex_constants(source,values,expected):
    e=run(source,values)['writers'][0]['evidence']
    assert e['leftActual']==expected and e['value'] is True


def test_missing_high_word_stays_unknown_and_is_requested():
    r=run('LDD>= D0 K10',{'D0':15})
    assert r['status']=='unknown' and 'D1' in r['missingDevices']
    assert {'D0','D1'}<=set(r['requiredDevices'])


@pytest.mark.parametrize('source,values,point,expected',[
    ('LD>= T0 K10',{'TN0':5,'T0':1},'TN0',5),
    ('LD>= C0 K10',{'CN0':5,'C0':1},'CN0',5),
    ('LDD>= C200 K0',{'CN200':-4,'C200':1},'CN200',-4),
])
def test_timer_and_counter_comparisons_read_current_value(source,values,point,expected):
    r=run(source,values)
    assert r['status']=='blocked' and point in r['requiredDevices']
    assert r['writers'][0]['evidence']['leftActual']==expected


@pytest.mark.parametrize('source',['LDD>= D7999 K0','LD>= R0 K10','LD= K32768 K0','LD= H10000 K0','LD>= C200 K0'])
def test_unsupported_or_out_of_range_operands_are_not_guessed(source):
    r=run(source,{})
    assert r['status']=='unknown' and 'D8000' not in r['requiredDevices']


def test_inverted_comparison_explains_required_operator():
    r=run('LD>= D0 K10\nINV',{'D0':12})
    assert r['status']=='blocked'
    assert r['writers'][0]['unmet']['requiredComparison']=='<'
