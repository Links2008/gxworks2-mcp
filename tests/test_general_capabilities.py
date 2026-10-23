"""通用能力必须区分三菱语义，并用可控时间验证真实时序约束。"""
from contextlib import contextmanager
import struct
import pytest
from gxworks2_mcp import simulator as sim
from gxworks2_mcp.analysis import interlock_summary, trace_device
from gxworks2_mcp.author import BuildError, compile_logic, compile_rungs
from gxworks2_mcp.il import parse_il, render_il
from gxworks2_mcp.native import encode_program
from gxworks2_mcp.validate import validate_program


def codes(source):
    return {x.code for x in validate_program(parse_il(source), cpu='FX3U')}


def test_latch_pair_is_not_an_ordinary_double_coil():
    p = parse_il('LD X0\nSET M0\nLD X1\nRST M0\nLD M0\nOUT Y0\nEND')
    assert 'DOUBLE_COIL' not in {x.code for x in validate_program(p, cpu='FX3U')}
    assert 'M0' not in interlock_summary(p)['multiWriterCoils']
    assert 'last OUT wins' not in str(trace_device(p, 'M0'))


def test_stl_output_reuse_and_unsafe_same_state_are_distinguished():
    prefix='LD M8002\nSET S0\nSTL S0\nOUT Y0\nLD X0\nSET S20\nSTL S20\n'
    assert 'DOUBLE_COIL' not in codes(prefix+'OUT Y0\nRET\nEND')
    assert 'DOUBLE_COIL' in codes(prefix+'OUT Y0\nOUT Y0\nRET\nEND')
    assert 'DOUBLE_COIL' in codes('LD X0\nOUT Y0\nLD X1\nOUT Y0\nEND')


def test_adjacent_timer_reuse_and_missing_transition_are_reported():
    src='LD M8002\nSET S0\nSTL S0\nOUT T0 K10\nLD X0\nSET S20\nSTL S20\nOUT T0 K20\nLD X1\nSET S99\nRET\nEND'
    assert {'STL_TIMER_REUSE', 'STL_TARGET_UNDEFINED'} <= codes(src)


@pytest.mark.parametrize('op,args', [('MOV',['K-10','D0']),('DMOV',['D0','D2']),
    ('ADD',['D0','K2','D4']),('SUB',['D0','K2','D4']),('MUL',['D0','K2','D4']),
    ('DIV',['D0','K2','D4']),('DADD',['D0','D2','D4']),('CMP',['D0','K2','M100']),
    ('INC',['D0']),('DEC',['D0']),('DSUB',['D0','D2','D4']),
    ('DMUL',['D0','D2','D4']),('DDIV',['D0','D2','D4']),
    ('DCMP',['D0','K-100000','M100']),('DINC',['D0']),('DDEC',['D0'])])
def test_structured_data_actions_encode(op,args):
    p=compile_rungs([{'instruction':op,'operands':args,'logic':'X0'}])
    assert op+' '+' '.join(args) in render_il(p)
    assert encode_program(p)


@pytest.mark.parametrize('op',['=','<>','>','>=','<','<='])
def test_comparison_contacts_form_real_logic(op):
    node={'and':['X0',{'compare':{'op':op,'left':'D0','right':'K10'}}]}
    p=compile_rungs([{'logic':node,'out':'Y0'}])
    assert 'AND'+op+' D0 K10' in render_il(p)
    assert not [x for x in validate_program(p,cpu='FX3U') if x.severity=='error']
    assert encode_program(p)


@pytest.mark.parametrize('rung',[
    {'instruction':'MOV','operands':['K1\nOUT Y0','D0']},
    {'instruction':'MOV','operands':['K1','X0']},
    {'instruction':'ADD','operands':['D0','D1']},
    {'instruction':'INC','operands':['D0'],'out':'D1'},
    {'instruction':'MADEUP','operands':['D0']},
    {'out':'Y0','logic':'X0\nOUT Y1'},
    {'out':'Y0','logic':{'not':'X0\nOUT Y1'}},
    {'instruction':'MOV','operands':['K1','D0'],'comment':'说明\nOUT Y1'},
])
def test_ambiguous_or_injected_actions_are_rejected(rung):
    with pytest.raises(BuildError):compile_rungs([rung])


def test_typed_register_and_counter_reads(monkeypatch):
    image={0x2000:struct.pack('<h',-123),0x2004:struct.pack('<i',-123456),
           0x1640:struct.pack('<H',3),0x17d0:struct.pack('<i',-7),0x1600:b'\x01\x00'}
    @contextmanager
    def memory(write=False):
        yield {'pid':7},0,lambda addr,size:image[addr][:size],None
    monkeypatch.setattr(sim,'memory',memory)
    v=sim.read_devices(['D0','D2','CN0','CN200','C0'],types={'D0':'int16','D2':'int32'})['values']
    assert v=={'D0':-123,'D2':-123456,'CN0':3,'CN200':-7,'C0':1}


@pytest.fixture
def clocked(monkeypatch):
    clock=[0.0]; actions=[]
    monkeypatch.setattr(sim.time,'perf_counter',lambda:clock[0])
    monkeypatch.setattr(sim.time,'sleep',lambda s:clock.__setitem__(0,clock[0]+s))
    monkeypatch.setattr(sim,'target',lambda:{'pid':7})
    monkeypatch.setattr(sim,'write_inputs',lambda x:actions.append(x))
    def install(fn):
        monkeypatch.setattr(sim,'read_devices',lambda devices,**kw:{'pid':7,'values':{d:fn(d,clock[0]) for d in devices}})
    return clock,actions,install


def test_hold_catches_a_later_drop(clocked):
    _,_,install=clocked;install(lambda d,t:int(t<.04))
    r=sim.verify([{'expect':{'Y0':1},'hold_ms':100}])
    assert not r['ok'] and r['trace'][0]['reason']=='hold_broken'


def test_early_transition_is_a_failure(clocked):
    _,_,install=clocked;install(lambda d,t:1)
    r=sim.verify([{'expect':{'Y0':1},'min_delay_ms':60}])
    assert not r['ok'] and r['trace'][0]['reason']=='too_early'


def test_global_forbidden_combination_is_sampled_during_wait(clocked):
    _,_,install=clocked;install(lambda d,t:1 if d!='M0' else 0)
    r=sim.verify([{'expect':{'M0':1}}],forbid=[{'Y0':1,'Y1':1}])
    assert not r['ok'] and r['trace'][0]['reason']=='forbidden_combination'


def test_order_hold_and_no_following_actions_after_failure(clocked):
    _,actions,install=clocked;install(lambda d,t:int(t>=.06) if d=='Y0' else 0)
    r=sim.verify([{'inputs':{'X0':1},'expect':{'Y0':1},'min_delay_ms':40,'hold_ms':40},
                  {'expect':{'Y1':1},'timeout_ms':60},
                  {'inputs':{'X1':1},'expect':{'Y0':0}}])
    assert not r['ok'] and len(r['trace'])==2 and actions==[{'X0':1}]
    assert r['trace'][0]['passed'] and r['trace'][1]['reason']=='timeout'


def test_entire_batch_is_checked_before_input_changes(clocked):
    _,actions,_=clocked
    with pytest.raises(ValueError):sim.verify([{'inputs':{'X0':1},'expect':{'Y0':1}},
                                              {'expect':{'Y0':1},'hold_ms':True}])
    assert not actions


@pytest.mark.parametrize('op,args',[
    ('MOV',['K32768','D0']),('DMOV',['K2147483648','D0']),
    ('DMOV',['D7999','D0']),('MUL',['D0','K2','D7999']),
    ('DMUL',['D0','D2','D7997']),('CMP',['D0','K2','M7678']),
])
def test_data_width_and_implicit_result_bounds(op,args):
    with pytest.raises(BuildError):compile_rungs([{'instruction':op,'operands':args}])


@pytest.mark.parametrize('value',['K-2147483648','K2147483647','K-100000','HFFFFFFFF'])
def test_wide_constants_roundtrip(value):
    from gxworks2_mcp.gxw import main_res_program
    p=compile_rungs([{'instruction':'DMOV','operands':[value,'D0'],'logic':'X0'}])
    raw=encode_program(p)
    r=main_res_program(struct.pack('<I',len(raw))+raw,body_len_offset=0,cpu='FX3U')
    assert r['decoded'] and value in r['il']


@pytest.mark.parametrize('device,dtype',[('D7999','int32'),('CN0','int32'),('CN200','int16'),
                                       ('X0','uint16'),('D0',{}),('D0','float32')])
def test_bad_read_types_do_not_open_memory(monkeypatch,device,dtype):
    monkeypatch.setattr(sim,'memory',lambda *_:pytest.fail('参数错误不得打开内存'))
    with pytest.raises(ValueError):sim.read_devices([device],types={device:dtype})


@pytest.mark.parametrize('options',[
    {'hold_ms':-1},{'hold_ms':10001},{'min_delay_ms':3000,'timeout_ms':100},
    {'types':{'Y0':'int32'}},{'timeout_ms':False},{'extra':1},
])
def test_invalid_time_batch_cannot_write(clocked,options):
    _,actions,_=clocked
    with pytest.raises(ValueError):sim.verify([dict(inputs={'X0':1},expect={'Y0':1},**options)])
    assert not actions
