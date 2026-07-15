"""
勿扰zzz - 拉黑拦截+睡眠模式管理
"""

import asyncio
import json
import random
import time as time_module
from pathlib import Path

from astrbot.api import star, logger, AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest

EXPIRY_PATH = Path("data/plugin_data/astrbot_plugin_silencer/expiry.json")
CACHE_PATH = Path("data/plugin_data/astrbot_plugin_silencer/sleep_cache.json")


SNAPSHOT_PATH = Path("data/plugin_data/astrbot_plugin_silencer/snapshot.json")


def _load_expiry() -> dict:
    if EXPIRY_PATH.exists():
        try:
            with open(EXPIRY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "expiry" in data:
                return data
            return {"expiry": data, "blacklist": []}
        except Exception:
            return {"expiry": {}, "blacklist": []}
    return {"expiry": {}, "blacklist": []}


def _save_expiry(expiry: dict, blacklist: list = None):
    data = {"expiry": expiry}
    if blacklist is not None:
        data["blacklist"] = blacklist
    EXPIRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EXPIRY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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


def _load_snapshot() -> dict:
    if SNAPSHOT_PATH.exists():
        try:
            with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_snapshot(data: dict):
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@star.register("astrbot_plugin_silencer", "miko", "勿扰zzz - 拉黑拦截+睡眠模式管理", "1.8.0")
class SilencerPlugin(star.Star):
    def __init__(self, context: star.Context, config: AstrBotConfig = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.enabled: bool = self.config.get("blacklist_enabled", True)
        self.admin_only: bool = self.config.get("admin_only", True)
        self.min_minutes: float = float(self.config.get("min_block_minutes", 0))
        self.max_minutes: float = float(self.config.get("max_block_minutes", 43200))
        loaded = _load_expiry()
        self._expiry: dict = loaded.get("expiry", {})
        saved_bl = loaded.get("blacklist", [])
        if saved_bl:
            current = self._get_list()
            merged = list(dict.fromkeys(current + saved_bl))
            if merged != current:
                self._set_list(merged)
        self._clean_expired()
        self._clean_internal_config()
        # 兼容旧版sleep_until
        su = self.config.get("sleep_until", [])
        if not isinstance(su, list):
            self.config["sleep_until"] = [su] if su else []
        self._wake_tasks = []
        self._internal_sleep_change = False
        self._peak_task = None
        snap = _load_snapshot()
        self._old_sleep_mode = snap.get("old_sleep_mode")
        self._old_sleep_sessions = snap.get("old_sleep_sessions")
        self._old_peak_enabled = snap.get("old_peak_enabled")
        self._old_sleep_until = snap.get("old_sleep_until")
        self._sleep_saved_scope = snap.get("sleep_saved_scope", "")
        self._restore_timers()
        # 后台检测配置变更
        async def _config_watcher():
            self._clean_internal_config()
            while True:
                await asyncio.sleep(10)
                try:
                    if self._internal_sleep_change:
                        self._internal_sleep_change = False
                        self._old_sleep_mode = self.config.get("sleep_mode", False)
                        self._old_sleep_sessions = self.config.get("sleep_mode_sessions", [])
                        self._old_sleep_until = self._get_until_list()
                        continue
                    old_mode = self._old_sleep_mode
                    old_ss = self._old_sleep_sessions
                    old_until = self._old_sleep_until
                    old_peak = self._old_peak_enabled
                    cur_mode = self.config.get("sleep_mode", False)
                    cur_ss = self.config.get("sleep_mode_sessions", [])
                    cur_until = self._get_until_list()
                    cur_peak = self.config.get("peak_hours_enabled", False)
                    if old_peak != cur_peak:
                        if cur_peak:
                            self._start_peak_watcher()
                            now = time_module.localtime()
                            now_m = now.tm_hour * 60 + now.tm_min
                            if 9 * 60 <= now_m < 12 * 60:
                                self._peak_sleep(12 * 60 - now_m)
                            elif 14 * 60 <= now_m < 18 * 60:
                                self._peak_sleep(18 * 60 - now_m)
                        else:
                            self._stop_peak_watcher()
                    if old_mode is not None and old_ss is not None and old_until is not None:
                        wake_sessions = set()
                        # 全局模式: sleep_mode true→false 则唤醒
                        if old_mode and not cur_mode:
                            wake_sessions.add("global")
                        # 会话模式: 被移除的会话
                        if old_ss != cur_ss:
                            for s in old_ss:
                                if s not in cur_ss:
                                    wake_sessions.add(s)
                        # 唤醒队列: 被移除的条目
                        if old_until != cur_until:
                            old_set = set(old_until)
                            cur_set = set(cur_until)
                            for entry in old_set - cur_set:
                                sess = entry.split(":")[0]
                                wake_sessions.add(sess)
                        for s in wake_sessions:
                            await self._execute_wake(s)
                    self._old_sleep_mode = cur_mode
                    self._old_sleep_sessions = cur_ss
                    self._old_peak_enabled = cur_peak
                    self._old_sleep_until = cur_until
                    self._snapshot_save()
                except:
                    pass
        asyncio.create_task(_config_watcher())
        # 启动时检查是否在高峰期
        if self.config.get("peak_hours_enabled", False):
            self._start_peak_watcher()
            now = time_module.localtime()
            now_m = now.tm_hour * 60 + now.tm_min
            if 9 * 60 <= now_m < 12 * 60:
                self._peak_sleep(12 * 60 - now_m)
            elif 14 * 60 <= now_m < 18 * 60:
                self._peak_sleep(18 * 60 - now_m)
        logger.info(f"勿扰zzz插件加载完成，当前黑名单: {self._get_list()}")

    # ── 唤醒时间列表操作 ──

    def _get_until_list(self) -> list:
        val = self.config.get("sleep_until", [])
        return val if isinstance(val, list) else []

    def _set_until_list(self, lst: list):
        self.config["sleep_until"] = lst
        try:
            self._config_save("set_until")
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
        saved = self._sleep_saved_scope
        if saved and saved != scope:
            _save_cache([])
            self._set_until_list([])
            self._sleep_clear()
            self.config["sleep_mode_sessions"] = []
            self._cancel_all_tasks()
            self._sleep_saved_scope = scope
            self._snapshot_save()
            try:
                self._config_save("scope_change")
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
        scope = str(self.config.get("sleep_scope", "全局"))
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
        self.config["sleep_mode"] = False

    # ── 高峰期自动静默 ──

    def _start_peak_watcher(self):
        if self._peak_task and not self._peak_task.done():
            return
        self._peak_task = asyncio.create_task(self._peak_scheduler())

    def _stop_peak_watcher(self):
        if self._peak_task and not self._peak_task.done():
            self._peak_task.cancel()
            self._peak_task = None

    async def _peak_scheduler(self):
        """精确到点触发高峰期，代替60秒轮询"""
        DAY = 24 * 60
        P1_S, P1_E = 9 * 60, 12 * 60
        P2_S, P2_E = 14 * 60, 18 * 60
        while True:
            try:
                now = time_module.localtime()
                now_m = now.tm_hour * 60 + now.tm_min
                n9 = (P1_S - now_m) % DAY
                n14 = (P2_S - now_m) % DAY
                if not n9: n9 = DAY
                if not n14: n14 = DAY
                if n9 <= n14:
                    await asyncio.sleep(n9 * 60)
                    self._peak_sleep(P1_E - P1_S)
                else:
                    await asyncio.sleep(n14 * 60)
                    self._peak_sleep(P2_E - P2_S)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(60)

    def _peak_sleep(self, duration_minutes: float):
        session = "global"
        dur_h = duration_minutes / 60
        self._internal_sleep_change = True
        self._cancel_all_tasks()
        self._set_until_list([])
        self.config["sleep_mode_sessions"] = []
        # 暂存原scope，强制全局（已有则不覆盖，防重载丢失）
        if "peak_original_scope" not in self.config:
            self.config["peak_original_scope"] = self.config.get("sleep_scope", "全局")
        self.config["sleep_scope"] = "全局"
        self._sleep_saved_scope = "全局"
        self._snapshot_save()
        self.config["sleep_mode"] = True
        self._add_until("global", time_module.time() + dur_h * 3600)
        try:
            self._config_save("peak_sleep")
        except Exception as e:
            logger.warning(f"高峰期静默保存配置失败: {e}")

        async def _auto_wake():
            try:
                await asyncio.sleep(dur_h * 3600)
                await self._execute_wake(session)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"高峰期唤醒异常: {e}")

        task = asyncio.create_task(_auto_wake())
        self._wake_tasks = [t for t in self._wake_tasks if not t.done()]
        self._wake_tasks.append(task)
        logger.info(f"高峰期自动静默 {duration_minutes}分钟")

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

    def _config_save(self, tag: str = ""):
        try:
            self.config.save_config()
        except:
            pass

    def _snapshot_save(self):
        _save_snapshot({
            "old_sleep_mode": self._old_sleep_mode,
            "old_sleep_sessions": self._old_sleep_sessions,
            "old_peak_enabled": self._old_peak_enabled,
            "old_sleep_until": self._old_sleep_until,
            "sleep_saved_scope": self._sleep_saved_scope,
        })

    def _clean_internal_config(self):
        dirty = False
        for k in list(self.config.keys()):
            if k.startswith("pre-config:") or k in ("just_woke_up", "_old_sleep_mode", "_old_sleep_sessions", "_old_peak_enabled", "sleep_saved_scope"):
                del self.config[k]
                dirty = True
        if dirty:
            self._config_save("clean_internal")

    def _get_list(self) -> list:
        return self.config.get("blacklist", [])

    def _set_list(self, blacklist: list):
        self.config["blacklist"] = blacklist
        try:
            self._config_save("set_blacklist")
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
            _save_expiry(self._expiry, self._get_list())

    async def _execute_wake(self, umo: str):
        """执行唤醒"""
        self._cancel_all_tasks()
        self._remove_until(umo)
        self._remove_until("global")
        # 直接设置睡眠状态，不调 _sleep_leave 避免递归
        scope = str(self.config.get("sleep_scope", "全局"))
        if scope in ("session", "仅触发的对话") and umo != "global":
            lst = self.config.get("sleep_mode_sessions", [])
            if isinstance(lst, list) and umo in lst:
                lst.remove(umo)
            self.config["sleep_mode_sessions"] = lst
        else:
            self.config["sleep_mode"] = False
            self.config["sleep_mode_sessions"] = []
        cache = _load_cache()
        _save_cache([])
        try:
            self._config_save("execute_wake")
        except:
            pass
        if cache:
            from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
            from astrbot.core.platform.message_type import MessageType
            from astrbot.api.message_components import Plain
            _scope = str(self.config.get("sleep_scope", "全局"))
            if _scope in ("session", "仅触发的对话") and umo != "global":
                cache = [c for c in cache if c.get("session") == umo]
            # 私聊按会话合并 群聊按会话+sender合并
            _groups = {}
            for c in cache:
                _is_group = c.get("msg_type", "FriendMessage") == "GroupMessage"
                _key = c.get("session", "") if not _is_group else f"{c.get('session','')}_{c.get('sender_id','')}"
                if _key not in _groups:
                    _groups[_key] = []
                _groups[_key].append(c)
            for _key, _msgs in _groups.items():
                _sess = _msgs[0].get("session", "")
                text = "睡后消息：" + chr(10)
                for c in _msgs:
                    text += f"来自 {c.get('sender_id','?')}: {c.get('message')}" + chr(10)
                msg_obj = AstrBotMessage()
                _mt = _msgs[0].get("msg_type", "FriendMessage")
                msg_obj.type = MessageType.GROUP_MESSAGE if _mt == "GroupMessage" else MessageType.FRIEND_MESSAGE
                if msg_obj.type == MessageType.GROUP_MESSAGE:
                    from astrbot.core.platform.astrbot_message import Group
                    _gid = _msgs[0].get("group_id", _sess.split(":")[-1])
                    _gname = _msgs[0].get("group_name", _gid)
                    msg_obj.group = Group(group_id=_gid)
                    msg_obj.group.group_name = _gname
                msg_obj.self_id = _msgs[0].get("self_id", "0")
                msg_obj.session_id = _sess.split(":")[-1]
                msg_obj.message_id = str(time_module.time())
                msg_obj.sender = MessageMember(user_id=_msgs[0].get("sender_id", "0"), nickname=_msgs[0].get("sender_name", "系统"))
                msg_obj.message = [Plain(text)]
                msg_obj.message_str = text
                msg_obj.raw_message = {"text": text}
                for p in self.context.platform_manager.platform_insts:
                    if p.meta().id == _sess.split(":")[0]:
                        _evt = p.create_event(msg_obj)
                        _evt.is_at_or_wake_command = True
                        p.commit_event(_evt)
                        break
                else:
                    for p in self.context.platform_manager.platform_insts:
                        _evt = p.create_event(msg_obj)
                        _evt.is_at_or_wake_command = True
                        p.commit_event(_evt)
                        break
        logger.info("唤醒执行完毕")
        # 高峰期结束后恢复原scope
        orig = self.config.get("peak_original_scope", "")
        if orig:
            self.config["sleep_scope"] = orig
            self._sleep_saved_scope = orig
            self._snapshot_save()
            del self.config["peak_original_scope"]
            try:
                self._config_save("wake_peak_restore")
            except:
                pass

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
                self._config_save("sleep_cleanup")
            except:
                pass
        # 刚睡醒注入
        if self.config.get("just_woke_up", False):
            self.config["just_woke_up"] = False
            try:
                self._config_save("just_woke_up")
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
                    _waker = event.get_sender_id()
                    _admins = self.config.get("wake_whitelist", [])
                    _role = getattr(event, 'role', None)
                    if _role not in ("admin",) and _waker not in _admins:
                        logger.info(f"唤醒权限不足: {_waker}")
                        event.call_llm = False
                        event.stop_event()
                        return
                    self.config["just_woke_up"] = True
                    await self._execute_wake(session)
                    try:
                        self._config_save("wake_cmd")
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
                            _msg_type_parts = sess.split(":")
                            _msg_type_val = _msg_type_parts[1] if len(_msg_type_parts) >= 3 else "FriendMessage"
                            _cache_item = {
                                "session": sess,
                                "sender_id": sender_id,
                                "sender_name": event.get_sender_name(),
                                "self_id": event.get_self_id(),
                                "message": event.message_str,
                                "msg_type": _msg_type_val,
                                "platform_name": event.platform_meta.name,
                                "time": time_module.time()
                            }
                            if _msg_type_val == "GroupMessage" and event.message_obj.group:
                                _cache_item["group_id"] = event.message_obj.group.group_id
                                _cache_item["group_name"] = event.message_obj.group.group_name
                            cache.append(_cache_item)
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
        '''
        拉黑指定用户，阻止其消息被回复。当用户违规或你不想理他时，主动拉黑。用户ID可从当前对话上下文、最近消息或@信息中获取。

        Args:
            user_id(string): 需要拉黑的用户ID。可以从对话历史、群成员信息或@内容中提取
            duration_minutes(number): 拉黑时长(分钟)，0表示自动按配置区间。可选参数，默认0
        '''
        if not self.config.get("llm_auto_block", False):
            role = getattr(event, 'role', None)
            sid = getattr(event, 'get_sender_id', lambda: '')()
            wl = self.config.get("cmd_whitelist", [])
            if self.admin_only and role not in ("admin",) and sid not in wl:
                return "权限不足"
        immune = self.config.get("immune_list", [])
        if user_id in immune:
            return f"{user_id} 受免疫名单保护，无法拉黑"
        blacklist = self._get_list()
        if user_id in blacklist:
            exp = self._expiry.get(user_id)
            if exp:
                left = int((exp - time_module.time()) / 60)
                return f"{user_id} 已在黑名单中，剩余约{left}分钟"
            return f"{user_id} 已经在黑名单里了(永久)"
        # 未指定时长时从配置区间随机
        if duration_minutes <= 0:
            mn = self.min_minutes or 30
            mx = self.max_minutes or 1440
            if mx < mn:
                mx = mn
            duration_minutes = random.uniform(mn, mx)
        # 限制在配置范围内
        if self.min_minutes > 0 and duration_minutes < self.min_minutes:
            duration_minutes = self.min_minutes
        if self.max_minutes > 0 and duration_minutes > self.max_minutes:
            duration_minutes = self.max_minutes
        blacklist.append(user_id)
        if duration_minutes > 0:
            self._expiry[user_id] = time_module.time() + duration_minutes * 60
            self._set_list(blacklist)
            _save_expiry(self._expiry, self._get_list())
            logger.info(f"已拉黑用户: {user_id}，时长{duration_minutes}分钟")
            return f"已拉黑 {user_id}，{duration_minutes}分钟后自动放行"
        else:
            self._expiry.pop(user_id, None)
            self._set_list(blacklist)
            _save_expiry(self._expiry, self._get_list())
            logger.info(f"已永久拉黑用户: {user_id}")
            return f"已永久拉黑 {user_id}"

    @filter.llm_tool(name="unblock_user")
    async def unblock_user(self, event: AstrMessageEvent, user_id: str):
        '''
        取消拉黑指定用户，恢复其消息被回复的权限。用户ID可从当前对话上下文或@信息中获取。

        Args:
            user_id(string): 需要取消拉黑的用户ID
        '''
        if self.config.get("llm_auto_block", False):
            sid = str(event.get_sender_id())
            if user_id != sid:
                role = getattr(event, 'role', None)
                wl = self.config.get("cmd_whitelist", [])
                if self.admin_only and role not in ("admin",) and sid not in wl:
                    return "权限不足"
        else:
            role = getattr(event, 'role', None)
            sid = getattr(event, 'get_sender_id', lambda: '')()
            wl = self.config.get("cmd_whitelist", [])
            if self.admin_only and role not in ("admin",) and sid not in wl:
                return "权限不足"
        blacklist = self._get_list()
        if user_id not in blacklist:
            return f"{user_id} 不在黑名单里"
        blacklist.remove(user_id)
        self._expiry.pop(user_id, None)
        self._set_list(blacklist)
        _save_expiry(self._expiry, self._get_list())
        logger.info(f"已取消拉黑用户: {user_id}")
        return f"已取消拉黑 {user_id}"

    @filter.llm_tool(name="go_to_sleep")
    async def go_to_sleep(self, event: AstrMessageEvent, duration_minutes: float = 0):
        '''
        进入勿扰模式(睡觉)，期间不回复任何消息(白名单除外)。当用户让你去睡觉或休息时，应直接调用此工具，不要只口头答应。

        Args:
            duration_minutes(number): 睡眠时长(分钟)。不传参时必须传0；传0或留空则自动随机。仅管理员和sleep_custom_users可指定时长。可选参数，默认0
        '''
        role = getattr(event, 'role', None)
        if not self.config.get("sleep_public", False) and self.admin_only and role not in ("admin",):
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
            dur_min = float(self.config.get("sleep_duration_min", 2))
            dur_max = float(self.config.get("sleep_duration_max", 3))
            if dur_max < dur_min:
                dur_max = dur_min
            dur_h = random.uniform(dur_min, dur_max)
        session = ""
        try:
            session = event.context.event.unified_msg_origin
        except:
            try:
                session = event.unified_msg_origin
            except:
                session = ""
        self._internal_sleep_change = True
        self._sleep_enter(session)
        scope = str(self.config.get("sleep_scope", "全局"))
        self._sleep_saved_scope = scope
        self._snapshot_save()
        if scope in ("session", "仅触发的对话"):
            self._add_until(session, time_module.time() + dur_h * 3600)
        else:
            self._add_until("global", time_module.time() + dur_h * 3600)
        try:
            self._config_save("go_sleep")
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
        sid = event.get_sender_id()
        role = getattr(event, 'role', None)
        wl = self.config.get("cmd_whitelist", [])
        if self.admin_only and role not in ("admin",) and sid not in wl:
            yield event.plain_result("权限不足")
            return
        # 尝试从消息链提取at
        target = ""
        for comp in event.get_messages():
            if hasattr(comp, 'qq') and str(comp.qq).isdigit():
                target = str(comp.qq)
                break
        if not target:
            parts = event.message_str.strip().split()
            if len(parts) >= 2:
                target = parts[1]
        if not target:
            yield event.plain_result("用法: 拉黑 <用户ID> [分钟数]")
            return
        minutes = 0
        for part in event.message_str.strip().split():
            try:
                minutes = float(part)
            except:
                continue
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
            _save_expiry(self._expiry, self._get_list())
            yield event.plain_result(f"已拉黑 {target}，{minutes}分钟后自动放行")
        else:
            self._expiry.pop(target, None)
            self._set_list(blacklist)
            _save_expiry(self._expiry, self._get_list())
            yield event.plain_result(f"已永久拉黑 {target}")

    @filter.command("取消拉黑")
    async def remove_blacklist(self, event: AstrMessageEvent):
        sid = event.get_sender_id()
        role = getattr(event, 'role', None)
        wl = self.config.get("cmd_whitelist", [])
        if self.admin_only and role not in ("admin",) and sid not in wl:
            yield event.plain_result("权限不足")
            return
        target = ""
        for comp in event.get_messages():
            if hasattr(comp, 'qq') and str(comp.qq).isdigit():
                target = str(comp.qq)
                break
        if not target:
            parts = event.message_str.strip().split()
            if len(parts) >= 2:
                target = parts[1]
        if not target:
            yield event.plain_result("用法: 取消拉黑 <用户ID>")
            return
        blacklist = self._get_list()
        if target not in blacklist:
            yield event.plain_result(f"{target} 不在黑名单里")
            return
        blacklist.remove(target)
        self._expiry.pop(target, None)
        self._set_list(blacklist)
        _save_expiry(self._expiry, self._get_list())
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
