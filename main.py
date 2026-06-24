"""
勿扰zzz - 拉黑拦截+睡眠模式管理
"""

import asyncio
import json
import time as time_module
from pathlib import Path

from astrbot.api import star, logger, AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest

EXPIRY_PATH = Path("data/plugin_data/astrbot_plugin_silencer/expiry.json")
CACHE_PATH = Path("data/plugin_data/astrbot_plugin_silencer/sleep_cache.json")


def _load_expiry() -> dict:
    if EXPIRY_PATH.exists():
        try:
            with open(EXPIRY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_expiry(expiry: dict):
    EXPIRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EXPIRY_PATH, "w", encoding="utf-8") as f:
        json.dump(expiry, f, ensure_ascii=False, indent=2)


def _load_cache() -> list:
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save_cache(cache: list):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)



@star.register("astrbot_plugin_silencer", "miko", "勿扰zzz - 拉黑拦截+睡眠模式管理", "1.7.0")
class SilencerPlugin(star.Star):
    def __init__(self, context: star.Context, config: AstrBotConfig = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.enabled: bool = self.config.get("blacklist_enabled", True)
        self.admin_only: bool = self.config.get("admin_only", True)
        self.min_minutes: float = float(self.config.get("min_block_minutes", 0))
        self.max_minutes: float = float(self.config.get("max_block_minutes", 43200))
        self._expiry: dict = _load_expiry()
        self._clean_expired()
        self._clean_internal_config()
        # 兼容旧版sleep_until
        su = self.config.get("sleep_until", [])
        if not isinstance(su, list):
            self.config["sleep_until"] = [su] if su else []
        self._wake_tasks = []
        self._restore_timers()
        # 后台检测配置变更
        async def _config_watcher():
            while True:
                await asyncio.sleep(3)
                try:
                    old_mode = self.config.get("_old_sleep_mode")
                    old_ss = self.config.get("_old_sleep_sessions")
                    cur_mode = self.config.get("sleep_mode", False)
                    cur_ss = self.config.get("sleep_mode_sessions", [])
                    if old_mode is not None and old_ss is not None:
                        if old_mode != cur_mode or old_ss != cur_ss:
                            removed = []
                            if old_mode != cur_mode:
                                removed.append("global")
                            if old_ss != cur_ss:
                                removed = [s for s in old_ss if s not in cur_ss]
                            cache = _load_cache()
                            for s in removed:
                                await self._execute_wake(s)
                            if not removed:
                                self._set_until_list([])
                                self._cancel_all_tasks()
                    self.config["_old_sleep_mode"] = cur_mode
                    self.config["_old_sleep_sessions"] = cur_ss
                    try:
                        self.config.save_config()
                    except:
                        pass
                except:
                    pass
        asyncio.create_task(_config_watcher())
        logger.info(f"勿扰zzz插件加载完成，当前黑名单: {self._get_list()}")

    # ── 唤醒时间列表操作 ──

    def _get_until_list(self) -> list:
        val = self.config.get("sleep_until", [])
        return val if isinstance(val, list) else []

    def _set_until_list(self, lst: list):
        self.config["sleep_until"] = lst
        try:
            self.config.save_config()
        except:
            pass

    def _add_until(self, session: str, ts: float):
        lst = self._get_until_list()
        lst.append(f"{session}:{ts}")
        self._set_until_list(lst)

    def _remove_until(self, session: str):
        lst = self._get_until_list()
        lst = [e for e in lst if not e.startswith(session + ":")]
        self._set_until_list(lst)

    # ── 睡眠模式状态操作 ──

    def _is_sleeping(self, session: str = "") -> bool:
        scope = str(self.config.get("sleep_scope", "全局"))
        # scope变更时清空所有缓存和定时任务
        saved = self.config.get("sleep_saved_scope", "")
        if saved and saved != scope:
            _save_cache([])
            self._set_until_list([])
            self._sleep_clear()
            self._cancel_all_tasks()
            self.config["sleep_saved_scope"] = scope
            try:
                self.config.save_config()
            except:
                pass
            logger.info("scope变更，已清空缓存")
        if scope in ("session", "仅触发的对话"):
            lst = self.config.get("sleep_mode_sessions", [])
            if not session:
                return bool(lst)
            return session in lst
        return bool(self.config.get("sleep_mode", False))

    def _sleep_enter(self, session: str):
        scope = str(self.config.get("sleep_scope", "global"))
        if scope in ("session", "仅触发的对话"):
            lst = self.config.get("sleep_mode_sessions", [])
            if not isinstance(lst, list):
                lst = []
            if session not in lst:
                lst.append(session)
            self.config["sleep_mode_sessions"] = lst
        else:
            self.config["sleep_mode"] = True

    def _sleep_leave(self, session: str):
        """离开睡眠状态，异步处理缓存"""
        scope = str(self.config.get("sleep_scope", "全局"))
        if scope in ("session", "仅触发的对话"):
            lst = self.config.get("sleep_mode_sessions", [])
            if isinstance(lst, list):
                self.config["sleep_mode_sessions"] = [s for s in lst if s != session]
        else:
            self.config["sleep_mode"] = False
        # 后台处理缓存
        cache = _load_cache()
        my_msgs = [c for c in cache if c.get("session") == session]
        if my_msgs:
            async def _process():
                await self._execute_wake(session)
            asyncio.create_task(_process())
        else:
            self._remove_until(session)
            self._remove_until("global")

    def _sleep_clear(self):
        self.config["sleep_mode"] = []

    # ── 定时任务恢复 ──

    def _restore_timers(self):
        if not self._is_sleeping():
            return
        now = time_module.time()
        for entry in self._get_until_list():
            try:
                session, ts_str = entry.rsplit(":", 1)
                ts = float(ts_str)
                left = ts - now
                if left > 0:
                    async def _recover(umo=session, delay=left):
                        try:
                            await asyncio.sleep(delay)
                            await self._execute_wake(umo)
                        except asyncio.CancelledError:
                            pass
                        except Exception as e:
                            logger.warning(f"恢复唤醒异常: {e}")
                    asyncio.create_task(_recover())
                    logger.info(f"恢复定时任务: {session} 剩余{left:.0f}秒")
                else:
                    self._remove_until(session)
                    self._sleep_leave(session)
            except:
                continue

    def _cancel_all_tasks(self):
        if hasattr(self, '_wake_tasks'):
            for t in self._wake_tasks:
                try:
                    if not t.done():
                        t.cancel()
                except:
                    pass
            self._wake_tasks = []

    def _clean_internal_config(self):
        for k in list(self.config.keys()):
            if k.startswith("pre-config:") or k in ("sleep_umo", "sleep_sender_id", "just_woke_up", "just_wake_up"):
                del self.config[k]
        try:
            self.config.save_config()
        except:
            pass

    def _get_list(self) -> list:
        return self.config.get("blacklist", [])

    def _set_list(self, blacklist: list):
        self.config["blacklist"] = blacklist
        try:
            self.config.save_config()
        except Exception as e:
            logger.warning(f"保存配置失败: {e}")

    def _clean_expired(self):
        blacklist = self._get_list()
        now = time_module.time()
        changed = False
        new_blacklist = []
        new_expiry = {}
        for uid in blacklist:
            exp = self._expiry.get(uid)
            if exp is not None and now >= exp:
                changed = True
                continue
            new_blacklist.append(uid)
            if exp is not None:
                new_expiry[uid] = exp
        for uid in list(self._expiry.keys()):
            if uid not in new_blacklist:
                changed = True
        if changed:
            self._set_list(new_blacklist)
            self._expiry = new_expiry
            _save_expiry(self._expiry)

    async def _execute_wake(self, umo: str):
        """执行唤醒"""
        self._cancel_all_tasks()
        # 尝试删除对应条目（全局模式可能存的是global）
        self._remove_until(umo)
        self._remove_until("global")
        self._sleep_leave(umo)
        cache = _load_cache()
        my_msgs = [c for c in cache if c.get("session") == umo] if umo else []
        remain = [c for c in cache if c.get("session") != umo] if umo else []
        _save_cache(remain)
        try:
            self.config.save_config()
        except:
            pass
        if my_msgs and umo:
            text = "睡后消息：" + chr(10)
            for c in my_msgs:
                text += f"来自 {c.get('sender_id','?')}: {c.get('message')}" + chr(10)
            from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
            from astrbot.core.platform.message_type import MessageType
            from astrbot.api.message_components import Plain
            msg_obj = AstrBotMessage()
            msg_obj.type = MessageType.FRIEND_MESSAGE
            msg_obj.self_id = my_msgs[0].get("self_id", "0") if my_msgs else "0"
            msg_obj.session_id = umo.split(":")[-1]
            msg_obj.message_id = str(time_module.time())
            msg_obj.sender = MessageMember(user_id=my_msgs[0].get("sender_id", "0") if my_msgs else "0", nickname=my_msgs[0].get("sender_name", "系统") if my_msgs else "系统")
            msg_obj.message = [Plain(text)]
            msg_obj.message_str = text
            msg_obj.raw_message = {"text": text}
            for p in self.context.platform_manager.platform_insts:
                if p.meta().id == umo.split(":")[0]:
                    p.commit_event(p.create_event(msg_obj))
                    break
        logger.info("唤醒执行完毕")

    @filter.on_llm_request()
    async def check_blacklist(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.enabled:
            return
        sender_id = event.get_sender_id()
        if not sender_id:
            return
        session = getattr(event, 'unified_msg_origin', '')
        # 睡眠已关时清理残留队列
        if not self._is_sleeping() and self._get_until_list():
            self._set_until_list([])
            self._cancel_all_tasks()
            try:
                self.config.save_config()
            except:
                pass
        # 刚睡醒注入
        if self.config.get("just_woke_up", False):
            self.config["just_woke_up"] = False
            try:
                self.config.save_config()
            except:
                pass
            tag = "提示:你刚睡醒，以下是你睡着时收到的信息"
            pos = str(self.config.get("wake_inject_pos", "prompt")).lower()
            nl = chr(10)
            if pos == "system":
                req.system_prompt = (req.system_prompt or "") + nl + nl + tag + nl
            else:
                if req.prompt:
                    req.prompt = tag + nl + nl + req.prompt
                else:
                    req.system_prompt = (req.system_prompt or "") + nl + nl + tag + nl
        # 睡眠模式检查
        if self.config.get("sleep_mode_enabled", True) and self._is_sleeping(session):
            if session in self.config.get("sleep_disabled_sessions", []):
                pass
            else:
                now = time_module.time()
                for entry in self._get_until_list():
                    try:
                        es, ets = entry.rsplit(":", 1)
                        if (es == "global" or es == session) and now >= float(ets):
                            self._remove_until(es)
                            self.config["just_woke_up"] = True
                            await self._execute_wake(session)
                            return
                    except:
                        continue
                if event.message_str.strip().lower() == "wake":
                    self.config["just_woke_up"] = True
                    await self._execute_wake(session)
                    try:
                        self.config.save_config()
                    except:
                        pass
                    return
                sleep_whitelist = self.config.get("sleep_whitelist", [])
                if sender_id not in sleep_whitelist:
                    max_cache = int(self.config.get("sleep_max_cache", 3))
                    max_total = int(self.config.get("sleep_max_total", 5))
                    if max_cache > 0:
                        cache = _load_cache()
                        sess = getattr(event, 'unified_msg_origin', None) or getattr(event, 'session_id', '')
                        if not sess:
                            sess = str(id(event))
                        session_msgs = [c for c in cache if c.get("session") == sess]
                        if len(session_msgs) < max_cache:
                            sessions_set = set(c.get("session") for c in cache)
                            if sess not in sessions_set and max_total > 0 and len(sessions_set) >= max_total:
                                sessions_order = []
                                seen = set()
                                for c in cache:
                                    if c.get("session") not in seen:
                                        sessions_order.append(c.get("session"))
                                        seen.add(c.get("session"))
                                if sessions_order:
                                    drop = sessions_order[0]
                                    cache = [c for c in cache if c.get("session") != drop]
                            cache.append({
                                "session": sess,
                                "sender_id": sender_id,
                                "sender_name": event.get_sender_name(),
                                "self_id": event.get_self_id(),
                                "message": event.message_str,
                                "time": time_module.time()
                            })
                            _save_cache(cache)
                    logger.info(f"睡眠模式拦截: {sender_id}")
                    event.call_llm = False
                    event.stop_event()
                    return
        immune = self.config.get("immune_list", [])
        if sender_id in immune:
            return
        self._clean_expired()
        if sender_id in self._get_list():
            logger.info(f"黑名单拦截: {sender_id} 的消息已被阻止")
            event.call_llm = False
            event.stop_event()

    @filter.llm_tool(name="block_user")
    async def block_user(self, event: AstrMessageEvent, user_id: str, duration_minutes: float = 0):
        role = getattr(event, 'role', None)
        if self.admin_only and role is not None and role != "admin":
            return "权限不足，只有管理员才能拉黑用户"
        blacklist = self._get_list()
        if user_id in blacklist:
            exp = self._expiry.get(user_id)
            if exp:
                left = int((exp - time_module.time()) / 60)
                return f"{user_id} 已在黑名单中，剩余约{left}分钟"
            return f"{user_id} 已经在黑名单里了(永久)"
        if duration_minutes > 0:
            if self.min_minutes > 0 and duration_minutes < self.min_minutes:
                duration_minutes = self.min_minutes
            if self.max_minutes > 0 and duration_minutes > self.max_minutes:
                duration_minutes = self.max_minutes
        blacklist.append(user_id)
        if duration_minutes > 0:
            self._expiry[user_id] = time_module.time() + duration_minutes * 60
            self._set_list(blacklist)
            _save_expiry(self._expiry)
            logger.info(f"已拉黑用户: {user_id}，时长{duration_minutes}分钟")
            return f"已拉黑 {user_id}，{duration_minutes}分钟后自动放行"
        else:
            self._expiry.pop(user_id, None)
            self._set_list(blacklist)
            _save_expiry(self._expiry)
            logger.info(f"已永久拉黑用户: {user_id}")
            return f"已永久拉黑 {user_id}"

    @filter.llm_tool(name="unblock_user")
    async def unblock_user(self, event: AstrMessageEvent, user_id: str):
        role = getattr(event, 'role', None)
        if self.admin_only and role is not None and role != "admin":
            return "权限不足，只有管理员才能取消拉黑"
        blacklist = self._get_list()
        if user_id not in blacklist:
            return f"{user_id} 不在黑名单里"
        blacklist.remove(user_id)
        self._expiry.pop(user_id, None)
        self._set_list(blacklist)
        _save_expiry(self._expiry)
        logger.info(f"已取消拉黑用户: {user_id}")
        return f"已取消拉黑 {user_id}"

    @filter.llm_tool(name="go_to_sleep")
    async def go_to_sleep(self, event: AstrMessageEvent, duration_minutes: float = 0):
        '''
        进入勿扰模式(睡觉)，期间不回复任何消息(白名单除外)。当用户让你去睡觉或休息时，应直接调用此工具，不要只口头答应。

        Args:
            duration_minutes(number): 睡眠时长(分钟)，0表示使用配置中的区间值。可选参数，默认0
        '''
        role = getattr(event, 'role', None)
        if not self.config.get("sleep_public", False) and self.admin_only and role is not None and role != "admin":
            return "权限不足"
        start_t = self.config.get("sleep_start_time", "")
        end_t = self.config.get("sleep_end_time", "")
        if start_t and end_t:
            now_h = time_module.localtime().tm_hour
            now_m = time_module.localtime().tm_min
            now_val = now_h * 60 + now_m
            try:
                sh, sm = map(int, start_t.split(":"))
                eh, em = map(int, end_t.split(":"))
                start_val = sh * 60 + sm
                end_val = eh * 60 + em
                in_time = False
                if end_val < start_val:
                    in_time = now_val >= start_val or now_val < end_val
                else:
                    in_time = start_val <= now_val < end_val
                if not in_time:
                    return "现在不是睡觉时间，睡不着"
            except:
                pass
        if duration_minutes > 0:
            custom_users = self.config.get("sleep_custom_users", [])
            cid = ""
            try:
                cid = event.context.event.get_sender_id()
            except:
                try:
                    cid = event.get_sender_id()
                except:
                    pass
            role = getattr(event, 'role', None)
            if cid in custom_users or role == "admin":
                dur_h = duration_minutes / 60
            else:
                duration_minutes = 0
        if duration_minutes <= 0:
            dur_min = float(self.config.get("sleep_duration_min", 6))
            dur_max = float(self.config.get("sleep_duration_max", 10))
            if dur_max < dur_min:
                dur_max = dur_min
            dur_h = dur_min + (dur_max - dur_min) * (time_module.time() % 100 / 100)
        session = ""
        try:
            session = event.context.event.unified_msg_origin
        except:
            try:
                session = event.unified_msg_origin
            except:
                session = ""
        self._sleep_enter(session)
        self.config["sleep_umo"] = session
        scope = str(self.config.get("sleep_scope", "global"))
        self.config["sleep_saved_scope"] = scope
        if scope in ("session", "仅触发的对话"):
            self._add_until(session, time_module.time() + dur_h * 3600)
        else:
            self._add_until("global", time_module.time() + dur_h * 3600)
        try:
            self.config.save_config()
        except Exception as e:
            logger.warning(f"保存配置失败: {e}")
        async def _auto_wake():
            try:
                await asyncio.sleep(dur_h * 3600)
                await self._execute_wake(session)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"自动唤醒异常: {e}")
        task = asyncio.create_task(_auto_wake())
        # 清理已完成的旧任务
        self._wake_tasks = [t for t in self._wake_tasks if not t.done()]
        self._wake_tasks.append(task)
        logger.info("已进入睡眠模式")
        return "晚安...我去睡了，有什么事明天再说"

    @filter.command("拉黑")
    async def add_blacklist(self, event: AstrMessageEvent):
        args = event.get_args()
        if not args:
            yield event.plain_result("用法: 拉黑 <用户ID> [分钟数]")
            return
        target = args[0].strip()
        minutes = 0
        if len(args) > 1:
            try:
                minutes = float(args[1])
            except ValueError:
                pass
        blacklist = self._get_list()
        if target in blacklist:
            yield event.plain_result(f"{target} 已经在黑名单里了")
            return
        if minutes > 0:
            if self.min_minutes > 0 and minutes < self.min_minutes:
                minutes = self.min_minutes
            if self.max_minutes > 0 and minutes > self.max_minutes:
                minutes = self.max_minutes
        blacklist.append(target)
        if minutes > 0:
            self._expiry[target] = time_module.time() + minutes * 60
            self._set_list(blacklist)
            _save_expiry(self._expiry)
            yield event.plain_result(f"已拉黑 {target}，{minutes}分钟后自动放行")
        else:
            self._expiry.pop(target, None)
            self._set_list(blacklist)
            _save_expiry(self._expiry)
            yield event.plain_result(f"已永久拉黑 {target}")

    @filter.command("取消拉黑")
    async def remove_blacklist(self, event: AstrMessageEvent):
        args = event.get_args()
        if not args:
            yield event.plain_result("用法: 取消拉黑 <用户ID>")
            return
        target = args[0].strip()
        blacklist = self._get_list()
        if target not in blacklist:
            yield event.plain_result(f"{target} 不在黑名单里")
            return
        blacklist.remove(target)
        self._expiry.pop(target, None)
        self._set_list(blacklist)
        _save_expiry(self._expiry)
        yield event.plain_result(f"已取消拉黑 {target}")

    @filter.command("黑名单")
    async def list_blacklist(self, event: AstrMessageEvent):
        blacklist = self._get_list()
        if not blacklist:
            yield event.plain_result("黑名单为空")
            return
        now = time_module.time()
        lines = []
        for uid in blacklist:
            exp = self._expiry.get(uid)
            if exp:
                left = int((exp - now) / 60)
                if left > 60:
                    lines.append(f"{uid}(剩{left//60}时{left%60}分)")
                else:
                    lines.append(f"{uid}(剩{left}分)")
            else:
                lines.append(f"{uid}(永久)")
        yield event.plain_result(f"当前黑名单: {', '.join(lines)}")
