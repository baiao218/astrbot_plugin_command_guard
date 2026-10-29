"""指令闸门 —— 陌生人的 AstrBot 指令无效化。

**为什么需要它**

AstrBot 的内置指令权限里，`GROUP_ADMIN` / `SHARED_GROUP_ADMIN` 两档是这样判的：

```python
if self.permission_type == PermissionType.SHARED_GROUP_ADMIN and event.get_group_id():
    return event.is_admin() or ...
return True          # ← 私聊时 get_group_id() 为空，直接落到这里
```

也就是说：**在私聊里，这两档权限对所有人放行**。而「清空上下文」的 `reset`
正好挂在 `SHARED_GROUP_ADMIN` 上。所以任何好友只要打一句 `reset`（私聊默认
不需要唤醒前缀），就能把你的对话上下文清掉。

**它怎么做**

只挂私聊（漏洞就在私聊，群聊里核心的权限判断是正常的，不用多管）。

在 `activated_handlers`（本轮被激活的处理器）里，把「指令类」的处理器摘掉，
其余原样保留。于是：

- 指令不会执行 —— 不是靠权限判断，是根本没进队列；
- 消息继续流向 LLM —— 她会用当前人格的口吻回应，而不是冷冰冰的「权限不足」；
- 其它插件的普通钩子（表情包、观测注入等）不受影响。

拦截点在 `@filter.event_message_type(PRIVATE_MESSAGE, priority=100000)`：
优先级数值越大越先跑，必须早于指令处理器。

**坑：必须原地修改列表，不能换引用**

第一版写的是 `event.set_extra("activated_handlers", kept)`，结果指令照样执行。
原因在 AstrBot 的管线实现：

```python
# waking_check/stage.py:237
event.set_extra("activated_handlers", activated_handlers)

# process_stage/method/star_request.py:30
activated_handlers = event.get_extra("activated_handlers")   # ← 拿到同一对象
for handler in activated_handlers:                            # ← 迭代它
```

`set_extra` 只是把 extras 字典里这个键指向一个新列表，**早已绑定成局部变量的
`for` 循环仍握着旧列表**，摘除对它无效。而且 `list(...)` 顺手复制一份的话，
连旧列表都改不到。所以这里必须：

1. `get_extra()` 直接取，**不做 `list()` 拷贝**；
2. 用切片赋值 `live[:] = kept` **原地**改内容。

迭代器按索引读取，切片赋值会缩短列表，但 `kept` 里剩的全是非指令处理器，
无论索引落在哪一位都不会误执行指令。

只挂私聊还有一个副作用上的好处：群聊里没有被 @ 的消息不会因为本插件而
被标记成「已唤醒」，原有的群聊行为完全不变。
"""

from __future__ import annotations

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.filter.regex import RegexFilter

PLUGIN_NAME = "astrbot_plugin_command_guard"

# 指令处理器必须最早被处理，所以给一个远比默认值大的优先级
GUARD_PRIORITY = 100000


class CommandGuardPlugin(Star):
    """非管理员发来的指令一律不执行。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        self.settings = config
        self._blocked: dict[str, int] = {}

    # ---------------- 判定 ----------------

    def _is_command_handler(self, handler) -> bool:
        block_regex = bool(self.settings.get("block_regex", False))
        for f in getattr(handler, "event_filters", []) or []:
            if isinstance(f, (CommandFilter, CommandGroupFilter)):
                return True
            if block_regex and isinstance(f, RegexFilter):
                return True
        return False

    def _allowed(self, event: AstrMessageEvent) -> bool:
        """这个发送者能不能用指令。"""
        try:
            if event.is_admin():
                return True
        except Exception:
            pass

        # 群管理员：部分平台把群主/管理员放在 event.role 里
        if bool(self.settings.get("allow_group_admin", False)) and event.get_group_id():
            if str(getattr(event, "role", "") or "") in ("admin", "owner"):
                return True

        return False

    def _skip_platform(self, event: AstrMessageEvent) -> bool:
        if not bool(self.settings.get("skip_webchat", True)):
            return False
        try:
            return event.get_platform_name() == "webchat"
        except Exception:
            return False

    # ---------------- 主钩子 ----------------

    @filter.event_message_type(
        filter.EventMessageType.PRIVATE_MESSAGE, priority=GUARD_PRIORITY
    )
    async def guard(self, event: AstrMessageEvent) -> None:
        if not bool(self.settings.get("enable", True)):
            return
        if self._skip_platform(event):
            return
        if self._allowed(event):
            return

        handlers = event.get_extra("activated_handlers")
        if not isinstance(handlers, list) or not handlers:
            return

        blocked = [h for h in handlers if self._is_command_handler(h)]
        if not blocked:
            return
        kept = [h for h in handlers if not self._is_command_handler(h)]

        # 摘掉指令处理器，其余保留（表情包、注入等普通钩子照常工作）。
        #
        # 必须原地切片赋值：star_request 的 for 循环在进入循环前就把这个列表
        # 绑定成了局部变量，set_extra 换个新列表对它无效，指令会照跑。
        # 同时这里不能对 get_extra 的返回值做 list() 拷贝，否则改的是副本。
        handlers[:] = kept

        names = sorted({str(getattr(h, "handler_name", "") or "") for h in blocked})
        names = [n for n in names if n]
        umo = getattr(event, "unified_msg_origin", "") or ""
        self._blocked[umo] = self._blocked.get(umo, 0) + 1

        if bool(self.settings.get("log", True)):
            logger.info(
                f"[command_guard] 拦截来自 {event.get_sender_id()} 的指令 "
                f"{names}（{umo}）"
            )

        mode = str(self.settings.get("on_block") or "llm").strip().lower()

        if mode == "silent":
            event.stop_event()
            return

        if mode == "text":
            template = str(
                self.settings.get("block_text") or "这条指令对我无效。"
            )
            text = template.replace("{command}", "、".join(names) or "指令")
            await event.send(event.plain_result(text))
            event.stop_event()
            return

        # 默认 llm：什么都不做，让消息落到模型，她用当前人格回应
        return

    # ---------------- 自检 ----------------

    @filter.on_astrbot_loaded()
    async def self_check(self, *args, **kwargs) -> None:
        """启动时确认自己排在处理器队列首位。

        `star_request` 的 `for handler in activated_handlers` 是按索引往前走的。
        我们的切片赋值会让迭代器跳过「本插件之前」的位置，所以本插件必须排第一，
        否则它前面的普通处理器（表情包之类）会被漏掉。正常情况下 100000 的优先级
        足以保证，这里只是留一条可诊断的日志。
        """
        try:
            from astrbot.core.star.star_handler import (
                EventType,
                star_handlers_registry,
            )

            handlers = star_handlers_registry.get_handlers_by_event_type(
                EventType.AdapterMessageEvent
            )
            pos = None
            for i, h in enumerate(handlers):
                if getattr(h, "handler_name", "") == "guard" and PLUGIN_NAME in str(
                    getattr(h, "handler_module_path", "") or ""
                ):
                    pos = i
                    break
            if pos == 0:
                logger.info(
                    f"[command_guard] 就绪，位于处理器队列首位"
                    f"（共 {len(handlers)} 个消息处理器）"
                )
            else:
                logger.warning(
                    f"[command_guard] 位置异常：排在队列第 {pos} 位，"
                    f"而非首位。指令仍会被拦住，但它前面的普通处理器本轮可能被跳过。"
                    f"请检查是否有插件使用了大于 {GUARD_PRIORITY} 的 priority。"
                )
        except Exception as e:  # noqa: BLE001 - 自检失败不能影响插件加载
            logger.debug(f"[command_guard] 自检跳过: {e}")

    # ---------------- 命令 ----------------

    @filter.command("闸门状态")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_status(self, event: AstrMessageEvent):
        """查看指令拦截情况。"""
        umo = getattr(event, "unified_msg_origin", "") or ""
        lines = ["【指令闸门】"]
        lines.append("开关：" + ("开" if bool(self.settings.get("enable", True)) else "关"))
        lines.append(
            "拦截后：" + {
                "llm": "交给模型，由人格回应",
                "silent": "完全静默",
                "text": "回固定话术",
            }.get(str(self.settings.get("on_block") or "llm"), "未知")
        )
        lines.append("允许群管理员：" + ("是" if bool(self.settings.get("allow_group_admin", False)) else "否"))
        lines.append(f"本会话累计拦截：{self._blocked.get(umo, 0)} 次")
        yield event.plain_result("\n".join(lines))

    @filter.command("闸门重置")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_reset(self, event: AstrMessageEvent):
        """清零拦截计数。"""
        self._blocked.clear()
        yield event.plain_result("拦截计数已清零。")
