from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional
from telethon import TelegramClient, events
from telethon.tl.types import User, KeyboardButtonCallback

from .config import Settings
from .database import Database

logger = logging.getLogger(__name__)


class BotCommandHandler:
    """处理Bot命令的独立处理器"""
    
    def __init__(self, settings: Settings, database: Database, user_client: TelegramClient, worker=None, queue_manager=None):
        self.settings = settings
        self.database = database
        self.user_client = user_client  # 用户账户客户端，用于下载文件
        self.worker = worker  # TelegramWorker实例，用于取消下载
        self.queue_manager = queue_manager  # 全局下载队列管理器
        self._bot_client: Optional[TelegramClient] = None
        self._bot_username: Optional[str] = None
        self._bot_id: Optional[int] = None
        self._download_semaphore = asyncio.Semaphore(5)
        self._active_downloads: dict[int, bool] = {}
        self._download_tasks: dict[int, asyncio.Task] = {}
        self._cancelled_downloads: set[int] = set()
        self._conversation_states: dict[int, dict] = {}  # 用户对话状态
        self._logger = logger
        
    async def start(self) -> None:
        """启动Bot命令处理器"""
        if not self.settings.bot_token:
            logger.warning("Bot Token未配置，无法启动Bot命令处理器")
            return
            
        try:
            proxy = None
            if self.settings.proxy_host and self.settings.proxy_port:
                proxy_host = self.settings.proxy_host.strip()
                for prefix in ("http://", "https://", "socks4://", "socks5://", "socks://"):
                    if proxy_host.lower().startswith(prefix):
                        proxy_host = proxy_host[len(prefix):].strip()
                        break

                if "/" in proxy_host:
                    proxy_host = proxy_host.split("/")[0]

                if proxy_host.startswith("[") and proxy_host.endswith("]"):
                    proxy_host = proxy_host[1:-1]
                elif ":" in proxy_host:
                    parts = proxy_host.split(":")
                    if len(parts) == 2 and "." in parts[0]:
                        proxy_host = parts[0]

                original_host = proxy_host
                if proxy_host in ("127.0.0.1", "localhost", "::1"):
                    if os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER") == "true":
                        proxy_host = "host.docker.internal"
                        logger.info(
                            "Detected Docker environment for bot client, converting proxy host %s to %s",
                            original_host,
                            proxy_host,
                        )

                proxy_type = (self.settings.proxy_type or "http").lower()
                if proxy_type not in ("http", "socks4", "socks5"):
                    logger.warning("Unknown proxy type %s for bot client, defaulting to http", proxy_type)
                    proxy_type = "http"

                proxy = (
                    proxy_type,
                    proxy_host,
                    int(self.settings.proxy_port),
                    True,
                    self.settings.proxy_user,
                    self.settings.proxy_password,
                )

                logger.info(
                    "Bot client using proxy: %s://%s:%s (user=%s)",
                    proxy_type,
                    proxy_host,
                    self.settings.proxy_port,
                    self.settings.proxy_user or "none",
                )

            session_path = Path(self.settings.data_dir) / "bot_session"
            self._bot_client = TelegramClient(
                str(session_path),
                int(self.settings.api_id),
                self.settings.api_hash,
                proxy=proxy,
                use_ipv6=False,
                connection_retries=5,
                retry_delay=2,
                timeout=30,
                auto_reconnect=True,
            )

            # 使用 bot_token 完成 Bot 登录
            await self._bot_client.start(bot_token=self.settings.bot_token)

            if not await self._bot_client.is_user_authorized():
                logger.error("Bot Token无效或未授权")
                raise RuntimeError("Bot Token 无效或未授权")
                
            bot_info = await self._bot_client.get_me()
            self._bot_username = bot_info.username
            self._bot_id = bot_info.id
            logger.info(f"Bot命令处理器已启动: @{self._bot_username} (ID: {bot_info.id})")
            
            # 设置Bot命令菜单
            await self._set_bot_commands()
            
            # 注册事件处理器
            self._bot_client.add_event_handler(
                self._handle_bot_command,
                events.NewMessage(pattern=r'^/')
            )
            self._bot_client.add_event_handler(
                self._handle_bot_message,
                events.NewMessage
            )
            self._bot_client.add_event_handler(
                self._handle_callback_query,
                events.CallbackQuery
            )
            
            # 启动Bot客户端（在后台运行）
            logger.info("Bot命令处理器正在监听消息...")
            # 在后台任务中运行Bot客户端
            asyncio.create_task(self._run_bot())
            
            # 发送启动通知给管理员
            await self._send_startup_notification()
            
        except Exception as e:
            logger.exception(f"启动Bot命令处理器失败: {e}")
            raise
            
    async def _set_bot_commands(self) -> None:
        """设置Bot命令菜单"""
        try:
            from telethon.tl.types import BotCommand, BotCommandScopeDefault
            from telethon.tl.functions.bots import SetBotCommandsRequest
            
            commands = [
                BotCommand(command="help", description="显示帮助信息"),
                BotCommand(command="download", description="查看下载统计信息"),
                BotCommand(command="createrule", description="创建群聊下载规则"),
                BotCommand(command="cancel", description="取消当前操作"),
            ]
            
            await self._bot_client(SetBotCommandsRequest(
                scope=BotCommandScopeDefault(),
                lang_code="zh",
                commands=commands
            ))
            logger.info("Bot命令菜单已设置")
        except Exception as e:
            logger.warning(f"设置Bot命令菜单失败: {e}")
    
    async def _send_startup_notification(self) -> None:
        """发送启动通知给管理员"""
        if not self.settings.admin_user_ids:
            return
        
        try:
            from telethon.tl.types import User as TgUser

            startup_message = (
                f"🚀 **Telegram Depiler已启动 (v{self.settings.version})**\n\n"
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
            
            for admin_id in self.settings.admin_user_ids:
                try:
                    entity = await self._bot_client.get_entity(admin_id)
                    if not isinstance(entity, TgUser):
                        logger.info("管理员ID %s 不是用户账号（可能是频道/群），跳过启动通知", admin_id)
                        continue

                    await self._bot_client.send_message(
                        entity.id,
                        startup_message,
                        parse_mode="markdown",
                    )
                    logger.info(f"已发送启动通知给管理员用户 {entity.id}")
                except Exception as e:
                    logger.warning(f"发送启动通知给管理员 {admin_id} 失败: {e}")
        except Exception as e:
            logger.exception(f"发送启动通知失败: {e}")
            
    async def _handle_bot_command(self, event: events.NewMessage.Event) -> None:
        """处理Bot命令"""
        if not event.message or not event.message.text:
            return
            
        command = event.message.text.split()[0].lower()
        sender = await event.get_sender()
        
        if not isinstance(sender, User):
            return
            
        sender_id = sender.id
        
        # 验证管理员权限
        if self.settings.admin_user_ids and sender_id not in self.settings.admin_user_ids:
            await event.reply("❌ 您没有权限使用此Bot")
            return
            
        if command == "/help":
            await self._handle_help_command(event)
        elif command == "/download":
            await self._handle_download_command(event)
        elif command == "/createrule":
            await self._handle_createrule_command(event)
        elif command == "/cancel":
            await self._handle_cancel_command(event)
        elif command == "/dedupe_on":
            # 开启机器人重复文件检测（基于 Telegram 文件 ID）
            self.database.set_config({"bot_dedupe_enabled": "1"})
            await event.reply("✅ 已开启机器人重复文件检测（基于 Telegram 文件 ID）")
        elif command == "/dedupe_off":
            # 关闭机器人重复文件检测，允许对相同文件重复下载
            self.database.set_config({"bot_dedupe_enabled": "0"})
            await event.reply("⚠️ 已关闭机器人重复文件检测，Bot 将对相同文件重复下载")
        else:
            await event.reply("❓ 未知命令。使用 /help 查看可用命令")
            
    async def _handle_help_command(self, event: events.NewMessage.Event) -> None:
        """处理/help命令"""
        help_text = (
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
        await event.reply(help_text, parse_mode='markdown')
        
    async def _handle_download_command(self, event: events.NewMessage.Event) -> None:
        """处理/download命令"""
        # 全局统计
        stats = self.database.get_download_stats()
        total = stats.get("total", 0)
        completed = stats.get("completed", 0)
        failed = stats.get("failed", 0)
        downloading = stats.get("downloading", 0)

        # 获取最近的下载记录，并筛选出正在进行的任务
        downloads = self.database.list_downloads(limit=200)
        active_status = {"downloading", "pending", "paused", "queued"}
        active_downloads = [d for d in downloads if d.get("status") in active_status]

        header_text = (
            "📊 **下载概览**\n\n"
            f"**总计：** {total}\n"
            f"✅ **成功：** {completed}\n"
            f"⏳ **下载中：** {downloading}\n"
            f"❌ **失败：** {failed}\n\n"
        )

        if not active_downloads:
            header_text += "当前没有正在进行的下载任务。"
            await event.reply(header_text, parse_mode='markdown')
            return

        # 构建正在下载列表和操作按钮
        lines: list[str] = []
        buttons: list[list[KeyboardButtonCallback]] = []

        for d in active_downloads[:10]:
            download_id = d.get("id")
            if download_id is None:
                continue

            file_name = d.get("file_name") or "未知"
            status = d.get("status") or "unknown"
            progress = float(d.get("progress") or 0.0)
            speed = float(d.get("download_speed") or 0.0)
            speed_text = self._format_speed(speed) if speed > 0 else "计算中..."
            
            # 状态显示文本
            status_text = {
                "downloading": "⏳ 下载中",
                "paused": "⏸️ 已暂停",
                "queued": "📋 队列中",
                "pending": "⏳ 等待中"
            }.get(status, status)

            lines.append(
                f"• 任务ID: `{download_id}` | 状态: {status_text}\n"
                f"  进度: {progress:.1f}% | 速度: {speed_text}\n"
                f"  文件: {file_name}"
            )

            # 根据状态显示不同的按钮
            row_buttons = []
            if status == "downloading":
                row_buttons.append(KeyboardButtonCallback("⏸️ 暂停", f"pause_{download_id}".encode("utf-8")))
            elif status == "paused":
                row_buttons.append(KeyboardButtonCallback("▶️ 开始", f"resume_{download_id}".encode("utf-8")))
            
            # 队列中和等待中的任务也可以置顶
            if status in ("downloading", "pending", "queued", "paused"):
                row_buttons.append(KeyboardButtonCallback("⭐ 置顶", f"priority_{download_id}".encode("utf-8")))
            
            row_buttons.append(KeyboardButtonCallback("🗑️ 删除", f"delete_{download_id}".encode("utf-8")))
            
            if row_buttons:
                buttons.append(row_buttons)

        text = header_text + "\n**正在进行的任务：**\n" + "\n\n".join(lines)

        await event.reply(text, buttons=buttons, parse_mode='markdown')
        
    async def _handle_bot_message(self, event: events.NewMessage.Event) -> None:
        """处理Bot收到的消息（非命令）"""
        if not event.message:
            return
            
        # 忽略命令消息（已由_handle_bot_command处理）
        if event.message.text and event.message.text.startswith('/'):
            return
        
        # 检查是否是对话过程中的消息
        sender = await event.get_sender()
        if not isinstance(sender, User):
            return
        
        if sender.id in self._conversation_states:
            await self._handle_conversation_message(event)
            return
            
        sender_id = sender.id
        
        # 验证管理员权限
        if self.settings.admin_user_ids and sender_id not in self.settings.admin_user_ids:
            return
            
        # 检查是否是视频或文档
        if event.message.video or event.message.document:
            await self._handle_media_message(event)
            
    async def _handle_media_message(self, event: events.NewMessage.Event) -> None:
        """处理Bot收到的媒体消息"""
        try:
            # 获取文件信息
            file_name = None
            file_size = 0
            media_type = None
            
            if event.message.video:
                media_type = "video"
                file_name = getattr(event.message.video, "file_name", None)
                if event.message.file:
                    file_size = getattr(event.message.file, "size", 0) or 0
            elif event.message.document:
                media_type = "document"
                file_name = getattr(event.message.document, "file_name", None)
                if event.message.file:
                    file_size = getattr(event.message.file, "size", 0) or 0
                    
            if not file_name:
                file_name = f"telegram_{event.message.id}"

            # 从 Telegram 媒体对象中提取文件 ID，用于去重
            tg_file_id = None
            tg_access_hash = None
            media_obj = getattr(event.message, "document", None) or getattr(event.message, "photo", None)
            if media_obj is not None:
                tg_file_id = getattr(media_obj, "id", None)
                tg_access_hash = getattr(media_obj, "access_hash", None)

            # 读取配置判断是否启用机器人重复检测（默认启用）
            config = self.database.get_config()
            bot_dedupe_enabled = config.get("bot_dedupe_enabled", "1") != "0"

            # 如果启用重复检测，且之前已经有相同 Telegram 文件的已完成下载，则不再重复下载
            if bot_dedupe_enabled and tg_file_id is not None and tg_access_hash is not None:
                existing = self.database.find_download_by_telegram_file(tg_file_id, tg_access_hash)
                if existing:
                    existing_id = existing.get("id")
                    existing_path = existing.get("file_path") or "未知路径"
                    text = (
                        "⚠️ 此文件之前已下载过，将不再重复下载。\n\n"
                        f"已有任务ID：`{existing_id}`\n"
                        f"保存路径：`{existing_path}`\n\n"
                        "如需再次下载此文件，可先使用 /dedupe_off 关闭重复检测，再重新发送。"
                    )
                    await event.reply(text, parse_mode='markdown')
                    return

            # 记录管理员发送给 Bot 的消息
            try:
                sender = await event.get_sender()
                sender_id = getattr(sender, "id", 0) if sender else 0
                sender_username = getattr(sender, "username", None) if sender else None
                sender_first_name = getattr(sender, "first_name", None) if sender else None
                sender_last_name = getattr(sender, "last_name", None) if sender else None

                message_text = getattr(event.message, "message", None) or getattr(event.message, "text", None)
                self.database.add_message(
                    message_id=event.message.id,
                    chat_id=event.chat_id or 0,
                    sender_id=sender_id,
                    sender_username=sender_username,
                    sender_first_name=sender_first_name,
                    sender_last_name=sender_last_name,
                    message_text=message_text,
                    has_media=True,
                    media_type=media_type,
                    file_name=file_name,
                )
            except Exception as e:
                logger.debug(f"记录管理员媒体消息失败: {e}")
                
            # 获取下载统计（全局）
            stats = self.database.get_download_stats()
            total = stats.get("total", 0)
            completed = stats.get("completed", 0)
            failed = stats.get("failed", 0)
            
            # 添加下载记录（初始状态为pending），记录文件大小与保存路径，便于前端展示
            download_id = self.database.add_download(
                message_id=event.message.id,
                chat_id=event.chat_id or 0,
                bot_username=self._bot_username or "unknown",
                file_name=file_name,
                origin_file_name=file_name,
                status="pending",
                source="bot",
                tg_file_id=tg_file_id,
                tg_access_hash=tg_access_hash,
                file_size=file_size,
                save_dir=str(self.settings.download_dir),
            )
            
            # 检查全局并发限制
            can_start = True
            if self.queue_manager:
                can_start = await self.queue_manager.try_start_download(download_id)
            else:
                # 没有队列管理器，直接标记为downloading
                self.database.update_download(download_id, status="downloading")
            
            # 发送初始回复（带控制按钮）
            if can_start:
                reply_text = (
                    f"📥 **开始下载**\n\n"
                    f"**文件ID：** `{event.message.id}`\n"
                    f"**任务ID：** `{download_id}`\n"
                    f"**文件名：** {file_name}\n"
                    f"**大小：** {self._format_size(file_size)}\n"
                    f"**类型：** {media_type}\n"
                    f"**速度：** 计算中...\n\n"
                    f"**下载统计：**\n"
                    f"总计：{total + 1} | 成功：{completed} | 失败：{failed}"
                )
            else:
                reply_text = (
                    f"📋 **任务已加入队列**\n\n"
                    f"**文件ID：** `{event.message.id}`\n"
                    f"**任务ID：** `{download_id}`\n"
                    f"**文件名：** {file_name}\n"
                    f"**大小：** {self._format_size(file_size)}\n"
                    f"**类型：** {media_type}\n\n"
                    f"当前有5个任务正在下载，本任务将在队列中等待...\n\n"
                    f"**下载统计：**\n"
                    f"总计：{total + 1} | 成功：{completed} | 失败：{failed}"
                )
            
            buttons = [
                [
                    KeyboardButtonCallback("⏸️ 暂停", f"pause_{download_id}".encode("utf-8")),
                    KeyboardButtonCallback("⭐ 置顶优先", f"priority_{download_id}".encode("utf-8")),
                ],
                [
                    KeyboardButtonCallback("🗑️ 删除", f"delete_{download_id}".encode("utf-8")),
                ],
            ]

            reply_msg = await event.reply(reply_text, parse_mode='markdown', buttons=buttons)
            
            # 如果任务进入队列，直接返回不执行下载
            if not can_start:
                return

            # 记录 Bot 的回复消息
            try:
                if self._bot_id is not None:
                    self.database.add_message(
                        message_id=reply_msg.id,
                        chat_id=reply_msg.chat_id or event.chat_id or 0,
                        sender_id=self._bot_id,
                        sender_username=self._bot_username,
                        sender_first_name=None,
                        sender_last_name=None,
                        message_text=reply_text,
                        has_media=False,
                        media_type=None,
                        file_name=None,
                    )
            except Exception as e:
                logger.debug(f"记录Bot回复消息失败: {e}")
            
            # 使用用户账户客户端下载文件
            # 首先需要通过用户账户客户端获取相同的消息
            async with self._download_semaphore:
                current_task = asyncio.current_task()
                if current_task:
                    self._download_tasks[download_id] = current_task
                self._active_downloads[download_id] = True
                try:
                    target_path = Path(self.settings.download_dir) / file_name
                    
                    # 下载文件并跟踪进度
                    import time
                    downloaded_bytes = 0
                    last_update_time = time.time()
                    last_downloaded = 0
                    download_speed = 0.0
                    start_time = time.time()
                    last_edit_time = 0.0
                    
                    def progress_callback(current: int, total: int) -> None:
                        nonlocal downloaded_bytes, last_update_time, last_downloaded, download_speed, last_edit_time

                        if download_id in self._cancelled_downloads:
                            raise asyncio.CancelledError("下载已被用户暂停")

                        downloaded_bytes = current
                        progress = (current / total * 100) if total > 0 else 0
                        
                        # 计算下载速度
                        current_time = time.time()
                        if last_update_time is not None:
                            time_diff = current_time - last_update_time
                            if time_diff > 0:
                                bytes_diff = current - last_downloaded
                                download_speed = bytes_diff / time_diff
                        
                        last_update_time = current_time
                        last_downloaded = current
                        
                        # 更新数据库
                        self.database.update_download(
                            download_id,
                            progress=progress,
                            download_speed=download_speed,
                        )

                        # 周期性更新 Bot 回复中的进度和速度
                        if current_time - last_edit_time >= 2.0 and self._active_downloads.get(download_id, False):
                            last_edit_time = current_time
                            asyncio.create_task(
                                self._update_progress_message(
                                    event.chat_id or 0,
                                    reply_msg.id,
                                    event.message.id,
                                    download_id,
                                    file_name,
                                    file_size,
                                    media_type,
                                    progress,
                                    download_speed,
                                    total,
                                    completed,
                                    failed,
                                )
                            )
                    
                    # 使用用户账户客户端下载文件
                    # 首先需要通过用户账户客户端获取相同的消息
                    # Bot收到的消息在用户账户中可以通过对话获取
                    try:
                        # 尝试通过用户账户客户端获取消息
                        # 由于Bot收到的消息是私聊，我们需要通过对话获取
                        chat = await self.user_client.get_entity(self._bot_username)
                        user_message = await self.user_client.get_messages(chat, ids=event.message.id)
                        
                        if user_message:
                            # 使用用户账户客户端下载
                            await self.user_client.download_media(
                                user_message,
                                file=target_path,
                                progress_callback=progress_callback
                            )
                        else:
                            # 如果无法通过用户账户获取，使用Bot客户端下载
                            logger.warning("无法通过用户账户获取消息，使用Bot客户端下载")
                            await self._bot_client.download_media(
                                event.message,
                                file=target_path,
                                progress_callback=progress_callback
                            )
                    except Exception as e:
                        logger.warning(f"尝试使用用户账户下载失败，使用Bot客户端: {e}")
                        # 如果无法通过用户账户下载，使用Bot客户端下载
                        await self._bot_client.download_media(
                            event.message,
                            file=target_path,
                            progress_callback=progress_callback
                        )
                    
                    # 下载完成
                    elapsed_time = time.time() - start_time
                    avg_speed = (file_size / elapsed_time) if elapsed_time > 0 else 0
                    
                    self.database.update_download(
                        download_id,
                        file_path=str(target_path),
                        status="completed",
                        progress=100.0,
                        download_speed=avg_speed,
                    )
                    
                    # 通知队列管理器，尝试启动下一个任务
                    if self.queue_manager:
                        await self.queue_manager.on_download_finished(download_id)
                    
                    # 更新回复消息（使用全局统计）
                    stats = self.database.get_download_stats()
                    total = stats.get("total", 0)
                    completed = stats.get("completed", 0)
                    failed = stats.get("failed", 0)
                    
                    success_text = (
                        f"✅ **下载完成**\n\n"
                        f"**文件ID：** `{event.message.id}`\n"
                        f"**任务ID：** `{download_id}`\n"
                        f"**文件名：** {file_name}\n"
                        f"**大小：** {self._format_size(file_size)}\n"
                        f"**平均速度：** {self._format_speed(avg_speed)}\n"
                        f"**耗时：** {elapsed_time:.1f}秒\n\n"
                        f"**下载统计：**\n"
                        f"总计：{total} | 成功：{completed} | 失败：{failed}"
                    )
                    # 下载完成后只保留删除按钮
                    finished_buttons = [
                        [
                            KeyboardButtonCallback("🗑️ 删除", f"delete_{download_id}".encode("utf-8")),
                        ]
                    ]

                    await self._bot_client.edit_message(
                        event.chat_id,
                        reply_msg.id,
                        success_text,
                        parse_mode='markdown',
                        buttons=finished_buttons,
                    )
                    self._active_downloads[download_id] = False
                    self._download_tasks.pop(download_id, None)
                    self._cancelled_downloads.discard(download_id)
                    
                except asyncio.CancelledError:
                    self._active_downloads[download_id] = False
                    self._download_tasks.pop(download_id, None)
                    self._cancelled_downloads.discard(download_id)
                    raise
                except Exception as e:
                    logger.exception(f"下载文件失败: {e}")
                    self.database.update_download(
                        download_id,
                        status="failed",
                        error=str(e),
                    )
                    
                    # 通知队列管理器，尝试启动下一个任务
                    if self.queue_manager:
                        await self.queue_manager.on_download_finished(download_id)
                    
                    stats = self.database.get_download_stats()
                    total = stats.get("total", 0)
                    completed = stats.get("completed", 0)
                    failed = stats.get("failed", 0)
                    
                    error_text = (
                        f"❌ **下载失败**\n\n"
                        f"**文件ID：** `{event.message.id}`\n"
                        f"**文件名：** {file_name}\n"
                        f"**错误：** {str(e)}\n\n"
                        f"**下载统计：**\n"
                        f"总计：{total} | 成功：{completed} | 失败：{failed}"
                    )
                    # 失败后同样保留删除按钮
                    failed_buttons = [
                        [
                            KeyboardButtonCallback("🗑️ 删除", f"delete_{download_id}".encode("utf-8")),
                        ]
                    ]

                    await self._bot_client.edit_message(
                        event.chat_id,
                        reply_msg.id,
                        error_text,
                        parse_mode='markdown',
                        buttons=failed_buttons,
                    )
                    self._active_downloads[download_id] = False
                    self._download_tasks.pop(download_id, None)
                    self._cancelled_downloads.discard(download_id)
                
        except Exception as e:
            logger.exception(f"处理媒体消息失败: {e}")
            
    async def restore_queued_download(self, download: dict) -> None:
        """从全局队列恢复 Bot 触发的下载任务。

        说明：
        - DownloadQueueManager 会先把任务状态置为 downloading，然后调用本方法；
        - 这里重新拉取原始消息并开始实际下载流程；
        - 为了简化实现，进度更新只做数据库更新，以及在开始/完成/失败时编辑一条 Bot 消息。
        """
        try:
            if not self._bot_client:
                logger.warning("Bot 客户端尚未就绪，无法恢复队列中的下载任务")
                return

            download_id = download.get("id")
            message_id = download.get("message_id")
            chat_id = download.get("chat_id")
            if not download_id or not message_id or not chat_id:
                logger.warning("恢复队列任务字段缺失: id=%s message_id=%s chat_id=%s", download_id, message_id, chat_id)
                return

            file_name = download.get("file_name") or f"telegram_{message_id}"
            file_size = int(download.get("file_size") or 0)
            media_type = "unknown"

            # 获取原始消息
            chat = await self._bot_client.get_entity(chat_id)
            msg = await self._bot_client.get_messages(chat, ids=message_id)
            if not msg:
                logger.warning("无法恢复队列任务 %s：找不到原始消息 %s", download_id, message_id)
                self.database.update_download(download_id, status="failed", error="找不到原始消息")
                return

            if msg.video:
                media_type = "video"
            elif msg.document:
                media_type = "document"

            # 计算保存路径
            from pathlib import Path as _Path

            save_dir = download.get("save_dir") or str(self.settings.download_dir)
            save_path = _Path(save_dir)
            if not save_path.is_absolute():
                save_path = _Path("/") / save_path
            target_path = save_path / file_name
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # 如果数据库中尚未记录 save_dir，则补写一份
            if not download.get("save_dir"):
                self.database.update_download(download_id, save_dir=str(save_path))

            # 统计信息用于展示
            stats = self.database.get_download_stats()
            total = stats.get("total", 0)
            completed = stats.get("completed", 0)
            failed = stats.get("failed", 0)

            # 找到需要更新的 Bot 消息（队列时的那条），找不到就新发一条
            reply_message_id = download.get("reply_message_id")
            reply_chat_id = download.get("reply_chat_id") or chat_id
            reply_msg = None
            try:
                if reply_message_id:
                    reply_msg = await self._bot_client.get_messages(reply_chat_id, ids=reply_message_id)
            except Exception:
                reply_msg = None

            start_text = (
                f"📥 **开始下载**\n\n"
                f"**文件ID：** `{message_id}`\n"
                f"**任务ID：** `{download_id}`\n"
                f"**文件名：** {file_name}\n"
                f"**大小：** {self._format_size(file_size)}\n"
                f"**类型：** {media_type}\n"
                f"**速度：** 计算中...\n\n"
                f"**下载统计：**\n"
                f"总计：{total} | 成功：{completed} | 失败：{failed}"
            )

            buttons = [
                [
                    KeyboardButtonCallback("⏸️ 暂停", f"pause_{download_id}".encode("utf-8")),
                    KeyboardButtonCallback("⭐ 置顶优先", f"priority_{download_id}".encode("utf-8")),
                ],
                [
                    KeyboardButtonCallback("🗑️ 删除", f"delete_{download_id}".encode("utf-8")),
                ],
            ]

            if reply_msg:
                try:
                    await self._bot_client.edit_message(
                        reply_chat_id,
                        reply_message_id,
                        start_text,
                        parse_mode="markdown",
                        buttons=buttons,
                    )
                except Exception as e:  # pragma: no cover - 防御性
                    logger.debug("编辑队列提示消息失败，将发送新消息: %s", e)
                    reply_msg = None

            if not reply_msg:
                reply_msg = await self._bot_client.send_message(
                    reply_chat_id,
                    start_text,
                    buttons=buttons,
                    parse_mode="markdown",
                )
                # 回写回复消息ID到数据库，便于后续再次恢复
                try:
                    self.database.update_download(
                        download_id,
                        reply_message_id=reply_msg.id,
                        reply_chat_id=reply_msg.chat_id or reply_chat_id,
                    )
                except Exception as e:
                    logger.debug("更新下载记录的回复消息ID失败: %s", e)

            # 开始实际下载
            import time

            start_time = time.time()
            downloaded_bytes = 0
            last_update_time = time.time()
            last_downloaded = 0
            download_speed = 0.0

            async with self._download_semaphore:
                current_task = asyncio.current_task()
                if current_task:
                    self._download_tasks[download_id] = current_task
                self._active_downloads[download_id] = True
                try:
                    def progress_callback(current: int, total: int) -> None:
                        nonlocal downloaded_bytes, last_update_time, last_downloaded, download_speed

                        if download_id in self._cancelled_downloads:
                            raise asyncio.CancelledError("下载已被用户暂停")

                        downloaded_bytes = current
                        progress = (current / total * 100) if total > 0 else 0

                        now = time.time()
                        if last_update_time is not None:
                            dt = now - last_update_time
                            if dt > 0:
                                bytes_diff = current - last_downloaded
                                download_speed = bytes_diff / dt
                        last_update_time = now
                        last_downloaded = current

                        # 只更新数据库，不频繁编辑消息，以减轻负载
                        self.database.update_download(
                            download_id,
                            progress=progress,
                            download_speed=download_speed,
                        )

                    await self._bot_client.download_media(
                        msg,
                        file=target_path,
                        progress_callback=progress_callback if file_size > 0 else None,
                    )

                    elapsed = time.time() - start_time
                    avg_speed = (file_size / elapsed) if elapsed > 0 else 0.0

                    self.database.update_download(
                        download_id,
                        file_path=str(target_path),
                        status="completed",
                        progress=100.0,
                        download_speed=avg_speed,
                    )

                    # 通知队列管理器，启动下一个任务
                    if self.queue_manager:
                        await self.queue_manager.on_download_finished(download_id)

                    success_text = (
                        f"✅ **下载完成**\n\n"
                        f"**文件ID：** `{message_id}`\n"
                        f"**任务ID：** `{download_id}`\n"
                        f"**文件名：** {file_name}\n"
                        f"**大小：** {self._format_size(file_size)}\n"
                        f"**平均速度：** {self._format_speed(avg_speed)}\n"
                        f"**耗时：** {elapsed:.1f}秒\n\n"
                        f"**下载统计：**\n"
                        f"总计：{total} | 成功：{completed} | 失败：{failed}"
                    )
                    finished_buttons = [
                        [KeyboardButtonCallback("🗑️ 删除", f"delete_{download_id}".encode("utf-8"))]
                    ]

                    await self._bot_client.edit_message(
                        reply_chat_id,
                        reply_msg.id,
                        success_text,
                        parse_mode="markdown",
                        buttons=finished_buttons,
                    )

                    self._active_downloads[download_id] = False
                    self._download_tasks.pop(download_id, None)
                    self._cancelled_downloads.discard(download_id)

                except asyncio.CancelledError:
                    self._active_downloads[download_id] = False
                    self._download_tasks.pop(download_id, None)
                    self._cancelled_downloads.discard(download_id)
                    raise
                except Exception as e:
                    logger.exception("恢复队列中的 Bot 下载任务失败: %s", e)
                    self.database.update_download(
                        download_id,
                        status="failed",
                        error=str(e),
                    )
                    if self.queue_manager:
                        await self.queue_manager.on_download_finished(download_id)

                    error_text = (
                        f"❌ **下载失败**\n\n"
                        f"**文件ID：** `{message_id}`\n"
                        f"**文件名：** {file_name}\n"
                        f"**错误：** {str(e)}\n\n"
                        f"**下载统计：**\n"
                        f"总计：{total} | 成功：{completed} | 失败：{failed}"
                    )
                    failed_buttons = [
                        [KeyboardButtonCallback("🗑️ 删除", f"delete_{download_id}".encode("utf-8"))]
                    ]
                    try:
                        await self._bot_client.edit_message(
                            reply_chat_id,
                            reply_msg.id,
                            error_text,
                            parse_mode="markdown",
                            buttons=failed_buttons,
                        )
                    except Exception:
                        pass
                    self._active_downloads[download_id] = False
                    self._download_tasks.pop(download_id, None)
                    self._cancelled_downloads.discard(download_id)

        except Exception as e:
            logger.exception("restore_queued_download 执行出错: %s", e)

    def _format_size(self, size: int) -> str:
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"
        
    def _format_speed(self, speed: float) -> str:
        """格式化下载速度"""
        return f"{self._format_size(int(speed))}/s"
    
    async def _update_progress_message(
        self,
        chat_id,
        reply_message_id,
        message_id,
        download_id,
        file_name,
        file_size,
        media_type,
        progress,
        speed,
        total,
        completed,
        failed,
    ) -> None:
        if not self._bot_client:
            return
        if not self._active_downloads.get(download_id, False):
            return
        try:
            speed_text = self._format_speed(speed) if speed > 0 else "计算中..."
            progress_text = f"{progress:.1f}%"
            text = (
                f"📥 **正在下载**\n\n"
                f"**文件ID：** `{message_id}`\n"
                f"**任务ID：** `{download_id}`\n"
                f"**文件名：** {file_name}\n"
                f"**大小：** {self._format_size(file_size)}\n"
                f"**类型：** {media_type}\n"
                f"**进度：** {progress_text}\n"
                f"**速度：** {speed_text}"
            )

            # 在进度更新时始终保留控制按钮
            buttons = [
                [
                    KeyboardButtonCallback("⏸️ 暂停", f"pause_{download_id}".encode("utf-8")),
                    KeyboardButtonCallback("⭐ 置顶优先", f"priority_{download_id}".encode("utf-8")),
                ],
                [
                    KeyboardButtonCallback("🗑️ 删除", f"delete_{download_id}".encode("utf-8")),
                ],
            ]
            await self._bot_client.edit_message(
                chat_id,
                reply_message_id,
                text,
                parse_mode='markdown',
                buttons=buttons,
            )
        except Exception as e:
            logger.debug(f"更新下载进度消息失败: {e}")
        
    async def _handle_callback_query(self, event: events.CallbackQuery.Event) -> None:
        """处理内联键盘按钮回调"""
        try:
            data = event.data.decode('utf-8')
            logger.info(f"收到回调查询: {data}")
            
            # 验证管理员权限
            sender = await event.get_sender()
            if not sender or sender.id not in (self.settings.admin_user_ids or []):
                await event.answer("❌ 您没有权限执行此操作", alert=True)
                return
            
            user_id = sender.id
            
            # 解析回调数据
            if data.startswith("group_"):
                # 群聊选择回调
                group_id = int(data.split("_")[1])
                await self._handle_group_callback(event, user_id, group_id)
            elif data.startswith("page_"):
                # 分页回调
                page = int(data.split("_")[1])
                await self._handle_page_callback(event, user_id, page)
            elif data == "mode_monitor" or data == "mode_history":
                # 模式选择回调
                mode = "monitor" if data == "mode_monitor" else "history"
                await self._handle_mode_callback(event, user_id, mode)
            elif data.startswith("pause_"):
                download_id = int(data.split("_")[1])
                await self._handle_pause_download(event, download_id)
            elif data.startswith("priority_"):
                download_id = int(data.split("_")[1])
                await self._handle_priority_download(event, download_id)
            elif data.startswith("delete_"):
                download_id = int(data.split("_")[1])
                await self._handle_delete_download(event, download_id)
            elif data.startswith("resume_"):
                download_id = int(data.split("_")[1])
                await self._handle_resume_download(event, download_id)
            elif data.startswith("retry_"):
                download_id = int(data.split("_")[1])
                await self._handle_retry_download(event, download_id)
            else:
                await event.answer("❓ 未知操作", alert=True)
                
        except Exception as e:
            logger.exception(f"处理回调查询失败: {e}")
            await event.answer(f"❌ 操作失败: {str(e)}", alert=True)

    async def pause_download(self, download_id: int) -> bool:
        downloads = self.database.list_downloads(limit=1000)
        download = next((d for d in downloads if d.get("id") == download_id), None)
        if not download:
            return False
        if download.get("status") != "downloading":
            return False

        self._cancelled_downloads.add(download_id)

        task = self._download_tasks.get(download_id)
        if task and not task.done():
            task.cancel()

        self._active_downloads[download_id] = False
        
        # 通知队列管理器，尝试启动下一个任务
        if self.queue_manager:
            await self.queue_manager.on_download_finished(download_id)
        
        return True
    
    async def _handle_pause_download(self, event: events.CallbackQuery.Event, download_id: int) -> None:
        """处理暂停下载"""
        try:
            # 获取下载记录
            downloads = self.database.list_downloads(limit=1000)
            download = next((d for d in downloads if d.get('id') == download_id), None)
            
            if not download:
                await event.answer("❌ 下载记录不存在", alert=True)
                return
            
            current_status = download.get('status')
            source = download.get('source') or 'bot'
            
            # 如果正在下载，取消并标记为暂停
            if current_status == 'downloading':
                success = False
                if source == 'rule' and self.worker:
                    success = await self.worker.cancel_download(download_id)
                else:
                    success = await self.pause_download(download_id)

                if success:
                    self.database.update_download(download_id, status="paused", error="用户暂停")
                    await event.answer("⏸️ 已暂停下载")

                    # 更新消息
                    await event.edit(
                        f"⏸️ **已暂停**\n\n"
                        f"文件: {download.get('file_name', '未知')}\n"
                        f"状态: 已暂停\n\n"
                        f"使用 /download 命令查看所有下载"
                    )
                else:
                    await event.answer("❌ 暂停失败", alert=True)
            elif current_status == 'paused':
                await event.answer("ℹ️ 下载已经是暂停状态", alert=True)
            else:
                await event.answer(f"ℹ️ 当前状态 ({current_status}) 无法暂停", alert=True)
                
        except Exception as e:
            logger.exception(f"暂停下载失败: {e}")
            await event.answer(f"❌ 暂停失败: {str(e)}", alert=True)
    
    async def _handle_priority_download(self, event: events.CallbackQuery.Event, download_id: int) -> None:
        """处理置顶优先"""
        try:
            # 获取下载记录
            downloads = self.database.list_downloads(limit=1000)
            download = next((d for d in downloads if d.get('id') == download_id), None)
            
            if not download:
                await event.answer("❌ 下载记录不存在", alert=True)
                return
            
            # 更新优先级（设置为高优先级）
            current_priority = download.get('priority', 0)
            new_priority = 10 if current_priority < 10 else 0
            
            self.database.update_download(download_id, priority=new_priority)

            # 如果设置为高优先级，则抢占最早开始的其他下载任务
            if new_priority > 0:
                other_candidates = [
                    d for d in downloads
                    if d.get('status') == 'downloading' and d.get('id') != download_id
                ]
                if other_candidates:
                    other_candidates.sort(key=lambda d: d.get('created_at') or "")
                    victim = other_candidates[0]
                    victim_id = victim.get('id')
                    victim_source = victim.get('source') or 'bot'

                    if victim_id is not None:
                        if victim_source == 'rule' and self.worker:
                            await self.worker.cancel_download(int(victim_id))
                        else:
                            await self.pause_download(int(victim_id))

                        self.database.update_download(
                            int(victim_id),
                            status="paused",
                            error="被高优先级任务抢占",
                        )

                await event.answer("⭐ 已设置为高优先级")
                await event.edit(
                    f"⭐ **高优先级**\n\n"
                    f"文件: {download.get('file_name', '未知')}\n"
                    f"状态: {download.get('status', '未知')}\n"
                    f"优先级: 高\n\n"
                    f"此任务将优先处理"
                )
            else:
                await event.answer("📋 已恢复正常优先级")
                
        except Exception as e:
            logger.exception(f"设置优先级失败: {e}")
            await event.answer(f"❌ 设置失败: {str(e)}", alert=True)
    
    async def _handle_delete_download(self, event: events.CallbackQuery.Event, download_id: int) -> None:
        """处理删除下载"""
        try:
            # 获取下载记录
            downloads = self.database.list_downloads(limit=1000)
            download = next((d for d in downloads if d.get('id') == download_id), None)
            
            if not download:
                await event.answer("❌ 下载记录不存在", alert=True)
                return
            
            # 如果正在下载，先取消任务
            if download.get('status') == 'downloading' and self.worker:
                logger.info(f"取消正在进行的下载任务: {download_id}")
                await self.worker.cancel_download(download_id)
                await asyncio.sleep(0.5)  # 等待取消完成
            
            # 删除文件（如果存在）
            file_path = download.get('file_path')
            if file_path and Path(file_path).exists():
                try:
                    Path(file_path).unlink()
                    logger.info(f"已删除文件: {file_path}")
                except Exception as e:
                    logger.warning(f"删除文件失败: {e}")
            
            # 删除数据库记录
            self.database.delete_download(download_id)
            await event.answer("✅ 已删除下载任务")
            
            # 更新消息
            await event.edit("🗑️ **已删除**\n\n此下载任务已被删除。")
            
            # 通知队列管理器，尝试启动下一个任务
            if self.queue_manager:
                await self.queue_manager.on_download_finished(download_id)
            
        except Exception as e:
            logger.exception(f"删除下载失败: {e}")
            await event.answer(f"❌ 删除失败: {str(e)}", alert=True)
    
    async def _handle_resume_download(self, event: events.CallbackQuery.Event, download_id: int) -> None:
        """处理恢复下载"""
        try:
            # 获取下载记录
            downloads = self.database.list_downloads(limit=1000)
            download = next((d for d in downloads if d.get('id') == download_id), None)
            
            if not download:
                await event.answer("❌ 下载记录不存在", alert=True)
                return
            
            current_status = download.get('status')
            
            if current_status != 'paused':
                await event.answer(f"ℹ️ 当前状态 ({current_status}) 无法恢复", alert=True)
                return
            
            # 检查全局并发限制
            can_start = True
            if self.queue_manager:
                can_start = await self.queue_manager.try_start_download(download_id)
            
            if can_start:
                await event.answer("✅ 已恢复下载")
                await event.edit(
                    f"▶️ **已恢复下载**\n\n"
                    f"文件: {download.get('file_name', '未知')}\n"
                    f"状态: 下载中\n\n"
                    f"使用 /download 命令查看所有下载"
                )
            else:
                await event.answer("📋 任务已加入队列，等待其他任务完成")
                await event.edit(
                    f"📋 **任务已加入队列**\n\n"
                    f"文件: {download.get('file_name', '未知')}\n"
                    f"状态: 队列中\n\n"
                    f"当前有5个任务正在下载，本任务将在队列中等待..."
                )
                
        except Exception as e:
            logger.exception(f"恢复下载失败: {e}")
            await event.answer(f"❌ 恢复失败: {str(e)}", alert=True)
    
    async def _handle_retry_download(self, event: events.CallbackQuery.Event, download_id: int) -> None:
        """处理重试下载"""
        # TODO: 实现重试功能
        await event.answer("🔄 重试功能开发中...", alert=True)
    
    async def _handle_createrule_command(self, event: events.NewMessage.Event) -> None:
        """处理/createrule命令 - 交互式创建群聊下载规则"""
        sender = await event.get_sender()
        user_id = sender.id
        
        try:
            # 获取用户的所有对话（群聊）
            from telethon.tl.types import Channel, Chat
            dialogs = await self.user_client.get_dialogs()
            
            # 过滤出群聊和频道
            groups = []
            for dialog in dialogs:
                entity = dialog.entity
                if isinstance(entity, (Channel, Chat)):
                    groups.append({
                        'id': entity.id,
                        'title': getattr(entity, 'title', 'Unknown'),
                        'type': 'channel' if isinstance(entity, Channel) else 'group'
                    })
            
            if not groups:
                await event.reply("❌ 未找到任何群聊或频道。请先加入一些群聊。")
                return
            
            # 初始化对话状态
            self._conversation_states[user_id] = {
                'step': 'select_group',
                'groups': groups,
                'page': 0,
                'rule_data': {}
            }
            
            # 使用内联键盘显示群聊列表（分页）
            await self._send_group_selection_page(event, user_id, 0)
            
        except Exception as e:
            logger.exception(f"处理创建规则命令失败: {e}")
            await event.reply(f"❌ 获取群聊列表失败: {str(e)}")
    
    async def _handle_cancel_command(self, event: events.NewMessage.Event) -> None:
        """处理/cancel命令 - 取消当前操作"""
        sender = await event.get_sender()
        user_id = sender.id
        
        if user_id in self._conversation_states:
            del self._conversation_states[user_id]
            await event.reply("✅ 已取消当前操作")
        else:
            await event.reply("ℹ️ 当前没有进行中的操作")
    
    async def _handle_conversation_message(self, event: events.NewMessage.Event) -> None:
        """处理对话过程中的消息"""
        sender = await event.get_sender()
        user_id = sender.id
        
        if user_id not in self._conversation_states:
            return
        
        state = self._conversation_states[user_id]
        step = state.get('step')
        message_text = event.message.text.strip()
        
        try:
            if step == 'select_group':
                await self._handle_group_selection(event, user_id, message_text, state)
            elif step == 'select_mode':
                await self._handle_mode_selection(event, user_id, message_text, state)
            elif step == 'select_extensions':
                await self._handle_extensions_selection(event, user_id, message_text, state)
            elif step == 'enter_min_size':
                await self._handle_min_size_input(event, user_id, message_text, state)
            elif step == 'enter_keywords':
                await self._handle_keywords_input(event, user_id, message_text, state)
            elif step == 'confirm':
                await self._handle_confirmation(event, user_id, message_text, state)
                
        except Exception as e:
            logger.exception(f"处理对话消息失败: {e}")
            await event.reply(f"❌ 处理失败: {str(e)}\n使用 /cancel 取消操作")
    
    async def _send_group_selection_page(self, event, user_id, page):
        """发送群聊选择页面（带分页）"""
        from telethon.tl.types import KeyboardButtonCallback
        from telethon.types import ReplyKeyboardMarkup
        
        state = self._conversation_states.get(user_id)
        if not state:
            return
        
        groups = state['groups']
        page_size = 10
        total_pages = (len(groups) + page_size - 1) // page_size
        
        # 确保页码有效
        page = max(0, min(page, total_pages - 1))
        state['page'] = page
        
        # 获取当前页的群聊
        start_idx = page * page_size
        end_idx = min(start_idx + page_size, len(groups))
        page_groups = groups[start_idx:end_idx]
        
        # 构建内联键盘
        buttons = []
        for group in page_groups:
            # 截断过长的标题
            title = group['title'][:30] + '...' if len(group['title']) > 30 else group['title']
            button_data = f"group_{group['id']}".encode('utf-8')
            buttons.append([KeyboardButtonCallback(title, button_data)])
        
        # 添加分页按钮
        nav_buttons = []
        if page > 0:
            nav_buttons.append(KeyboardButtonCallback("⬅️ 上一页", f"page_{page-1}".encode('utf-8')))
        if page < total_pages - 1:
            nav_buttons.append(KeyboardButtonCallback("下一页 ➡️", f"page_{page+1}".encode('utf-8')))
        
        if nav_buttons:
            buttons.append(nav_buttons)
        
        # 发送或编辑消息
        text = (
            f"📋 **请选择要监控的群聊**\n\n"
            f"第 {page + 1}/{total_pages} 页 (共 {len(groups)} 个群聊)\n\n"
            f"💡 点击下方按钮选择群聊\n"
            f"使用 /cancel 取消操作"
        )
        if isinstance(event, events.CallbackQuery.Event):
            # 这是回调查询，编辑现有消息
            await event.edit(text, buttons=buttons, parse_mode='markdown')
        else:
            # 这是新消息，发送新的消息
            await event.reply(text, buttons=buttons, parse_mode='markdown')
    
    async def _handle_group_callback(self, event, user_id, group_id):
        """处理群聊选择回调"""
        state = self._conversation_states.get(user_id)
        if not state or state.get('step') != 'select_group':
            await event.answer("❌ 会话已过期，请重新使用 /createrule", alert=True)
            return
        
        groups = state['groups']
        selected_group = next((g for g in groups if g['id'] == group_id), None)
        
        if not selected_group:
            await event.answer("❌ 群聊不存在", alert=True)
            return
        
        state['rule_data']['chat_id'] = selected_group['id']
        state['rule_data']['chat_title'] = selected_group['title']
        state['step'] = 'select_mode'
        
        # 使用内联键盘选择模式
        from telethon.tl.types import KeyboardButtonCallback
        
        buttons = [
            [KeyboardButtonCallback("📡 监控模式 - 自动下载新消息", b"mode_monitor")],
            [KeyboardButtonCallback("📚 历史模式 - 下载历史消息", b"mode_history")]
        ]
        
        mode_text = (
            f"✅ 已选择群聊: **{selected_group['title']}**\n\n"
            "📝 **请选择规则模式**\n\n"
            "💡 点击下方按钮选择模式"
        )
        
        await event.edit(mode_text, buttons=buttons, parse_mode='markdown')
        await event.answer()
    
    async def _handle_page_callback(self, event, user_id, page):
        """处理分页回调"""
        state = self._conversation_states.get(user_id)
        if not state or state.get('step') != 'select_group':
            await event.answer("❌ 会话已过期，请重新使用 /createrule", alert=True)
            return
        
        await self._send_group_selection_page(event, user_id, page)
        await event.answer()
    
    async def _handle_group_selection(self, event, user_id, message_text, state):
        """处理群聊选择（文本输入方式，作为备用）"""
        groups = state['groups']
        selected_group = None
        
        # 尝试解析为数字
        try:
            num = int(message_text)
            # 尝试作为chat_id
            selected_group = next((g for g in groups if g['id'] == num), None)
        except ValueError:
            pass
        
        if not selected_group:
            await event.reply("❌ 无效的选择，请使用上方按钮选择群聊")
            return
        
        state['rule_data']['chat_id'] = selected_group['id']
        state['rule_data']['chat_title'] = selected_group['title']
        state['step'] = 'select_mode'
        
        mode_text = (
            f"✅ 已选择群聊: **{selected_group['title']}**\n\n"
            "📝 **请选择规则模式**\n\n"
            "1️⃣ **监控模式** - 自动下载新消息中的文件\n"
            "2️⃣ **历史模式** - 下载群聊历史消息中的文件\n\n"
            "💡 请回复 1 或 2"
        )
        await event.reply(mode_text, parse_mode='markdown')
    
    async def _handle_mode_callback(self, event, user_id, mode):
        """处理模式选择回调"""
        state = self._conversation_states.get(user_id)
        if not state or state.get('step') != 'select_mode':
            await event.answer("❌ 会话已过期，请重新使用 /createrule", alert=True)
            return
        
        state['rule_data']['mode'] = mode
        mode_name = '监控模式' if mode == 'monitor' else '历史模式'
        state['step'] = 'select_extensions'
        
        ext_text = (
            f"✅ 已选择: **{mode_name}**\n\n"
            "📁 **请选择文件类型**\n\n"
            "可选项（多选，用逗号分隔）：\n"
            "• mp4, mkv, avi (视频)\n"
            "• jpg, png, gif (图片)\n"
            "• mp3, flac (音频)\n"
            "• pdf, zip (文档)\n\n"
            "💡 示例: mp4,mkv,jpg\n"
            "或回复 all 下载所有类型"
        )
        
        await event.edit(ext_text, buttons=None, parse_mode='markdown')
        await event.answer()
    
    async def _handle_mode_selection(self, event, user_id, message_text, state):
        """处理模式选择（文本输入方式，作为备用）"""
        if message_text == '1':
            state['rule_data']['mode'] = 'monitor'
            mode_name = '监控模式'
        elif message_text == '2':
            state['rule_data']['mode'] = 'history'
            mode_name = '历史模式'
        else:
            await event.reply("❌ 无效的选择，请使用上方按钮或回复 1 或 2")
            return
        
        state['step'] = 'select_extensions'
        
        ext_text = (
            f"✅ 已选择: **{mode_name}**\n\n"
            "📁 **请选择文件类型**\n\n"
            "可选项（多选，用逗号分隔）：\n"
            "• mp4, mkv, avi (视频)\n"
            "• jpg, png, gif (图片)\n"
            "• mp3, flac (音频)\n"
            "• pdf, zip (文档)\n\n"
            "💡 示例: mp4,mkv,jpg\n"
            "或回复 all 下载所有类型"
        )
        await event.reply(ext_text, parse_mode='markdown')
    
    async def _handle_extensions_selection(self, event, user_id, message_text, state):
        """处理文件类型选择"""
        if message_text.lower() == 'all':
            extensions = ''
        else:
            extensions = message_text.lower().replace(' ', '')
        
        state['rule_data']['extensions'] = extensions
        state['step'] = 'enter_min_size'
        
        size_text = (
            f"✅ 文件类型: **{extensions if extensions else '所有类型'}**\n\n"
            "📏 **请输入文件体积范围（MB）**\n\n"
            "💡 **格式说明：**\n"
            "• `0` - 不限制大小\n"
            "• `10` - 大于等于 10MB\n"
            "• `10-100` - 10MB 到 100MB 之间\n"
            "• `0-100` - 小于等于 100MB\n\n"
            "**示例：** 10-500"
        )
        await event.reply(size_text, parse_mode='markdown')
    
    async def _handle_min_size_input(self, event, user_id, message_text, state):
        """处理文件体积范围输入"""
        size_range = message_text.strip()
        
        # 验证格式
        min_bytes, max_bytes = Database.parse_size_range(size_range)
        
        state['rule_data']['size_range'] = size_range
        state['rule_data']['min_size_bytes'] = min_bytes
        state['rule_data']['max_size_bytes'] = max_bytes
        state['step'] = 'enter_keywords'
        
        # 显示解析结果
        if min_bytes == 0 and max_bytes == 0:
            size_desc = "不限制"
        elif min_bytes > 0 and max_bytes > 0:
            size_desc = f"{min_bytes / (1024 * 1024):.1f} MB ~ {max_bytes / (1024 * 1024):.1f} MB"
        elif min_bytes > 0:
            size_desc = f">= {min_bytes / (1024 * 1024):.1f} MB"
        else:
            size_desc = f"<= {max_bytes / (1024 * 1024):.1f} MB"
        
        keywords_text = (
            f"✅ 体积范围: **{size_desc}**\n\n"
            "🔍 **请输入关键词过滤**\n\n"
            "• 包含关键词: 用 + 开头，例如: +电影\n"
            "• 排除关键词: 用 - 开头，例如: -广告\n"
            "• 多个关键词用逗号分隔\n\n"
            "💡 示例: +电影,+4K,-广告\n"
            "或回复 skip 跳过关键词过滤"
        )
        await event.reply(keywords_text, parse_mode='markdown')
    
    async def _handle_keywords_input(self, event, user_id, message_text, state):
        """处理关键词输入"""
        if message_text.lower() == 'skip':
            include_keywords = ''
            exclude_keywords = ''
        else:
            keywords = [k.strip() for k in message_text.split(',')]
            include_keywords = ','.join([k[1:] for k in keywords if k.startswith('+')])
            exclude_keywords = ','.join([k[1:] for k in keywords if k.startswith('-')])
        
        state['rule_data']['include_keywords'] = include_keywords
        state['rule_data']['exclude_keywords'] = exclude_keywords
        state['step'] = 'confirm'
        
        # 显示确认信息
        rule_data = state['rule_data']
        
        # 格式化体积范围显示
        min_bytes = rule_data.get('min_size_bytes', 0)
        max_bytes = rule_data.get('max_size_bytes', 0)
        if min_bytes == 0 and max_bytes == 0:
            size_desc = "不限制"
        elif min_bytes > 0 and max_bytes > 0:
            size_desc = f"{min_bytes / (1024 * 1024):.1f} MB ~ {max_bytes / (1024 * 1024):.1f} MB"
        elif min_bytes > 0:
            size_desc = f">= {min_bytes / (1024 * 1024):.1f} MB"
        else:
            size_desc = f"<= {max_bytes / (1024 * 1024):.1f} MB"
        
        confirm_text = (
            "📋 **规则配置预览**\n\n"
            f"**群聊**: {rule_data['chat_title']}\n"
            f"**模式**: {'监控模式' if rule_data['mode'] == 'monitor' else '历史模式'}\n"
            f"**文件类型**: {rule_data['extensions'] if rule_data['extensions'] else '所有类型'}\n"
            f"**体积范围**: {size_desc}\n"
            f"**包含关键词**: {include_keywords if include_keywords else '无'}\n"
            f"**排除关键词**: {exclude_keywords if exclude_keywords else '无'}\n\n"
            "✅ 回复 yes 确认创建\n"
            "❌ 回复 no 取消"
        )
        await event.reply(confirm_text, parse_mode='markdown')
    
    async def _handle_confirmation(self, event, user_id, message_text, state):
        """处理确认"""
        if message_text.lower() not in ['yes', 'y', '是', '确认']:
            await event.reply("❌ 已取消创建规则")
            del self._conversation_states[user_id]
            return
        
        # 创建规则
        rule_data = state['rule_data']
        try:
            rule_id = self.database.add_group_rule(
                chat_id=rule_data['chat_id'],
                chat_title=rule_data['chat_title'],
                mode=rule_data['mode'],
                include_extensions=rule_data['extensions'],
                min_size_bytes=rule_data.get('min_size_bytes', 0),
                max_size_bytes=rule_data.get('max_size_bytes', 0),
                size_range=rule_data.get('size_range', '0'),
                include_keywords=rule_data['include_keywords'],
                exclude_keywords=rule_data['exclude_keywords'],
                enabled=True
            )
            
            success_text = (
                f"✅ **规则创建成功！**\n\n"
                f"规则ID: {rule_id}\n"
                f"群聊: {rule_data['chat_title']}\n"
                f"模式: {'监控模式' if rule_data['mode'] == 'monitor' else '历史模式'}\n\n"
                f"规则已启用，开始{'监控新消息' if rule_data['mode'] == 'monitor' else '准备下载历史消息'}！"
            )
            await event.reply(success_text, parse_mode='markdown')
            
            # 清除对话状态
            del self._conversation_states[user_id]
            
        except Exception as e:
            logger.exception(f"创建规则失败: {e}")
            await event.reply(f"❌ 创建规则失败: {str(e)}")
    
    async def _run_bot(self) -> None:
        """在后台运行Bot客户端"""
        try:
            await self._bot_client.run_until_disconnected()
        except Exception as e:
            logger.exception(f"Bot客户端运行出错: {e}")
            
    async def stop(self) -> None:
        """停止Bot命令处理器"""
        if self._bot_client:
            try:
                await self._bot_client.disconnect()
                logger.info("Bot命令处理器已停止")
            except Exception as e:
                logger.warning(f"停止Bot命令处理器时出错: {e}")

