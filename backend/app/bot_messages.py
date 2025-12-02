from __future__ import annotations

"""集中管理 Bot 对外发送的所有消息文案。

本文件中的字符串可以由用户直接编辑，以调整 Bot 的提示文案和排版。
变量占位符使用 Python 的 str.format 语法，例如 {download_id}、{file_name} 等。
"""

# 基本提示与错误
NO_PERMISSION = "❌ 您没有权限使用此Bot"
UNKNOWN_COMMAND = "❓ 未知命令。使用 /help 查看可用命令"

# /help 命令
HELP_TEXT = (
    "🤖 **Telegram下载管理器Bot**\n\n"
    "**可用命令：**\n"
    "/help - 显示此帮助信息\n"
    "/download - 查看下载统计信息\n"
    "/createrule - 创建群聊下载规则\n"
    "/cancel - 取消当前操作\n"
    "/dedupe_on - 开启机器人重复文件检测\n"
    "/dedupe_off - 关闭机器人重复文件检测\n\n"
    "**使用方法：**\n"
    "1. 直接向Bot发送视频或文件，系统会自动下载\n"
    "2. 使用 /createrule 创建群聊自动下载规则"
)

# 启动通知
STARTUP_MESSAGE = (
    "🚀 **Telegram Depiler已启动 (v{version})**\n\n"
    "✅ Bot已就绪，正在监听消息\n\n"
    "📖 **可用命令：**\n"
    "/help - 显示帮助信息\n"
    "/download - 查看下载统计\n"
    "/createrule - 创建群聊下载规则\n"
    "/cancel - 取消当前操作\n\n"
    "• ✨使用方法：\n"
    "• 直接发送文件给Bot即可下载\n"
    "• 使用 /createrule 设置群聊自动下载\n"
    "• 支持视频、图片、音频、文档等文件类型"
)

# 下载相关
DOWNLOAD_OVERVIEW_HEADER = (
    "📊 **下载概览**\n\n"
    "**总计：** {total}\n"
    "✅ **成功：** {completed}\n"
    "⏳ **下载中：** {downloading}\n"
    "❌ **失败：** {failed}\n\n"
)

NO_ACTIVE_DOWNLOADS = "当前没有正在进行的下载任务。"

DOWNLOAD_ITEM_LINE = (
    "• 任务ID: `{download_id}` | 状态: {status}\n"
    "  进度: {progress:.1f}% | 速度: {speed_text}\n"
    "  文件: {file_name}"
)

DOWNLOAD_START_MESSAGE = (
    "📥 **开始下载**\n\n"
    "**文件ID：** `{message_id}`\n"
    "**任务ID：** `{download_id}`\n"
    "**文件名：** {file_name}\n"
    "**大小：** {file_size}\n"
    "**类型：** {media_type}\n"
    "**速度：** 计算中...\n\n"
    "**下载统计：**\n"
    "总计：{total} | 成功：{completed} | 失败：{failed}"
)

DOWNLOAD_QUEUED_MESSAGE = (
    "📋 **任务已加入队列**\n\n"
    "**文件ID：** `{message_id}`\n"
    "**任务ID：** `{download_id}`\n"
    "**文件名：** {file_name}\n"
    "**大小：** {file_size}\n"
    "**类型：** {media_type}\n\n"
    "当前有5个任务正在下载，本任务将在队列中等待...\n\n"
    "**下载统计：**\n"
    "总计：{total} | 成功：{completed} | 失败：{failed}"
)

DOWNLOAD_COMPLETED_MESSAGE = (
    "✅ **下载完成**\n\n"
    "**文件ID：** `{message_id}`\n"
    "**任务ID：** `{download_id}`\n"
    "**文件名：** {file_name}\n"
    "**大小：** {file_size}\n"
    "**平均速度：** {avg_speed}\n"
    "**耗时：** {elapsed:.1f}秒\n\n"
    "**下载统计：**\n"
    "总计：{total} | 成功：{completed} | 失败：{failed}"
)

DOWNLOAD_FAILED_MESSAGE = (
    "❌ **下载失败**\n\n"
    "**文件ID：** `{message_id}`\n"
    "**文件名：** {file_name}\n"
    "**错误：** {error}\n\n"
    "**下载统计：**\n"
    "总计：{total} | 成功：{completed} | 失败：{failed}"
)

DOWNLOADING_PROGRESS_MESSAGE = (
    "📥 **正在下载**\n\n"
    "**文件ID：** `{message_id}`\n"
    "**任务ID：** `{download_id}`\n"
    "**文件名：** {file_name}\n"
    "**大小：** {file_size}\n"
    "**类型：** {media_type}\n"
    "**进度：** {progress_bar} {progress_percent}\n"
    "**速度：** {speed_text}"
)

DOWNLOAD_DELETED_MESSAGE = "🗑️ **已删除**\n\n此下载任务已被删除。"

# 重复文件检测
DEDUPE_HIT_MESSAGE = (
    "⚠️ 此文件之前已下载过，将不再重复下载。\n\n"
    "已有任务ID：`{existing_id}`\n"
    "保存路径：`{existing_path}`\n\n"
    "如需再次下载此文件，可先使用 /dedupe_off 关闭重复检测，再重新发送。"
)

DEDUPE_ON_MESSAGE = "✅ 已开启机器人重复文件检测（基于 Telegram 文件 ID）"
DEDUPE_OFF_MESSAGE = "⚠️ 已关闭机器人重复文件检测，Bot 将对相同文件重复下载"

# 暂停相关
PAUSE_DOWNLOAD_NOT_FOUND = "❌ 下载记录不存在"
PAUSE_SUCCESS_ANSWER = "⏸️ 已暂停下载"
PAUSE_FAILED_ANSWER = "❌ 暂停失败"
PAUSE_ALREADY_PAUSED_ANSWER = "ℹ️ 下载已经是暂停状态"
PAUSE_INVALID_STATUS_ANSWER = "ℹ️ 当前状态 ({status}) 无法暂停"
PAUSE_ERROR_ANSWER = "❌ 暂停失败: {error}"

PAUSE_MESSAGE_BODY = (
    "⏸️ **已暂停**\n\n"
    "文件: {file_name}\n"
    "状态: 已暂停\n\n"
    "使用 /download 命令查看所有下载"
)

# 优先级相关
PRIORITY_SET_HIGH_ANSWER = "⭐ 已设置为高优先级"
PRIORITY_RESET_ANSWER = "📋 已恢复正常优先级"

PRIORITY_SET_HIGH_MESSAGE = (
    "⭐ **高优先级**\n\n"
    "文件: {file_name}\n"
    "状态: {status}\n"
    "优先级: 高\n\n"
    "此任务将优先处理"
)

# 其他可以继续补充：
# - 创建规则向导中的每一步提示
# - 规则历史下载范围提示
