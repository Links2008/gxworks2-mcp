# GX Simulator2 接入说明

## 接入范围

本机 GX Works2 1.620 的 FX Simulator2。工具读取真实虚拟 CPU 运行数据，区别于项目中用于单元测试的简化扫描模型。

| 工具 | 功能 |
|---|---|
| gx_simulator_start | 启动当前可见工程的仿真，等待虚拟传输完成、监视视图和运行状态一致 |
| gx_simulator_status | 查询唯一虚拟 CPU、可执行文件身份、面板开关和实际 M8000 运行标志 |
| gx_simulator_control | 虚拟 CPU RUN/STOP；同时核对面板和实际运行标志 |
| gx_simulator_stop | 通过 GX Works2 正常结束仿真，不强杀进程 |
| gx_simulator_read | 批量读取 X、Y、M、S、T、TN、D、C、CN；可指定整数类型 |
| gx_simulator_inputs | 批量切换虚拟 X 输入0/1，保留其他位并读回 |
| gx_simulator_press | 按下20..2000ms后松开；异常时也尝试向原实例释放 |
| gx_simulator_verify | 一批输入变化、期望值、响应时间、持续保持及采样禁止组合；全部参数先验证，失败停止后续步骤 |

操作工具须传 `confirm=true`。每步 `inputs` 会保持到下一次修改；需要瞬时按钮用 `press`。
验证不会自动模拟机械限位，调用者须按工艺提供传感器变化。失败后保留虚拟现场，不冒称复位。

## 地址与版本边界

- X/Y：八进制，X0..X377、Y0..Y377；只有 X 可写。
- M：M0..M7679，以及 M8000..M8511；S：S0..S4095，只读。
- T0..T511 为定时器触点；TN0..TN511 为16位当前值。D0..D7999、C0..C255触点、CN0..CN255当前值只读。
- `types` 可指定 int16/uint16/int32/uint32；32位D读取连续两字，CN200及以上天然为32位。详见[通用编写与验收](通用编写与验收.md)。
- 不接受 IP、端口、COM 号、调用者指定 PID 或任意内存地址，不调用实体 PLC 通道。
- FX 引擎 SHA256 必须为 `4b401141a67a36c97b53bfd181b54255e0d320fa774cec7f151df55970658711`。
- 数据区每次从活进程读取，不使用失效缓存；打开进程后再次核对映像路径。短读、未知版本和多实例均失败。
- GUI 部分限定中文1.620。未知对话框不自动确认，传输报告未观察到完成时不判启动成功。
- 数据区属于版本相关的模拟器内部接口，不是三菱承诺跨版本兼容的公开 API；升级后须重新核验。

## 机械手按钮示例

仅适用于本任务已确认的 I/O，先打开并启动相应机械手工程：

```python
gx_simulator_inputs(values={"X0":0,"X1":0,"X2":0,"X3":0,"X4":0,"X5":1,
                            "X6":0,"X7":0,"X10":0,"X11":1,"X12":0}, confirm=True)
gx_simulator_control(action="run", confirm=True)
gx_simulator_press(device="X0", duration_ms=100, confirm=True)
gx_simulator_read(devices=["X0", "Y0", "S20", "M0"])
```

实测按键已释放 X0=0，Y0=1、S20=1、M0=1。批量验证示例：

```python
gx_simulator_verify(steps=[
    {"expect":{"Y0":1,"S20":1},"timeout_ms":2000},
    {"inputs":{"X1":1},"expect":{"Y0":0,"M0":0},"timeout_ms":2000}
], confirm=True)
```

## 实际验收

- 虚拟开关：通过 GX Works2 当前值更改界面设置 X5=1，独立读取 FX 数据区确认相同位为1。
- 真正运行：设置原点条件并按下/松开 X0，观察 Y0=1、S0退出、S20进入。
- 完整机械手自动循环：12个期望检查点全部通过，3.350秒，包含两个1秒夹钳定时过程及最终暂停。
- 生命周期：已实测结束、重新启动、读取虚拟下载完成报告，以及CPU RUN/STOP和M8000一致性。
- MCP：`tests/test_simulator_stdio.py` 通过新 stdio 客户端检索工具并执行真实调用；设置
  `GXWORKS2_SIMULATOR_LIVE_TEST=1` 才执行会改变仿真状态的机械手验收。
- 这些结果不代表实体设备、接线或机械安全验收。

## 实现依据

数据区布局参考开源项目 [NetToGXSim2 的 FX 内存桥](https://github.com/ismaillowkey/Mitsubishi-NetToGXSim2/blob/main/src/NetToGXSim2.Core/GxSim2MemoryBridge.cs)，
并在本机通过独立界面写入、活进程读回和实际程序反应交叉验证。来源说明见 `third-party/NetToGXSim2-NOTICE.txt`。
本实现不安装其网络桥、不启动网络监听，也不继承其任意进程回退或读取失败后填零的策略。
三菱另有 [MX Component 的官方模拟器连接方式](https://fa-faq.mitsubishielectric.co.jp/faq/show/13399)，本机未安装该组件，本实现不依赖它。
