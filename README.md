# 勿扰zzz插件 💤

集成黑名单拦截与睡眠模式管理的多功能插件，由miko倾情打造～

## 🚫 黑名单功能

- 拦截指定用户的消息，阻止LLM回复
- 支持定时拉黑，到点自动放行
- 免疫名单：部分用户即使被拉黑也不拦截
- 动态管理：通过LLM工具或命令实时添加/移除黑名单

## 🌙 睡眠模式功能

- 进入勿扰状态后不回复任何人（白名单除外）
- 支持两种作用范围：全局所有对话 / 仅触发对话
- 睡眠时长可手动指定或使用配置区间
- 按对话独立缓存消息，唤醒后恢复为事件发送给LLM
- 唤醒时注入【刚睡醒】状态提示
- 支持白名单，白名单用户不受睡眠影响

## 唤醒方式

| 方式 | 说明 | 处理范围 |
|------|------|----------|
| 定时任务 | 到点自动唤醒 | 触发睡眠的会话 |
| wake指令 | 发送"wake"唤醒 | 当前会话 |
| 手动清状态 | 配置界面关闭睡眠 | 自动处理对应会话 |
| 后台守护 | 每3秒检测配置变更 | 被移除的会话 |
| scope变更 | 切换作用范围 | 清空所有缓存 |

## 定时任务

- 每个会话独立定时任务，互不覆盖
- 插件重载后自动恢复未完成的定时任务
- 后台守护每3秒检测配置变更，自动处理

## LLM工具

| 工具名 | 说明 | 权限 |
|--------|------|------|
| `block_user` | 拉黑用户，可设置时长(分钟) | 管理员 |
| `unblock_user` | 取消拉黑 | 管理员 |
| `go_to_sleep` | 进入勿扰模式，可选时长(分钟) | 管理员/配置开放 |

## 命令

| 命令 | 说明 |
|------|------|
| `拉黑 <用户ID> [分钟数]` | 拉黑用户 |
| `取消拉黑 <用户ID>` | 取消拉黑 |
| `黑名单` | 查看黑名单及剩余时间 |

## 配置项一览

**🛡️ 黑名单配置**
- `blacklist` - 黑名单用户ID列表
- `blacklist_enabled` - 启用黑名单
- `admin_only` - 拉黑工具限管理员
- `immune_list` - 免疫名单
- `min_block_minutes` - 最短拉黑时长
- `max_block_minutes` - 最长拉黑时长

**😴 睡眠模式配置**
- `sleep_mode_enabled` - 启用睡眠模式
- `sleep_scope` - 睡眠作用范围（全局/仅触发对话）
- `sleep_mode` - 全局睡眠状态（工具控制，勿手动）
- `sleep_mode_sessions` - 会话级睡眠状态（工具控制，勿手动）
- `sleep_until` - 自动唤醒队列（仅供查看）
- `sleep_whitelist` - 睡眠白名单
- `sleep_disabled_sessions` - 关闭睡眠的对话
- `sleep_max_cache` - 每会话缓存上限
- `sleep_max_total` - 总会话总缓存数
- `sleep_start_time` - 可睡眠时段开始
- `sleep_end_time` - 可睡眠时段结束
- `sleep_duration_min` - 最短睡眠时长
- `sleep_duration_max` - 最长睡眠时长
- `sleep_custom_users` - 可指定时长用户
- `sleep_public` - 睡眠工具开放所有人
- `wake_inject_pos` - 刚睡醒注入位置

## 📂 数据存储

- 黑名单过期时间：`data/plugin_data/astrbot_plugin_silencer/expiry.json` 📁
- 睡眠缓存：`data/plugin_data/astrbot_plugin_silencer/sleep_cache.json` 📁

---

*夜深了，该睡觉了喵～zzz* 💤
