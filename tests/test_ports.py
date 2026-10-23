"""只读枚举、蓝牙分类、候选端口查找及系统查询失败的边界。"""
import json
import socket
import sys
from types import SimpleNamespace
import pytest
from gxworks2_mcp.mcprotocol import MCClient, MCConfig, MCProtocolError, probe
from gxworks2_mcp.ports import find_ports, list_ports


def test_registry_inventory_is_not_a_plc_probe(monkeypatch):
    class Key:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    rows = [(r'\Device\BthModem0', 'COM6', 1), (r'\Device\USBSER000', 'COM12', 1),
            (r'\Device\Serial0', 'COM2', 1), ('invalid', 'LPT1', 1)]
    registry = SimpleNamespace(HKEY_LOCAL_MACHINE=1, REG_SZ=1,
        OpenKey=lambda *a: Key(), QueryInfoKey=lambda k: (0, len(rows), 0), EnumValue=lambda k, i: rows[i])
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'winreg', registry)
    result = list_ports()
    assert [p['port'] for p in result['ports']] == ['COM2', 'COM6', 'COM12']
    assert result['ports'][1]['transport'] == 'bluetooth'
    assert all(not p['plcVerified'] for p in result['ports'])
    assert result['openedPorts'] == result['writes'] == 0


def test_access_denied_is_not_reported_as_empty_inventory(monkeypatch):
    def denied(*args): raise PermissionError('denied')
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'winreg', SimpleNamespace(HKEY_LOCAL_MACHINE=1, OpenKey=denied))
    with pytest.raises(PermissionError): list_ports()


def test_non_string_and_duplicate_com_entries_do_not_crash_or_select_a_plc(monkeypatch):
    class Key:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    rows = [
        (r'\Device\BthModem0', ' com6 ', 1),
        (r'\Device\USBSER000', 'COM6', 1),
        (r'\Device\BTHENUM{abc}', 'COM4', 1),
        (None, 12, 1),
        (r'\Device\Serial0', 'LPT1', 1),
    ]
    registry = SimpleNamespace(HKEY_LOCAL_MACHINE=1, REG_SZ=1,
        OpenKey=lambda *a: Key(), QueryInfoKey=lambda k: (0, len(rows), 0), EnumValue=lambda k, i: rows[i])
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setitem(sys.modules, 'winreg', registry)
    result = list_ports()
    assert [p['port'] for p in result['ports']] == ['COM4', 'COM6']
    assert result['ports'][0]['transport'] == 'bluetooth'
    assert result['ports'][1]['transport'] == 'ambiguous'
    assert result['ports'][1]['systemDevice'] == r'\Device\BthModem0;\Device\USBSER000'
    assert result['openedPorts'] == result['writes'] == 0
    assert all(not p['plcVerified'] for p in result['ports'])


def _inventory():
    return dict(ok=True, ports=[dict(port='COM6', transport='bluetooth', plcVerified=False)],
        openedPorts=0, writes=0)


def test_find_ports_selects_only_one_identified_cpu(monkeypatch):
    monkeypatch.setattr('gxworks2_mcp.ports.list_ports', _inventory)
    calls = []
    def fake(host, port, timeout, transport):
        calls.append((host, port, transport))
        if port == 5556:
            return dict(reachable=True, cpu_type='FX3U', writes=0, command='0101')
        return dict(reachable=False, error='refused', writes=0)
    found = find_ports('192.0.2.10', probe=fake)
    assert calls == [('192.0.2.10', 5000, 'tcp'), ('192.0.2.10', 5555, 'tcp'), ('192.0.2.10', 5556, 'tcp')]
    assert found['selected'] == dict(host='192.0.2.10', port=5556, transport='tcp', cpu_type='FX3U')
    assert found['ambiguous'] is False
    assert found['openedSerialPorts'] == found['writes'] == 0
    assert found['serialPorts'][0]['plcVerified'] is False


def test_empty_or_multiple_cpu_replies_are_not_chosen(monkeypatch):
    monkeypatch.setattr('gxworks2_mcp.ports.list_ports', _inventory)
    def empty(*_):
        return dict(reachable=True, cpu_type='   ')
    none = find_ports('192.0.2.10', probe=empty)
    assert none['selected'] is None and none['matches'] == []
    def both(host, port, timeout, transport):
        if port in (5000, 5555):
            return dict(reachable=True, cpu_type='Q03UDV')
        return dict(reachable=False)
    many = find_ports('192.0.2.10', probe=both)
    assert many['selected'] is None and many['ambiguous'] is True
    assert [item['port'] for item in many['matches']] == [5000, 5555]


def test_bad_host_and_unknown_transport_do_not_open_sockets(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError('不得打开套接字')
    monkeypatch.setattr(socket, 'socket', boom)
    with pytest.raises(ValueError):
        find_ports('192.0.2.10 192.0.2.11', probe=lambda *_: pytest.fail('不得探测网段'))
    refused = probe('192.0.2.10', transport='serial')
    assert refused['reachable'] is False and refused['writes'] == 0
    with pytest.raises(MCProtocolError):
        MCClient(MCConfig(host='192.0.2.10', transport='serial')).connect()


def test_uppercase_tcp_is_not_sent_as_udp(monkeypatch):
    seen = []
    class Sock:
        def settimeout(self, _timeout): pass
        def connect(self, _address): raise OSError('refused')
        def close(self): pass
    def factory(_family, kind):
        seen.append(kind)
        return Sock()
    monkeypatch.setattr(socket, 'socket', factory)
    result = probe('192.0.2.10', 5555, 0.1, 'TCP')
    assert seen == [socket.SOCK_STREAM]
    assert result['reachable'] is False and result['transport'] == 'tcp'


def test_cpu_model_probe_does_not_write_or_remote_control(monkeypatch):
    class Sock:
        def settimeout(self, _timeout): pass
        def connect(self, _address): pass
        def close(self): pass
    monkeypatch.setattr(socket, 'socket', lambda *_: Sock())
    monkeypatch.setattr(MCClient, 'write_words', lambda *_: pytest.fail('不得写字'))
    monkeypatch.setattr(MCClient, 'write_bits', lambda *_: pytest.fail('不得写位'))
    monkeypatch.setattr(MCClient, 'remote', lambda *_: pytest.fail('不得远程控制'))
    monkeypatch.setattr(MCClient, 'cpu_type', lambda _self: 'FX3U')
    assert probe('192.0.2.10', 9, 0.1, 'tcp')['cpu_type'] == 'FX3U'


def test_plc_write_asks_before_connecting(monkeypatch):
    from gxworks2_mcp import server
    monkeypatch.setattr(server, '_client', lambda *_: pytest.fail('未确认不得连接'))
    asked = json.loads(server.plc_write('D0', [1], host='192.0.2.10', allow_write=True))
    assert asked['ok'] is False and asked['written'] == 0
    assert asked['question'] == '现在写入吗？'
    assert asked['device'] == 'D0' and asked['values'] == [1]
    still = json.loads(server.plc_write('D0', [1], host='192.0.2.10', confirm=True))
    assert still['ok'] is False and still['written'] == 0


def test_plc_write_connects_only_after_both_confirmations(monkeypatch):
    from gxworks2_mcp import server
    seen = []
    class Client:
        def __enter__(self):
            return self
        def __exit__(self, *_exc):
            return False
        def write_words(self, device, values):
            seen.append((device, list(values)))
            return len(values)
    monkeypatch.setattr(server, '_client', lambda *_args: Client())
    result = json.loads(server.plc_write('D10', [3, 4], host='192.0.2.10', confirm=True, allow_write=True))
    assert result['ok'] is True and result['written'] == 2
    assert seen == [('D10', [3, 4])]


def test_find_ports_tool_returns_failure_without_probing(monkeypatch):
    monkeypatch.setattr(socket, 'socket', lambda *_: pytest.fail('不得打开套接字'))
    from gxworks2_mcp import server
    payload = json.loads(server.plc_find_ports(' '))
    assert payload['ok'] is False and payload['writes'] == 0 and payload['openedSerialPorts'] == 0
