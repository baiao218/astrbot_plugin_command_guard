# 指令闸门 · astrbot_plugin_command_guard

陌生人发来的 AstrBot 指令一律不执行。

## 安装

在 AstrBot WebUI 的**插件市场**搜索「指令闸门」，或者：

```bash
cd AstrBot/data/plugins
git clone https://github.com/baiao218/astrbot_plugin_command_guard.git
```

重启 AstrBot（或在插件页点「重载插件」）。

### 前置条件

1. **AstrBot ≥ 4.17.0**
2. 平台不限：拦截逻辑走的是 AstrBot 自己的处理器队列，不依赖任何 OneBot 扩展接口
3. 实际生效场景是**私聊**——群聊的权限判断本来就是对的，不需要它

### 许可

MIT，见 [LICENSE](LICENSE)。

---

## 它补的是什么口子

AstrBot 的指令权限里，`GROUP_ADMIN` 与 `SHARED_GROUP_ADMIN` 两档是这么判的：

```python
if self.permission_type == PermissionType.SHARED_GROUP_ADMIN and event.get_group_id():
    return event.is_admin() or bool(event.get_extra("_session_isolated", False))
return True          # ← 私聊时 get_group_id() 是空，条件不成立，直接放行
```

两档都先要求「有群号」。私聊没有群号，于是整个条件短路，落到 `return True`——**所有人放行**。

而负责清空上下文的 `reset` 正好挂在 `SHARED_GROUP_ADMIN` 上。再加上私聊默认 `friend_message_needs_wake_prefix = False`（不需要 `/` 前缀），结果就是：

> 任何好友打一句 `reset`，你的对话上下文就没了。

这不是配置问题，是核心的权限判断漏了私聊这一支。群聊反而是安全的：`get_group_id()` 非空，条件成立，会正常检查 `is_admin()`。

## 它怎么做

只挂私聊——漏洞就在私聊，群聊不用多管。

在 `activated_handlers`（本轮被激活的处理器）里，把「指令类」处理器**摘掉**，其余原样保留：

- 指令不会执行 —— 不是被权限挡住，是根本没进执行队列
- 消息继续流向 LLM —— 她用当前人格的口吻回应，而不是冷冰冰的「权限不足」
- 其它插件的普通钩子（表情包、观测注入等）完全不受影响

拦截点：

```python
@filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=100000)
```

`priority` 数值越大越先跑，必须早于指令处理器。

只挂私聊还有个副作用上的好处：群聊里没有被 @ 的消息不会因为本插件被标记成「已唤醒」，原有群聊行为一点不变。

## 三种拦截后的表现

| `on_block` | 表现 | 适合 |
|---|---|---|
| `llm`（默认） | 指令被吞，消息当普通聊天交给人格 | 对外会话，最自然 |
| `silent` | 完全不回应 | 不想浪费 token |
| `text` | 回一句固定话术，可用 `{command}` 占位 | 想明确表态 |

`llm` 模式下，对方打 `reset`，她看到的就是「reset」这一串文字，会按人格回应：

> 对方：reset
> 她：这个我做不到。……你原本想处理的是什么？

## 配套建议

**关掉 `no_permission_reply`。**

原本陌生人碰 ADMIN 指令会收到：

> 您(ID: 123456)的权限不足以使用此指令。通过 /sid 获取 ID 并请管理员添加。

这句话把「怎么加白名单」直接教给了对方。关掉之后权限检查静默跳过，消息落到人格层，她正常回应。

位置：`cmd_config.json` → `platform_settings.no_permission_reply = false`

## 命令

| 命令 | 作用 |
|---|---|
| `/闸门状态` | 开关、拦截后处理方式、本会话累计拦截次数 |
| `/闸门重置` | 清零拦截计数 |

均为管理员命令。

## 配置

| 项 | 默认 | 说明 |
|---|---|---|
| `enable` | `true` | 总开关 |
| `on_block` | `llm` | 拦截后怎么处理 |
| `block_text` | `这条指令对我无效。` | `text` 模式的话术 |
| `allow_group_admin` | `false` | 群里允许群主/管理员用指令（靠 `event.role`） |
| `skip_webchat` | `true` | 跳过面板测试对话 |
| `log` | `true` | 拦截时打日志 |

## 验证

重启后，拿小号私聊发：

```
reset
```

上下文应该**不会**被清空，她会照人格回一句。再发 `/上报状态`，不会有「权限不足」提示，而是正常的角色回应。

## 已知限制

- 这是「堵」不是「修」。根因在核心的权限判断里，插件只是站在前面挡住了
- 如果以后 AstrBot 官方修了 `PermissionType` 的私聊分支，可以关掉本插件
- 群聊里若出现 `_session_isolated` 为真的会话，`SHARED_GROUP_ADMIN` 也会对所有人放行；本插件只管私聊，这种情况管不到

## 版本

### 1.0.0

- 私聊范围内拦截非管理员的全部指令处理器
- `llm` / `silent` / `text` 三种拦截后处理
- `/闸门状态` `/闸门重置`
