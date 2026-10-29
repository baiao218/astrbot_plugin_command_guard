# Changelog

## 1.1.0

- 修复：摘除处理器改为**原地**修改 `activated_handlers` 列表（`handlers[:] = kept`）。
  v1.0.0 用 `set_extra` 换列表，`star_request.py` 内部的 `for` 循环仍握着旧列表，
  导致「日志打印了拦截，指令照常执行」
- 新增：`/闸门状态`、`/闸门重置` 管理命令
- 新增：`on_block` 三种处理模式（`llm` / `silent` / `text`）

## 1.0.0

- 首次发布：在 `priority=100000` 处摘掉指令类处理器，
  堵住私聊场景下 `PermissionType.SHARED_GROUP_ADMIN` 直接放行的口子
