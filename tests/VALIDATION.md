# 0.1.0 验证记录

2026-10-14，当前 MSI MS-1T91。

- 21 项 unittest 全部通过（模拟硬件，无真实 sysfs 写入）。
- TypeScript --noEmit 和 Rollup 构建通过。
- Python compileall 通过。
- systemd-analyze verify 在主机只读检查通过。
- 真实硬件只读探测发现 10 个 policy；构建前后探测配置完全一致。
- 发布 manifest、ZIP 完整性、tar 路径及无符号链接检查通过。
- 未安装、启用或启动服务，未做 Steam/Decky 渲染和手柄实机验证，未运行游戏或压力测试。
