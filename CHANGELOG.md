# Changelog

## v1.0.7 - 2026-03-23

### Added
- 新增 LLM 工具 `dglab_timed_switch_wave`：支持定时换波形，可在波形池中按 `sequence` 顺序循环或 `random` 随机切换。

### Changed
- 定时换波形工具在运行期间会持续循环发送，直到停止输出、清空队列或同通道新波形覆盖。

## v1.0.6 - 2026-03-23

### Added
- 新增 LLM 工具 `dglab_send_wave_combo`：支持按顺序配置预设波形与时长（如 A 15s -> B 20s -> C 10s）。

### Changed
- 波形组合按“组合为单位”持续循环发送，直到停止输出、清空队列或同通道新波形覆盖。

## v1.0.5 - 2026-03-23

### Changed
- `dglab_send_custom_wave` 移除 `duration_seconds` 参数，改为持续循环发送，直到停止输出、清空队列或同通道新波形覆盖。

## v1.0.4 - 2026-03-21

### Fixed
- 修复 `dglab_send_wave` 发送波形时可能误取消另一通道波形任务的问题。
- 波形后台任务改为按通道独立管理（A/B 分离），避免跨通道互相中断。

### Changed
- `dglab_send_wave` 移除 `duration_seconds` 参数，改为持续循环发送，直到停止输出、清空队列或同通道新波形覆盖。
- 会话停止与插件终止流程同步支持清理 A/B 独立波形任务。

## v1.0.3 - 2026-03-18

### Added
- 新增配置项 `send_qr_raw_url`（默认开启），用于控制发送二维码时是否附带原始绑定链接。

### Changed
- 优化 `/dglab start` 发送二维码时的提示文案，补充扫码失败排查与常用命令说明。

## v1.0.2 - 2026-03-15

### Added
- 新增 LLM 工具 `dglab_send_custom_wave`：支持大模型自行设计波形并发送。

### Changed
- 波形工具时长规则调整：`dglab_send_wave` 与 `dglab_send_custom_wave` 的 `duration_seconds` 默认 30 秒，最小 30 秒，最大 180 秒。

## v1.0.1 - 2026-03-14

### Added
- 新增会话内一键开火配置命令：`/dglab fire`。
- 新增 LLM 工具 `dglab_quick_fire`：在当前强度上临时叠加增量，持续结束后恢复原强度。

### Changed
- `dglab_send_wave` 持续时间上限调整为 120 秒。
- `dglab_quick_fire` 持续时间上限为 30 秒。



