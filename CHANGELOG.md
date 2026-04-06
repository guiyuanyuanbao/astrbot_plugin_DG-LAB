# Changelog

## v2.0.0 - 2026-04-05

### Added
- 新增内置额度系统，包含独立 `billing.db` 数据库、免费额度与付费额度（发电额度）管理。
- 新增爱发电订单兑换能力，支持通过 `/dglab redeem <order_id>` 将订单金额兑换为付费 TOKEN。
- 新增管理员额度管理命令：`/dglab quota-list`、`/dglab redeem-list`、`/dglab recharge`。
- 新增管理员免费额度刷新命令：`/dglab refresh-free user_id=123` 与 `/dglab refresh-free all=true`。
- 新增 `afdian` 与 `billing` 配置对象，用于管理爱发电接口、刷新周期、兑换比例和模型倍率。

### Changed
- 插件版本提升至 `v2.0.0`。
- 新增 `billing.enabled` 总开关，默认关闭；关闭时仅停用自动余额拦截与自动扣费链路。
- 自动计费逻辑支持免费额度惰性刷新、免费优先扣费、群聊免计费和仅郊狼模式计费。
- `help` 指令输出按普通用户命令与管理员命令分组整理，并同步补齐当前可用命令说明。

## v1.1.0 - 2026-03-29

### Added
- 新增指令 `/dglab wavelist`：查看当前可用波形列表（内置 + 用户上传）。
- 新增指令 `/dglab waveinfo <波形名>`：查看指定波形的帧数、总时长、首帧与末帧。
- 新增配置项 `uploaded_wave_files`（仅 `.pulse`）：支持在配置界面上传并加载 DG-Lab App 导出的波形文件。
- 上传文件读取路径统一为 AstrBot 默认目录：`plugin_data/astrbot_plugin_DG_LAB/files/uploaded_wave_files/`。

## v1.0.9 - 2026-03-23

### Changed
- 优化断联逻辑：当 DG-Lab APP 主动断开时，插件会自动执行与 `/dglab stop` 一致的完整退出流程（停止会话、断开设备、并发送退出提示）。
- 优化插件销毁断联流程：插件 `terminate` 时，若会话仍绑定设备，将主动通知并执行标准 WS 断连，避免残留连接状态。

## v1.0.8 - 2026-03-23

### Changed
- 断开控制器连接时，改为通过 WS 服务端标准断连流程处理。

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



