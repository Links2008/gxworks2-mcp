"""被动读取 Windows 串口登记，并只读查找少量已记录的 MC 端口。

串口路径不打开句柄、不发送字节。以太网路径只调用调用者给出的探测函数；
默认探测是 CPU 型号读取，不写软元件，也不远程启停。
"""

import re
import sys
import time


def _com_name(port):
    if not isinstance(port, str):
        return None
    name = port.strip().upper()
    if not re.fullmatch(r"COM[1-9][0-9]*", name):
        return None
    return name


def _serial_transport(device):
    if not isinstance(device, str):
        return "unidentified-serial"
    lowered = device.lower()
    if "bthmodem" in lowered or "bthenum" in lowered:
        return "bluetooth"
    return "unidentified-serial"


def list_ports() -> dict:
    if sys.platform != "win32":
        raise OSError("当前端口枚举实现仅支持 Windows")
    import winreg
    started = time.perf_counter()
    found = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
    except FileNotFoundError:
        key = None
    if key is not None:
        with key:
            for index in range(winreg.QueryInfoKey(key)[1]):
                device, port, kind = winreg.EnumValue(key, index)
                name = _com_name(port)
                if kind != winreg.REG_SZ or name is None:
                    continue
                transport = _serial_transport(device)
                device_name = device if isinstance(device, str) else ""
                current = found.get(name)
                if current is None:
                    found[name] = dict(port=name, systemDevice=device_name, transport=transport,
                        plcVerified=False)
                    continue
                if device_name and device_name not in current["systemDevice"].split(";"):
                    current["systemDevice"] = (current["systemDevice"] + ";" + device_name).strip(";")
                if current["transport"] != transport:
                    current["transport"] = "ambiguous"
    ports = [found[name] for name in sorted(found, key=lambda item: int(item[3:]))]
    return dict(ok=True, ports=ports, openedPorts=0, writes=0,
                source="Windows SERIALCOMM registry; not a PLC identity probe",
                elapsedMs=round((time.perf_counter() - started) * 1000, 2),
                note="端口存在不代表已连接 PLC；原生 USB 设备也可能不显示为 COM 口。同一 COM 出现多种传输时标为 ambiguous，仍不代选。")


def find_ports(host: str = "127.0.0.1", timeout: float = 1.0, *, probe=None) -> dict:
    """登记串口，并对三个文档候选做只读 CPU 型号探测。

    恰好一个候选返回非空型号时才填写 selected。没有响应或多个响应都不猜测。
    不扫描网段，不打开 COM，不写 PLC。
    """
    if not isinstance(host, str) or not host.strip() or any(char.isspace() for char in host):
        raise ValueError("host 必须是单个主机名或地址")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.05 <= float(timeout) <= 2:
        raise ValueError("timeout 必须是 0.05..2 秒")
    serial = list_ports()
    if probe is None:
        from .mcprotocol import probe as probe
    from .mcprotocol import MC_PORT_CANDIDATES
    target = host.strip()
    attempts = []
    matches = []
    for port, transport, why in MC_PORT_CANDIDATES:
        try:
            result = probe(target, port, float(timeout), transport)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            result = {"reachable": False, "error": str(exc)}
        if not isinstance(result, dict):
            result = {"reachable": False, "error": "探测没有返回结果对象"}
        cpu = result.get("cpu_type")
        identified = bool(result.get("reachable")) and isinstance(cpu, str) and bool(cpu.strip())
        attempt = dict(host=target, port=port, transport=transport, reason=why,
            reachable=bool(result.get("reachable")), identified=identified,
            cpu_type=cpu.strip() if isinstance(cpu, str) else None,
            error=result.get("error"), writes=0, command="0101")
        attempts.append(attempt)
        if identified:
            matches.append(attempt)
    selected = matches[0] if len(matches) == 1 else None
    return dict(ok=True,
        selected=None if selected is None else dict(
            host=selected["host"], port=selected["port"],
            transport=selected["transport"], cpu_type=selected["cpu_type"]),
        ambiguous=len(matches) > 1,
        matches=[dict(host=item["host"], port=item["port"], transport=item["transport"],
            cpu_type=item["cpu_type"]) for item in matches],
        attempts=attempts, serialPorts=serial["ports"], openedSerialPorts=0, writes=0,
        physicalPlcWrite=False,
        note="串口只读登记，没有打开。以太网只对 TCP 5000、5555、5556 读取 CPU 型号。"
             "恰好一个返回型号才写入 selected。5000 不是协议规定的端口。GX Simulator2 不监听这些端口。")
