# 第三方资料归属

`src/gxworks2_mcp/data/fx3u_step_widths.json` 来自
[Arienax/gxworks-agent](https://github.com/Arienax/gxworks-agent)，固定提交
`4c456be917e67fe4091811c575690a7c17fdd28b`，原路径
`resources/instructions/mitsubishi/fx3u_step_widths.json`。

该提交的 `LICENSE` 与 `LICENSE-SCOPE.md` 明确采用 Apache-2.0，许可证全文随本项目
保留在 `gxworks-agent-APACHE-2.0.txt`。本项目复用经原生编码观察建立的数据表，
并参考 `native_adapters/WorkspaceSourceSave.cs`、`WorkspaceValidation.cs` 的公开接口
初始化、编译检查和保存顺序，以及 `src/gxw/sfc_pou.py` 的有界帧格式。
对应适配位于本项目 `data/WorkspaceSfc1620.cs`、`sfc_binary.py`；已按本机 1.620
类型库签名及真实运行重新核验。没有采用 1.635 的 RVA 地址，没有引入其工作台、服务、
AI 接口或大型运行时。数据表内的证据和支持范围保持原样。

GX Works3 MCP 仅用于学习分析工具的设计，未复制其受限制实现。

`data/fx3u-ladder.gxw` 由本机 GX Works2 创建，为仅含 END 的 FX3U 简单梯形图空白工程，
不是参考仓库或真实生产工程的拷贝。模板不包含用户的电镀生产线逻辑。
