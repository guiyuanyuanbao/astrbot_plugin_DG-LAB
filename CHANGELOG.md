# Changelog

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



