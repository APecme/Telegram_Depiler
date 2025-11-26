from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional
from telethon import TelegramClient, events
from telethon.tl.types import User

from .config import Settings
from .database import Database

logger = logging.getLogger(__name__)


class BotCommandHandler:
    """处理Bot命令的独立处理器"""
    
    def __init__(self, settings: Settings, database: Database, user_client: TelegramClient, worker=None):
        self.settings = settings
        self.database = database
        self.user_client = user_client  # 用户账户客户端，用于下载文件
        self.worker = worker  # TelegramWorker实例，用于取消下载
        self._bot_client: Optional[TelegramClient] = None
        self._bot_username: Optional[str] = None
        self._bot_id: Optional[int] = None
        self._download_semaphore = asyncio.Semaphore(5)
        self._active_downloads: dict[int, bool] = {}
        self._conversation_states: dict[int, dict] = {}  # 用户对话状态
        
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
            startup_message = (
                "🚀 **Telegram下载管理器已启动**\n\n"
                "✅ Bot已就绪，正在监听消息\n\n"
                "📖 **可用命令：**\n"
                "/help - 显示帮助信息\n"
                "/download - 查看下载统计\n"
                "/createrule - 创建群聊下载规则\n"
                "/cancel - 取消当前操作\n\n"
                "💡 **提示：**\n"
                "• 直接发送文件给Bot即可下载\n"
                "• 使用 /createrule 设置群聊自动下载\n"
                "• 支持视频、图片、音频、文档等文件类型"
            )
            
            for admin_id in self.settings.admin_user_ids:
                try:
                    await self._bot_client.send_message(
                        admin_id,
                        startup_message,
                        parse_mode='markdown'
                    )
                    logger.info(f"已发送启动通知给管理员 {admin_id}")
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
            "/cancel - 取消当前操作\n\n"
            "**使用方法：**\n"
            "1. 直接向Bot发送视频或文件，系统会自动下载\n"
            "2. 使用 /createrule 创建群聊自动下载规则\n\n"
            "**提示：**\n"
            "• 支持视频、文档、音频、图片等多种文件类型\n"
            "• 下载进度会实时更新\n"
            "• 群聊规则支持文件类型、大小、关键词过滤"
        )
        await event.reply(help_text, parse_mode='markdown')
        
    async def _handle_download_command(self, event: events.NewMessage.Event) -> None:
        """处理/download命令"""
        downloads = self.database.list_downloads(limit=100)
        
        total = len(downloads)
        completed = sum(1 for d in downloads if d.get("status") == "completed")
        failed = sum(1 for d in downloads if d.get("status") == "failed")
        downloading = sum(1 for d in downloads if d.get("status") == "downloading")
        
        stats_text = (
            f"📊 **下载统计**\n\n"
            f"**总计：** {total}\n"
            f"✅ **成功：** {completed}\n"
            f"⏳ **下载中：** {downloading}\n"
            f"❌ **失败：** {failed}\n"
        )
        
        if downloads:
            # 显示最近5个下载
            recent = downloads[:5]
            stats_text += "\n**最近下载：**\n"
            for d in recent:
                status_emoji = {
                    "completed": "✅",
                    "downloading": "⏳",
                    "failed": "❌",
                    "pending": "⏸️"
                }.get(d.get("status", "pending"), "❓")
                stats_text += f"{status_emoji} {d.get('file_name', '未知')}\n"
                
        await event.reply(stats_text, parse_mode='markdown')
        
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
                
            # 获取下载统计
            downloads = self.database.list_downloads(limit=1000)
            total = len(downloads)
            completed = sum(1 for d in downloads if d.get("status") == "completed")
            failed = sum(1 for d in downloads if d.get("status") == "failed")
            
            # 添加下载记录
            download_id = self.database.add_download(
                message_id=event.message.id,
                chat_id=event.chat_id or 0,
                bot_username=self._bot_username or "unknown",
                file_name=file_name,
                status="downloading",
            )
            
            # 发送初始回复
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
            
            reply_msg = await event.reply(reply_text, parse_mode='markdown')

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
                    
                    # 更新回复消息
                    downloads = self.database.list_downloads(limit=1000)
                    total = len(downloads)
                    completed = sum(1 for d in downloads if d.get("status") == "completed")
                    failed = sum(1 for d in downloads if d.get("status") == "failed")
                    
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
                    
                    await self._bot_client.edit_message(
                        event.chat_id,
                        reply_msg.id,
                        success_text,
                        parse_mode='markdown'
                    )
                    self._active_downloads[download_id] = False
                    
                except Exception as e:
                    logger.exception(f"下载文件失败: {e}")
                    self.database.update_download(
                        download_id,
                        status="failed",
                        error=str(e),
                    )
                    
                    downloads = self.database.list_downloads(limit=1000)
                    total = len(downloads)
                    completed = sum(1 for d in downloads if d.get("status") == "completed")
                    failed = sum(1 for d in downloads if d.get("status") == "failed")
                    
                    error_text = (
                        f"❌ **下载失败**\n\n"
                        f"**文件ID：** `{event.message.id}`\n"
                        f"**文件名：** {file_name}\n"
                        f"**错误：** {str(e)}\n\n"
                        f"**下载统计：**\n"
                        f"总计：{total} | 成功：{completed} | 失败：{failed}"
                    )
                    
                    await self._bot_client.edit_message(
                        event.chat_id,
                        reply_msg.id,
                        error_text,
                        parse_mode='markdown'
                    )
                    self._active_downloads[download_id] = False
                
        except Exception as e:
            logger.exception(f"处理媒体消息失败: {e}")
            
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
                f"**速度：** {speed_text}\n\n"
                f"**下载统计：**\n"
                f"总计：{total + 1} | 成功：{completed} | 失败：{failed}"
            )
            await self._bot_client.edit_message(
                chat_id,
                reply_message_id,
                text,
                parse_mode='markdown',
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
            
            # 解析回调数据
            if data.startswith("pause_"):
                download_id = int(data.split("_")[1])
                await self._handle_pause_download(event, download_id)
            elif data.startswith("priority_"):
                download_id = int(data.split("_")[1])
                await self._handle_priority_download(event, download_id)
            elif data.startswith("delete_"):
                download_id = int(data.split("_")[1])
                await self._handle_delete_download(event, download_id)
            elif data.startswith("retry_"):
                download_id = int(data.split("_")[1])
                await self._handle_retry_download(event, download_id)
            else:
                await event.answer("❓ 未知操作", alert=True)
                
        except Exception as e:
            logger.exception(f"处理回调查询失败: {e}")
            await event.answer(f"❌ 操作失败: {str(e)}", alert=True)
    
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
            
            # 如果正在下载，取消并标记为暂停
            if current_status == 'downloading' and self.worker:
                await self.worker.cancel_download(download_id)
                self.database.update_download(download_id, status="paused", error="用户暂停")
                await event.answer("⏸️ 已暂停下载")
                
                # 更新消息
                await event.edit(
                    f"⏸️ **已暂停**\n\n"
                    f"文件: {download.get('file_name', '未知')}\n"
                    f"状态: 已暂停\n\n"
                    f"使用 /download 命令查看所有下载"
                )
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
            # 注意：这里只是标记，实际的优先级队列需要在下载管理器中实现
            current_priority = download.get('priority', 0)
            new_priority = 10 if current_priority < 10 else 0
            
            self.database.update_download(download_id, priority=new_priority)
            
            if new_priority > 0:
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
            # TODO: 添加 database.delete_download 方法
            await event.answer("✅ 已删除下载任务")
            
            # 更新消息
            await event.edit("🗑️ **已删除**\n\n此下载任务已被删除。")
            
        except Exception as e:
            logger.exception(f"删除下载失败: {e}")
            await event.answer(f"❌ 删除失败: {str(e)}", alert=True)
    
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
                'rule_data': {}
            }
            
            # 显示群聊列表
            group_list = "📋 **请选择要监控的群聊**\n\n"
            for idx, group in enumerate(groups[:20], 1):  # 限制显示前20个
                group_list += f"{idx}. {group['title']} (ID: {group['id']})\n"
            
            if len(groups) > 20:
                group_list += f"\n... 还有 {len(groups) - 20} 个群聊\n"
            
            group_list += "\n💡 **请回复群聊编号或群聊ID**\n使用 /cancel 取消操作"
            
            await event.reply(group_list, parse_mode='markdown')
            
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
    
    async def _handle_group_selection(self, event, user_id, message_text, state):
        """处理群聊选择"""
        groups = state['groups']
        selected_group = None
        
        # 尝试解析为数字
        try:
            num = int(message_text)
            # 先尝试作为索引（1-based）
            if 1 <= num <= len(groups):
                selected_group = groups[num - 1]
            else:
                # 尝试作为chat_id
                selected_group = next((g for g in groups if g['id'] == num), None)
        except ValueError:
            pass
        
        if not selected_group:
            await event.reply("❌ 无效的选择，请输入正确的编号或ID")
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
    
    async def _handle_mode_selection(self, event, user_id, message_text, state):
        """处理模式选择"""
        if message_text == '1':
            state['rule_data']['mode'] = 'monitor'
            mode_name = '监控模式'
        elif message_text == '2':
            state['rule_data']['mode'] = 'history'
            mode_name = '历史模式'
        else:
            await event.reply("❌ 无效的选择，请回复 1 或 2")
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
            "📏 **请输入最小文件大小（MB）**\n\n"
            "💡 输入数字，例如: 10\n"
            "或回复 0 表示不限制大小"
        )
        await event.reply(size_text, parse_mode='markdown')
    
    async def _handle_min_size_input(self, event, user_id, message_text, state):
        """处理最小文件大小输入"""
        try:
            min_size = float(message_text)
            if min_size < 0:
                await event.reply("❌ 大小不能为负数，请重新输入")
                return
        except ValueError:
            await event.reply("❌ 无效的数字，请重新输入")
            return
        
        state['rule_data']['min_size_mb'] = min_size
        state['step'] = 'enter_keywords'
        
        keywords_text = (
            f"✅ 最小大小: **{min_size} MB**\n\n"
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
        confirm_text = (
            "📋 **规则配置预览**\n\n"
            f"**群聊**: {rule_data['chat_title']}\n"
            f"**模式**: {'监控模式' if rule_data['mode'] == 'monitor' else '历史模式'}\n"
            f"**文件类型**: {rule_data['extensions'] if rule_data['extensions'] else '所有类型'}\n"
            f"**最小大小**: {rule_data['min_size_mb']} MB\n"
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
            # 将MB转换为字节
            min_size_bytes = int(rule_data['min_size_mb'] * 1024 * 1024)
            
            rule_id = self.database.add_group_rule(
                chat_id=rule_data['chat_id'],
                chat_title=rule_data['chat_title'],
                mode=rule_data['mode'],
                include_extensions=rule_data['extensions'],
                min_size_bytes=min_size_bytes,
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

