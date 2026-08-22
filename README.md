# 勿扰zzz 💤

黑名单拦截 + 睡眠模式 + 高峰期静默，miko和哥哥摸出来的小玩意～

---

## 干啥用的

三个事，嘛都很实用：

1. **拉黑**：不爽就拉黑，支持定时自动放行
2. **睡觉**：bot去睡觉觉，消息帮你存着，醒了再回
3. **高峰期静默**：DeepSeek峰谷定价的工作日时段自动闭嘴，省小钱钱（周末没峰谷，不静默）

---

## 怎么工作的

### 黑名单

- 挂在 `@filter.on_llm_request()` 上，LLM处理前就拦了
- 查 sender_id 在不在黑名单里，在就掐掉
- 定时拉黑用 `expiry.json` 记过期时间，每次来消息顺便清理过期的

### 睡眠

- 调 `go_to_sleep` 后就睡了，开个 asyncio 定时任务等着醒来
- 睡了之后非白名单的消息全拦，塞进 `sleep_cache.json`
- 缓存按会话独立存，每个会话有上限
- 醒来有四种方式：
  - 定时到了自己醒
  - 有人发 "醒醒"
  - 手动改配置关掉睡眠（后台每10秒扫一次变更）
  - 改 sleep_scope 会清空所有睡眠状态
- 醒了就读缓存，拼成 AstrBotMessage，走 `commit_event` 丢进事件队列，完整跑一遍LLM处理链
- 群聊缓存按 session+sender 分组回放，私聊按 session 合并
- 醒来时给LLM注入【刚睡醒】标记，让它知道刚才在摸鱼

---

## 唤醒方式一览

| 方式 | 说明 | 范围 | 权限 |
|------|------|------|------|
| 定时到期 | asyncio定时器到点 | 触发睡眠的会话 | - |
| wake指令 | 发 "醒醒" | 当前会话 | 管理员 或 wake_whitelist |
| 配置变更 | 后台每10秒对比快照，sleep_mode关了就唤醒 | 对应会话 | - |
| scope变更 | 切换作用范围清空全部 | 所有 | - |

---

## LLM工具

| 工具 | 干嘛的 | 权限 |
|------|------|------|
| `block_user` | 拉黑 | llm_auto_block 开了随便用，否则走 admin_only。受 `blacklist_enabled` 控制 |
| `unblock_user` | 放出来 | 同上 |
| `go_to_sleep` | 去睡觉 | sleep_public 开了随便用，否则走 admin_only。受 `sleep_mode_enabled` 控制 |

---

## 命令

| 命令 | 干嘛的 | 权限 |
|------|------|------|
| `拉黑 <ID/@> [分钟]` | 拉黑 | 管理员 或 cmd_whitelist。受 `blacklist_enabled` 控制 |
| `取消拉黑 <ID/@>` | 取消 | 同上 |
| `黑名单` | 看看拉了谁 | 所有人 |
| `wake` | 叫醒 | 管理员 或 wake_whitelist |

---

## 配置项

### 黑名单

- `blacklist` — 黑名单用户ID列表，被拉黑的都在这里
- `blacklist_enabled` — 总开关，关了 block_user/unblock_user 工具停用(LLM看不到)，拉黑命令也拒绝，需重载插件
- `admin_only` — 开启后只有管理员和 `cmd_whitelist` 里的用户能操作拉黑/取消拉黑
- `cmd_whitelist` — 命令白名单，即使不是管理员也能用 `拉黑` / `取消拉黑` 命令
- `immune_list` — 免疫名单，这里面的用户既不能被拉黑，也不会被黑名单拦截
- `llm_auto_block` — 开了之后LLM自己就能调 `block_user` / `unblock_user` 工具，不用走管理员权限。不过 `unblock_user` 时只能解封自己
- `min_block_minutes` — 最短拉黑时长（分钟）。不指定时长时会从 `min` ~ `max` 之间随机
- `max_block_minutes` — 最长拉黑时长（分钟）。设为 0 表示不设上限

### 睡眠

- `sleep_mode_enabled` — 总开关，关了 go_to_sleep 工具停用(LLM看不到)，睡眠拦截也失效，需重载插件
- `sleep_scope` — `全局`：一睡所有对话都停；`仅触发的对话`：只睡触发睡眠的那个会话，其他会话正常回复
- `sleep_mode` — 全局睡眠状态，`true` / `false`。手动从 true 改成 false 会触发唤醒（后台10秒内检测到）
- `sleep_mode_sessions` — 会话模式下正在睡眠的会话列表，手动清掉某个会话也会触发唤醒
- `sleep_until` — 唤醒时间队列，每项格式 `会话:Unix时间戳`。到时间自动醒，手动删掉某项也会触发唤醒
- `sleep_whitelist` — 睡眠白名单，这里面的用户睡着时也不拦截，正常回复
- `sleep_disabled_sessions` — 这些会话永远不进入睡眠，即使全局睡了也照常回复
- `sleep_max_cache` — 每会话最多缓存几条消息，超出就丢最旧的（默认 3）
- `sleep_max_total` — 最多缓存几个不同会话，超出就丢弃最早进入的那个会话的全部缓存（默认 5）
- `sleep_start_time` — 允许睡觉的时间段起点，格式 `HH:MM`，比如 `"23:00"`。不设则不限制
- `sleep_end_time` — 允许睡觉的时间段终点，支持跨天，比如 `"08:00"`。不在时段内调用 `go_to_sleep` 会返回 "睡不着"
- `sleep_duration_min` — 随机睡眠的最短时长（小时），不传时长时从 `min` ~ `max` 之间随机（默认 2）
- `sleep_duration_max` — 随机睡眠的最长时长（小时），默认 3
- `sleep_custom_users` — 这些用户可以自己指定睡眠时长，传多少睡多少。管理员不受此限制
- `sleep_public` — 开了之后任何人都能让 bot 去睡觉，否则走 `admin_only`
- `wake_whitelist` — 非管理员也能发 `wake` 叫醒的用户列表
- `wake_inject_pos` — 醒后注入【刚睡醒】提示的位置：`"system"` 塞进系统提示词，`"prompt"` 塞进用户消息前面

### 高峰期静默

- `peak_hours_enabled` — 开启后自动在 DeepSeek 峰谷时段闭嘴省 token
  - **仅周一至周五**生效，周末（周六/周日）DeepSeek 无峰谷定价，不触发静默
  - 工作日每天 **9:00** 自动睡 180 分钟到 **12:00**
  - 工作日每天 **14:00** 自动睡 240 分钟到 **18:00**
  - 强制全局睡眠，无视 `sleep_scope` 设置；高峰期结束后自动恢复原来的 scope
  - 插件启动时如果刚好在工作日高峰期内，会立刻进入睡眠并补足剩余时长

---

## 数据文件

都扔在 `data/plugin_data/astrbot_plugin_silencer/` 下：

| 文件 | 存啥 |
|------|------|
| `expiry.json` | 黑名单过期时间 |
| `sleep_cache.json` | 睡后缓存的消息 |
| `snapshot.json` | 配置快照，后台检测变更用 |

---

## 缓存回放策略

醒来后缓存的消息这样拼：

- **私聊**：同一会话全合一条
- **群聊**：同一会话 + 同一 sender 合一条
- 群聊事件自动补 group 信息和 `is_at_or_wake_command=True`

---

*哦呀斯密～ 困死了……zzz* 💤
