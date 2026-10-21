# TIA Portal MCP 与原生工程接口的借鉴

核对日期：2026-10-20。

## 官方与社区的边界

[西门子官方 MCP](https://mcp.siemens.com/docs) 当前公开文档以产品、资料与技术资源检索为主。
[TIA Portal Openness](https://docs.tia.siemens.cloud/r/en-us/v20/tia-portal-openness-api-for-automation-of-engineering-workflows/tia-portal-openness-api/functions-for-projects-and-project-data/compiling-a-project)
是官方工程 API，可直接编译设备和程序块，返回编译结果对象。

本次查到的 [heilingbrunner/tiaportal-mcp](https://github.com/heilingbrunner/tiaportal-mcp)、
[Czarnak/tia-portal-mcp](https://github.com/Czarnak/tia-portal-mcp) 和
[eponce00/tiaopen-mcp](https://github.com/eponce00/tiaopen-mcp) 是社区维护项目。
不能仅因使用官方 Openness，就称其 MCP 服务器由西门子官方发布。

## 能力与速度比较

| 路线 | 可以做到什么 | 耗时证据 | 对本项目的启发 |
|---|---|---|---|
| Siemens 官方资料 MCP | 产品、文档、技术资源搜索 | 本次未取得可比实测延迟 | 为指令、CPU 和接口提供可追溯依据 |
| TIA Openness 社区 MCP | 枚举工程、读写块和变量、导入结构化内容、调用原生编译并返回诊断；各项目覆盖不同 | 本次查阅资料未给出同机同输入基准，不能声称毫秒级编译 | 以工程对象操作代替界面操作，原生编译结果是单独验收项 |
| GX Works3 MCP Bridge | ST 编辑、编译和错误读取，编辑依赖界面/剪贴板 | 无本机可比基准 | 借鉴读回和错误反馈，不沿用其界面写入路径 |
| 当前 GX Works2 MCP | FX3U 普通梯形图和 STL/RET 源码原生写入、完整读回和非目标流校验 | 本机写入中位 167/163 ms；不包含模型、编译或实机耗时 | 保留纯文件快路径，原生图形和编译另走版本绑定接口 |

## 采用的方法

1. 工程路径、CPU、程序对象和源字节绑定，拒绝对过期或不同工程直接套用编辑。
2. 一次请求处理相关图形、动作、转移与校验，减少模型与工具之间的往返。
3. 文件读取和写入无需厂商进程；原生图形转换/编译最多一个按需、限时、无窗口工作进程。
4. 图形绑定核验、源码读回、原生编译结果、顺控时序、实体设备证据分别报告。
5. 分别测量文件处理、工作进程冷启动、原生转换/编译、MCP 端到端延迟。

## 当前原生 SFC 开发发现

本机 Workspace 与 CodeGenerator 是 1.620.0.1，FX 转换器为 14.67。
参考 GX Works2 项目的原生适配器针对 1.635.0.1 / 15.31，不能直接复用其 RVA。
已从本机类型库读出 SFC 打开、图形解码、单元读取、步骤/转移写入、错误检查和编码的
接口签名。2026-10-21 已完成本机适配：单个按需进程完成图形、动作、条件、原生整工程
转换、严格检查、完整性信息更新和保存导出；顺启逆停零错误、零警告，源码与 MAIN.res
完整读回，导出工程可再次原生打开。MCP 端到端写入 6570.32ms，离线读取 11.21ms。
