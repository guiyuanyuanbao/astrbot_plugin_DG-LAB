# astrbot_plugin_DG-LAB

<p align="center">
  <img src="logo.png" alt="DG-LAB Logo">
</p>

## 重要警告（重载/重启前必读）

在执行以下操作前：
- 重载插件
- 停用插件
- 重启 AstrBot

请先确保所有客户端都已经执行 `/dglab stop` 退出郊狼模式。

否则可能出现：
- WS 服务未能及时释放
- `ws_port`（默认 5555）被占用
- 插件重载后无法重新绑定端口

如果已经发生端口占用，需要手动处理占用进程后再重载插件。

## 环境要求

- Python 3.10+
- AstrBot
- DG-Lab APP（3.0）

## 安装

1. 将目录放入 AstrBot 插件目录。
2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 在 AstrBot 中启用插件并配置参数。

## 配置项

文件：_conf_schema.json

- ws_host: WebSocket 监听地址，默认 `0.0.0.0`
- ws_port: WebSocket 监听端口，默认 `5555`
- ws_external_host: 二维码中的可访问地址（局域网 IP 或公网域名/IP）
- send_qr_raw_url: 发送二维码时是否附带原始绑定链接（默认开启）
- max_strength_a: A 通道最大强度（0-200）
- max_strength_b: B 通道最大强度（0-200）
- dglab_persona_id: 郊狼共享人格 ID
- dglab_persona_system_prompt: 郊狼人格系统提示词
- dglab_persona_begin_dialogs: 郊狼人格预设对话（列表，需 user/assistant 交替）
- dglab_persona_error_reply: 人格创建/切换失败时的自定义提示
- dglab_default_persona_id: 退出郊狼后默认恢复人格（当进入前原人格为空时生效）

> 注意：二维码里使用的是 `ws_external_host:ws_port`，请确保 APP 设备可以访问该地址。

## 指令

- /dglab help
  - 查看郊狼指令组帮助
- /dglab start（别名：开启）
  - 开启郊狼模式
  - 返回二维码用于 APP 绑定
  - 注册郊狼控制工具
- /dglab stop（别名：退出、关闭）
  - 关闭郊狼模式
  - 将 AB 通道强度归零
  - 取消工具注册
- /dglab channel A|B|AB
  - 设置可用通道
- /dglab part A:部位 B:部位
  - 设置通道对应部位描述
- /dglab fire [强度] 或 /dglab fire A:强度 B:强度
  - 设置一键开火临时增量（范围 1-30）
  - 仅影响会话内 `dglab_quick_fire` 工具
- /dglab status（别名：状态）
  - 查看会话状态和设备状态

## LLM 工具

仅在郊狼模式开启且设备绑定后可用：

- dglab_set_strength
- dglab_send_wave
- dglab_send_custom_wave
- dglab_quick_fire
- dglab_get_status
- dglab_clear_wave
- dglab_stop_output

### 一键开火说明

- 命令 `/dglab fire` 用于设置会话内一键开火增量：
  - `/dglab fire 10`：A/B 通道都设置为 10
  - `/dglab fire A:8 B:12`：分通道设置

## 协议说明

本插件仅实现 DG-Lab 的 APP 收信协议，不使用前端协议。

- 强度上报格式：`strength-a+b+aLimit+bLimit`
- 强度控制格式：`strength-channel+mode+value`
- 波形下发格式：`pulse-A:[...]` / `pulse-B:[...]`
- 清队列格式：`clear-1` / `clear-2`

## 常见问题

1. 扫码后无法绑定
- 检查 `ws_external_host` 是否可从手机访问
- 检查防火墙是否放行 `ws_port`（默认 5555）

2. 状态里强度上限为 0
- 先在 APP 侧手动调整一次强度或上限，触发上报
- 确认绑定成功消息是否出现

## 开发说明

- WebSocket 中继与控制器实现：dg_server.py
- 工具实现：dg_tools.py
- 波形预设：dg_waves.py
- 插件入口与指令：main.py

## 内置波形

本插件在 `dg_waves.py` 中提供若干内置波形（直接可通过工具或大模型调用）：

- `breathe`（呼吸）：模拟缓慢吸呼的起伏。
- `tide`（潮汐）：长周期涨落，类似潮汐上升/下降。
- `combo`（连击）：短促的连击脉冲。
- `fast_pinch`（快速按捏）：快速重复按捏感。
- `pinch_crescendo`（按捏渐强）：按捏力度逐步增强的过渡。
- `heartbeat`（心跳节奏）：模拟心跳的节奏脉冲。
- `compress`（压缩）：由强到弱或由弱到强的压缩式变化。
- `rhythm_step`（节奏步伐）：节奏化的步伐/鼓点型波形。

## WS 服务器机制

WS 服务采用“按需启动 + 空闲关闭”：
- 第一个会话执行 `/dglab start` 时启动 WS
- 后续会话复用同一个 WS 实例
- 最后一个会话执行 `/dglab stop` 后，WS 自动关闭

实现中包含启动锁，避免并发重复启动导致端口冲突。

## 郊狼人格生成与删除机制

郊狼人格采用“共享人格”模式：
- 有效配置 `dglab_persona_system_prompt` 时：
  - 首次启用郊狼模式会创建或更新共享人格
  - 后续会话复用该人格
- 所有会话退出郊狼模式后：
  - 删除共享郊狼人格，避免污染人格库

每个会话仍会记录进入郊狼前的人格，并在 `/dglab stop` 时恢复。

## 动态 Tools 机制（兼容性提示）

本插件使用动态注册与动态删除 Tools：
- 郊狼模式激活时注册 Tools
- 无活跃会话时卸载 Tools

其中，动态删除使用了对内部工具列表的直接移除方式（非开发文档提及的标准公开路径）。

这意味着：
- 当前版本可用
- 随 AstrBot 版本更新可能失效
- 若升级后出现工具残留/未卸载问题，请优先检查该机制兼容性

## 版本

当前版本：`v1.0.3`
