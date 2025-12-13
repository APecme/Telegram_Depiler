from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any, Awaitable, Callable, Literal, Optional

logger = logging.getLogger(__name__)


def _prefer_ipv4_resolution() -> None:
    """Force asyncio sockets to resolve dotted hosts as IPv4.
    
    This patch is critical for Docker environments where IPv6 resolution
    can cause 'Address family for hostname not supported' errors.
    """
    if getattr(socket, "_telegram_ipv4_patched", False):
        return

    original_getaddrinfo = socket.getaddrinfo
    logged_hosts = set()

    def ipv4_first(
        host: str,
        port: int | str,
        family: int = 0,
        type_: int = 0,
        proto: int = 0,
        flags: int = 0,
    ):
        nonlocal logged_hosts
        ipv6_family = getattr(socket, "AF_INET6", None)
        target_families = (0, socket.AF_UNSPEC)
        if ipv6_family is not None:
            target_families += (ipv6_family,)
        
        # Force IPv4 for all non-IPv6 hosts (no ':' in hostname)
        if host and ":" not in host and family in target_families:
            if host not in logged_hosts:
                logger.debug(
                    "Forcing IPv4 resolution for host %s (family=%s)", host, family
                )
                logged_hosts.add(host)
            family = socket.AF_INET
        
        try:
            return original_getaddrinfo(host, port, family, type_, proto, flags)
        except OSError as exc:
            # If IPv4 resolution fails, log and re-raise
            if exc.errno == -9:  # Address family for hostname not supported
                logger.warning(
                    "DNS resolution failed for %s:%s (family=%s): %s. "
                    "This may indicate a Docker network configuration issue.",
                    host, port, family, exc
                )
            raise

    socket.getaddrinfo = ipv4_first
    if hasattr(socket, "has_ipv6"):
        socket.has_ipv6 = False  # type: ignore[attr-defined]
    setattr(socket, "_telegram_ipv4_patched", True)
    logger.info("IPv4-only resolver patch applied (has_ipv6=%s)", getattr(socket, "has_ipv6", None))


_prefer_ipv4_resolution()

from telethon import TelegramClient, events, functions
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
    PhoneNumberInvalidError,
    PasswordHashInvalidError,
    SendCodeUnavailableError,
)
from telethon.tl.types import User

from .config import Settings
from .database import Database


@dataclass
class LoginContext:
    phone_number: str
    state: Literal["idle", "waiting_code", "waiting_password", "completed"]
    sent_code: Optional[Any] = None
    password_hint: Optional[str] = None


class TelegramWorker:
    def __init__(self, settings: Settings, database: Database, queue_manager=None):
        self.settings = settings
        self.database = database
        self.queue_manager = queue_manager  # 全局下载队列管理器
        self._client: Optional[TelegramClient] = None
        self._event_handler_added = False
        self._lock = asyncio.Lock()
        self._client_lock = asyncio.Lock()
        self._bot_username: Optional[str] = None
        self._login_context: Optional[LoginContext] = None
        self._bot_client: Optional[TelegramClient] = None  # Bot客户端用于发送通知
        self._download_tasks: dict[int, asyncio.Task] = {}  # 跟踪正在进行的下载任务
        self._cancelled_downloads: set[int] = set()  # 已取消的下载ID

    @property
    def session_path(self) -> Path:
        # 只使用用户账户，session文件固定
        return self.settings.data_dir / "telegram_session"

    async def _get_client(self) -> TelegramClient:
        async with self._client_lock:
            if self._client:
                return self._client

            if not self.settings.api_id or not self.settings.api_hash:
                raise ValueError("api_id/api_hash not configured")

            # 只使用用户账户登录
            proxy = None
            if self.settings.proxy_host and self.settings.proxy_port:
                # 清理代理主机地址，移除可能存在的协议前缀
                proxy_host = self.settings.proxy_host.strip()
                # 移除常见的协议前缀
                for prefix in ("http://", "https://", "socks4://", "socks5://", "socks://"):
                    if proxy_host.lower().startswith(prefix):
                        proxy_host = proxy_host[len(prefix):].strip()
                        break
                
                # 移除可能的路径和端口（如果用户输入了完整 URL）
                if "/" in proxy_host:
                    proxy_host = proxy_host.split("/")[0]
                
                # 处理 IPv6 地址格式 [::1] 或 [2001:db8::1]
                if proxy_host.startswith("[") and proxy_host.endswith("]"):
                    proxy_host = proxy_host[1:-1]  # 移除方括号
                elif ":" in proxy_host:
                    # 检查是否是 IPv6 地址（包含多个冒号组）
                    # IPv4 地址格式：192.168.1.1:7890 -> 只有一个冒号，分割取主机
                    # IPv6 地址格式：2001:db8::1 或 [2001:db8::1] -> 多个冒号，不分割
                    parts = proxy_host.split(":")
                    if len(parts) == 2 and "." in parts[0]:
                        # 可能是 IPv4:port 格式，只取主机部分
                        proxy_host = parts[0]
                    # 否则可能是 IPv6 地址，保持原样
                
                # 处理 Docker 容器内访问宿主机代理的问题
                # 如果代理地址是 127.0.0.1 或 localhost，在 Docker 环境中需要转换为 host.docker.internal
                original_host = proxy_host
                if proxy_host in ("127.0.0.1", "localhost", "::1"):
                    # 检查是否在 Docker 容器中运行
                    import os
                    if os.path.exists("/.dockerenv") or os.environ.get("DOCKER_CONTAINER") == "true":
                        proxy_host = "host.docker.internal"
                        logger.info(
                            "Detected Docker environment, converting proxy host %s to %s",
                            original_host, proxy_host
                        )
                
                # 获取代理类型，默认为 http
                proxy_type = (self.settings.proxy_type or "http").lower()
                if proxy_type not in ("http", "socks4", "socks5"):
                    logger.warning("Unknown proxy type %s, defaulting to http", proxy_type)
                    proxy_type = "http"
                
                # Telethon 代理配置格式：(type, host, port, rdns, username, password)
                proxy = (
                    proxy_type,
                    proxy_host,
                    int(self.settings.proxy_port),
                    True,  # rdns
                    self.settings.proxy_user,
                    self.settings.proxy_password,
                )
                logger.info(
                    "Using proxy: %s://%s:%s (user=%s)",
                    proxy_type, proxy_host, self.settings.proxy_port,
                    self.settings.proxy_user or "none"
                )

            client = await self._create_client(proxy)
            self._client = client
            return client

    async def _create_client(self, proxy: Optional[tuple]) -> TelegramClient:
        """创建 Telegram 客户端，参考 telegram-message-bot-main 的实现方式
        
        关键配置：
        - use_ipv6=False: 强制使用 IPv4，避免 Docker 环境中的 IPv6 解析问题
        - connection_retries=5: 连接重试次数
        - retry_delay=2: 重试延迟
        - timeout=30: 连接超时
        - auto_reconnect=True: 自动重连
        """
        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                # 创建客户端（不立即连接）
                client = TelegramClient(
                    str(self.session_path),
                    int(self.settings.api_id),
                    self.settings.api_hash,
                    proxy=proxy,
                    use_ipv6=False,  # 强制使用 IPv4，避免 Docker 环境中的 IPv6 解析问题
                    connection_retries=5,
                    retry_delay=2,
                    timeout=30,
                    auto_reconnect=True,
                )
                logger.debug("TelegramClient created (attempt %d/2)", attempt + 1)
                
                # 尝试连接
                await client.connect()
                logger.info("TelegramClient connected successfully")
                return client
            except sqlite3.OperationalError as exc:
                last_error = exc
                logger.warning("Telegram session database locked, resetting session file: %s", exc)
                if 'client' in locals():
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                self._reset_session_files()
                await asyncio.sleep(0.1)
            except (ConnectionError, OSError) as exc:
                last_error = exc
                error_msg = str(exc)
                # 检查是否是 DNS 解析错误
                if "gaierror" in error_msg or "Address family" in error_msg:
                    logger.error(
                        "DNS resolution failed. This may indicate: "
                        "1) Docker network/DNS configuration issue, "
                        "2) IPv6 resolution problem, "
                        "3) Network connectivity issue. Error: %s", exc
                    )
                else:
                    logger.error("Failed to connect to Telegram: %s", exc)
                if 'client' in locals():
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                # 如果是第一次尝试，继续重试；否则退出
                if attempt == 0:
                    logger.info("Retrying connection...")
                    await asyncio.sleep(1)
                    continue
                break
            except Exception as exc:
                last_error = exc
                logger.error("Unexpected error creating Telegram client: %s", exc)
                if 'client' in locals():
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                break
        
        raise RuntimeError("Unable to initialize Telegram session") from last_error

    def _reset_session_files(self) -> None:
        base = str(self.session_path)
        if not base.endswith(".session"):
            base = f"{base}.session"
        candidates = [
            Path(base),
            Path(f"{base}-journal"),
            Path(f"{base}.journal"),
            Path(f"{base}-wal"),
            Path(f"{base}-shm"),
        ]
        for path in candidates:
            try:
                path.unlink()
            except FileNotFoundError:
                continue

    async def restart_client(self, reset_session: bool = True) -> dict[str, Any]:
        async with self._client_lock:
            if self._client:
                try:
                    await self._client.disconnect()
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Failed to disconnect client cleanly: %s", exc)
                self._client = None
            self._event_handler_added = False
            if reset_session:
                self._reset_session_files()
            self._clear_login_context()
        logger.info("Telegram client restarted (reset_session=%s)", reset_session)
        return {"status": "restarted", "reset_session": reset_session}

    async def send_login_code(self, phone_number: str, force: bool = False) -> dict[str, Any]:
        """发送验证码 - 参考 telegram-message-bot-main 的实现方式
        
        实现逻辑：
        1. 如果强制重新登录，先重置会话
        2. 创建或获取客户端
        3. 连接客户端（不进行完整登录）
        4. 发送验证码请求
        """
        async with self._lock:
            # 如果强制重新登录，先登出并重置会话
            if force:
                logger.info("Force restart requested, resetting session")
                await self.restart_client(reset_session=True)
            
            # 创建或获取客户端
            try:
                client = await self._get_client()
            except (ConnectionError, OSError, RuntimeError) as exc:
                error_msg = str(exc)
                logger.error("Failed to get Telegram client: %s", exc)
                # 提供更详细的错误信息
                if "gaierror" in error_msg or "Address family" in error_msg:
                    raise ConnectionError(
                        f"DNS 解析失败，无法连接到 Telegram 服务器。"
                        f"请检查：1) Docker 网络配置 2) DNS 设置 3) 代理配置。"
                        f"原始错误: {exc}"
                    ) from exc
                raise ConnectionError(f"无法连接到 Telegram 服务器: {exc}") from exc

            if client is None:
                logger.error("Failed to initialize Telegram client: _get_client returned None")
                raise RuntimeError("Failed to initialize Telegram client")
            
            # 确保客户端已连接（不进行完整登录）
            # 参考 telegram-message-bot-main: 先连接，再检查连接状态
            if not client.is_connected():
                try:
                    logger.debug("Client not connected, attempting to connect...")
                    await client.connect()
                    logger.info("Client connected successfully")
                except (ConnectionError, OSError) as exc:
                    error_msg = str(exc)
                    logger.error("Failed to connect client: %s", exc)
                    if "gaierror" in error_msg or "Address family" in error_msg:
                        raise ConnectionError(
                            f"DNS 解析失败，客户端连接失败。"
                            f"请检查 Docker 网络配置和 DNS 设置。"
                            f"原始错误: {exc}"
                        ) from exc
                    raise ConnectionError(f"客户端连接失败: {exc}") from exc
            
            if not client.is_connected():
                raise ConnectionError("客户端连接失败，请重试")
            
            try:
                # 发送验证码请求
                logger.info("Sending verification code to %s", phone_number)
                result = await client.send_code_request(phone_number)
                logger.info("Verification code sent successfully")
            except PhoneNumberInvalidError as exc:
                logger.error("Invalid phone number: %s", phone_number)
                raise ValueError("Invalid phone number") from exc
            except SendCodeUnavailableError as exc:
                logger.error("Send code unavailable: %s", exc)
                raise ValueError(
                    "验证码发送不可用。可能的原因："
                    "1. 该手机号的所有验证方式（短信、电话等）都已使用"
                    "2. 验证码发送过于频繁，请稍后再试"
                    "3. 需要等待一段时间后才能重新发送"
                ) from exc
            except (ConnectionError, OSError) as exc:
                error_msg = str(exc)
                logger.error("Network error while sending code: %s", exc)
                if "gaierror" in error_msg or "Address family" in error_msg:
                    raise ConnectionError(
                        f"网络错误：DNS 解析失败。"
                        f"请检查网络连接、代理设置和 Docker 网络配置。"
                        f"原始错误: {exc}"
                    ) from exc
                raise ConnectionError(f"网络错误，请检查网络连接或代理设置: {exc}") from exc
            
            # 保存登录上下文
            self._login_context = LoginContext(
                phone_number=phone_number,
                state="waiting_code",
                sent_code=result,
                password_hint=None,
            )
            
            return {
                "status": "code_sent",
                "success": True,
                "message": f"验证码已发送到 {phone_number}",
                "next_step": "verify_code",
                "step": "waiting_code",
                "timeout": getattr(result, "timeout", None),
                "phone_code_hash": result.phone_code_hash,
            }

    async def submit_verification_code(self, phone_number: str, code: str) -> dict[str, Any]:
        """提交验证码 - 参考 telegram-message-bot-main 的实现方式"""
        async with self._lock:
            client = await self._get_client()
            context = self._login_context
            
            # 验证登录上下文
            if not context:
                logger.error("No login context found for phone number: %s", phone_number)
                raise ValueError("没有待处理的登录请求。请先发送验证码。")
            
            if context.phone_number != phone_number:
                logger.error(
                    "Phone number mismatch: context=%s, request=%s",
                    context.phone_number,
                    phone_number
                )
                raise ValueError(
                    f"手机号不匹配。当前登录上下文中的手机号: {context.phone_number}，"
                    f"请求中的手机号: {phone_number}。请重新发送验证码。"
                )
            
            if context.state != "waiting_code":
                logger.error(
                    "Invalid state for verification: phone=%s, state=%s",
                    phone_number,
                    context.state
                )
                raise ValueError(
                    f"当前不在等待验证码状态，当前状态: {context.state}。"
                    f"请重新发送验证码。"
                )
            
            if not client or not client.is_connected():
                logger.error("Client not connected for phone number: %s", phone_number)
                raise ConnectionError("客户端未连接，请重新发送验证码")
            
            if not code or not code.strip():
                logger.error("Empty verification code for phone number: %s", phone_number)
                raise ValueError("验证码不能为空")
            
            try:
                # 提交验证码
                result = await client.sign_in(phone=phone_number, code=code)
                
                # 登录成功
                self.database.set_config({"phone_number": phone_number})
                context.state = "completed"
                self._clear_login_context()
                
                user = await client.get_me()
                # 保存登录状态
                self.database.save_login_state(
                    account_type="user",
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    phone_number=phone_number,
                    is_authorized=True,
                )
                return {
                    "status": "connected",
                    "success": True,
                    "message": "登录成功",
                    "step": "completed",
                    "user": self._describe_user(user),
                }
                
            except PhoneCodeInvalidError as exc:
                context.state = "idle"
                raise ValueError("Invalid code") from exc
            except PhoneCodeExpiredError as exc:
                context.state = "idle"
                raise ValueError("Code expired") from exc
            except SessionPasswordNeededError:
                # 需要二步验证密码
                context.state = "waiting_password"
                hint = await self._fetch_password_hint(client)
                context.password_hint = hint
                return {
                    "status": "password_required",
                    "success": True,
                    "message": "需要输入二步验证密码",
                    "next_step": "submit_password",
                    "step": "waiting_password",
                    "password_hint": hint,
                }
    
    async def submit_password(self, phone_number: str, password: str) -> dict[str, Any]:
        """提交二步验证密码 - 参考 telegram-message-bot-main 的实现方式"""
        async with self._lock:
            client = await self._get_client()
            context = self._login_context
            
            # 验证登录上下文
            if not context or context.phone_number != phone_number:
                raise ValueError("No pending login for this phone number. Please request a new code.")
            
            if context.state != "waiting_password":
                raise ValueError(f"当前不在等待密码状态，当前状态: {context.state}")
            
            if not client or not client.is_connected():
                raise ConnectionError("客户端未连接，请重新发送验证码")
            
            if not password:
                raise ValueError("密码不能为空")
            
            try:
                # 提交密码
                result = await client.sign_in(password=password)
                
                # 登录成功
                self.database.set_config({"phone_number": phone_number})
                context.state = "completed"
                self._clear_login_context()
                
                user = await client.get_me()
                # 保存登录状态
                self.database.save_login_state(
                    account_type="user",
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    phone_number=phone_number,
                    is_authorized=True,
                )
                return {
                    "status": "connected",
                    "success": True,
                    "message": "登录成功",
                    "step": "completed",
                    "user": self._describe_user(user),
                }
                
            except PasswordHashInvalidError as exc:
                context.state = "idle"
                raise ValueError("Invalid two-factor password") from exc

    def set_bot_client(self, bot_client: Optional[TelegramClient]) -> None:
        """设置Bot客户端，用于发送通知"""
        self._bot_client = bot_client
        logger.info("Bot客户端已设置，可以发送群聊下载通知")
    
    async def cancel_download(self, download_id: int) -> bool:
        """取消正在进行的下载任务"""
        try:
            # 标记为已取消
            self._cancelled_downloads.add(download_id)
            
            # 如果任务正在运行，取消它
            if download_id in self._download_tasks:
                task = self._download_tasks[download_id]
                if not task.done():
                    task.cancel()
                    logger.info(f"已取消下载任务 {download_id}")
                del self._download_tasks[download_id]
            
            # 更新数据库状态
            self.database.update_download(download_id, status="cancelled", error="用户取消")
            
            # 通知队列管理器，尝试启动下一个任务
            if self.queue_manager:
                await self.queue_manager.on_download_finished(download_id)
            
            return True
        except Exception as e:
            logger.exception(f"取消下载任务失败: {e}")
            return False
    
    def _cleanup_download_task(self, task: asyncio.Task) -> None:
        """清理完成的下载任务"""
        try:
            # 从任务字典中移除
            for download_id, t in list(self._download_tasks.items()):
                if t == task:
                    del self._download_tasks[download_id]
                    # 如果在取消列表中，也移除
                    self._cancelled_downloads.discard(download_id)
                    break
        except Exception as e:
            logger.debug(f"清理下载任务时出错: {e}")
    
    async def restore_queued_download(self, download_id: int, message_id: int, chat_id: int) -> None:
        """恢复队列中的下载任务（不创建新记录，直接继续下载）"""
        try:
            # 获取下载记录
            downloads = self.database.list_downloads(limit=1000)
            download = next((d for d in downloads if d.get('id') == download_id), None)
            
            if not download:
                logger.warning(f"恢复下载任务失败：找不到下载记录 {download_id}")
                return
            
            # 检查状态，确保是downloading（队列管理器已经标记为downloading）
            current_status = download.get('status')
            if current_status != 'downloading':
                logger.warning(f"恢复下载任务失败：任务状态不是downloading，当前状态: {current_status}")
                return
            
            # 获取规则信息
            rules = self.database.get_group_rules_for_chat(chat_id=chat_id, mode='monitor', only_enabled=True)
            if not rules:
                logger.warning(f"恢复下载任务失败：找不到群聊规则 {chat_id}")
                self.database.update_download(download_id, status="failed", error="找不到群聊规则")
                return
            
            rule = rules[0]  # 使用第一个规则
            
            # 获取客户端
            client = await self._get_client()
            
            # 获取消息
            try:
                chat = await client.get_entity(chat_id)
                message = await client.get_messages(chat, ids=message_id)
                
                if not message:
                    logger.warning(f"恢复下载任务失败：找不到消息 {message_id}")
                    self.database.update_download(download_id, status="failed", error="找不到消息")
                    return
                
                # 获取发送者信息
                sender = await message.get_sender()
                
                # 直接执行下载逻辑，不创建新记录
                await self._continue_download_from_queue(download_id, message, rule, chat, sender)
                
            except Exception as e:
                logger.exception(f"恢复下载任务失败: {e}")
                self.database.update_download(download_id, status="failed", error=str(e))
                
        except Exception as e:
            logger.exception(f"恢复队列下载任务失败: {e}")
    
    async def _continue_download_from_queue(self, download_id: int, message: Any, rule: dict, chat: Any, sender: Any) -> None:
        """从队列继续下载（使用已有的下载记录）"""
        try:
            import time
            from telethon.tl.custom.button import Button
            
            # 获取下载记录
            downloads = self.database.list_downloads(limit=1000)
            download = next((d for d in downloads if d.get('id') == download_id), None)
            if not download:
                return
            
            # 获取原始文件名
            original_file_name = download.get('file_name') or f"file_{message.id}"
            
            # 应用文件名模板
            filename_template = rule.get('filename_template') or "{message_id}_{file_name}"
            chat_title = getattr(chat, 'title', 'Unknown').replace('/', '_').replace('\\', '_')
            timestamp = int(time.time())
            
            # 替换模板变量
            file_name = filename_template.replace('{task_id}', str(download_id))
            file_name = file_name.replace('{message_id}', str(message.id))
            file_name = file_name.replace('{chat_title}', chat_title)
            file_name = file_name.replace('{timestamp}', str(timestamp))
            file_name = file_name.replace('{file_name}', original_file_name)
            
            # 确保文件名有扩展名
            if '.' in original_file_name and '.' not in file_name:
                ext = original_file_name.split('.')[-1]
                file_name = f"{file_name}.{ext}"
            
            # 应用保存路径
            save_dir = rule.get('save_dir') or self.settings.download_dir
            target_path = Path(save_dir) / file_name
            target_path.parent.mkdir(parents=True, exist_ok=True)
            
            logger.info("从队列恢复下载文件: %s -> %s", original_file_name, target_path)
            
            # 获取文件大小
            file_size = 0
            if message.file:
                file_size = getattr(message.file, "size", 0) or 0
            
            # 注册当前任务以便跟踪和取消
            current_task = asyncio.current_task()
            if current_task:
                self._download_tasks[download_id] = current_task
            
            # 下载文件并跟踪进度
            downloaded_bytes = 0
            last_update_time = time.time()
            last_downloaded = 0
            download_speed = 0.0
            
            def progress_callback(current: int, total: int) -> None:
                nonlocal downloaded_bytes, last_update_time, last_downloaded, download_speed
                
                # 检查是否已取消
                if download_id in self._cancelled_downloads:
                    raise asyncio.CancelledError("下载已被用户取消")
                
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
                try:
                    self.database.update_download(
                        download_id,
                        progress=progress,
                        download_speed=download_speed,
                    )
                except Exception as e:
                    logger.debug("更新下载进度失败: %s", e)
            
            # 检查是否在开始前就被取消
            if download_id in self._cancelled_downloads:
                raise asyncio.CancelledError("下载已被用户取消")
            
            await message.download_media(
                file=target_path,
                progress_callback=progress_callback if file_size > 0 else None
            )
            
            self.database.update_download(
                download_id, 
                status="completed", 
                file_path=str(target_path),
                progress=100.0,
                download_speed=0.0
            )
            logger.info("从队列恢复下载完成: %s", file_name)
            
            # 通知队列管理器，尝试启动下一个任务
            if self.queue_manager:
                await self.queue_manager.on_download_finished(download_id)
            
            # 清理任务
            self._download_tasks.pop(download_id, None)
            self._cancelled_downloads.discard(download_id)
            
        except asyncio.CancelledError:
            logger.info("从队列恢复的下载任务 %d 已被取消", download_id)
            if 'target_path' in locals() and target_path.exists():
                try:
                    target_path.unlink()
                except Exception:
                    pass
            raise
        except Exception as exc:
            logger.exception("从队列恢复下载文件失败: %s", exc)
            self.database.update_download(
                download_id, status="failed", error=str(exc)
            )
            if self.queue_manager:
                await self.queue_manager.on_download_finished(download_id)
            self._download_tasks.pop(download_id, None)
            self._cancelled_downloads.discard(download_id)

    async def start_bot_listener(self, bot_username: str) -> None:
        client = await self._get_client()
        if not await client.is_user_authorized():
            raise PermissionError("Client not authorized. Complete login first.")

        self._bot_username = bot_username

        if not self._event_handler_added:
            logger.info("🔧 注册用户账户事件处理器...")
            client.add_event_handler(self._build_handler(), events.NewMessage)
            self._event_handler_added = True
            logger.info("✅ 用户账户事件处理器已注册")
        else:
            logger.info("ℹ️  用户账户事件处理器已存在，跳过注册")

        # Bot账户不能调用get_dialogs()，跳过warm-up
        if not self.settings.bot_token:
            try:
                await client.get_dialogs()  # warm-up (仅用户账户)
            except Exception as e:
                logger.warning("Failed to get dialogs for warm-up: %s", e)
        
        logger.info("👂 开始监听来自 %s 的消息和群聊消息", bot_username)

    async def list_dialogs(self) -> list[dict[str, Any]]:
        client = await self._get_client()
        if not await client.is_user_authorized():
            raise PermissionError("Client not authorized. Complete login first.")

        from telethon.tl.types import Channel, Chat

        dialogs = await client.get_dialogs()
        results: list[dict[str, Any]] = []

        for d in dialogs:
            try:
                entity = d.entity
                # 与 BotCommandHandler._handle_createrule_command 中保持一致：
                # 只要是 Channel 或 Chat，就认为是可选的“群聊/频道”
                is_group_like = isinstance(entity, (Channel, Chat))
                if not is_group_like:
                    continue

                title = (
                    getattr(d, "name", None)
                    or getattr(entity, "title", None)
                    or getattr(entity, "first_name", None)
                    or ""
                )
                username = getattr(entity, "username", None)

                results.append(
                    {
                        "id": getattr(entity, "id", 0),
                        "title": title,
                        "username": username,
                        # 前端使用 is_group 过滤，这里对群和频道统一标记为 true
                        "is_group": True,
                    }
                )
            except Exception as exc:
                logger.debug("构建对话信息失败: %s", exc)
                continue

        return results

    def _build_handler(self) -> Callable[[events.NewMessage.Event], Awaitable[None]]:
        async def handler(event: events.NewMessage.Event) -> None:
            # 获取消息和发送者信息
            if not event.message:
                return
            
            # 获取发送者信息
            sender = await event.get_sender()
            if not sender:
                logger.debug("无法获取发送者信息，忽略消息")
                return
            
            sender_id = getattr(sender, "id", None)
            sender_username = getattr(sender, "username", None)
            sender_first_name = getattr(sender, "first_name", None)
            
            # 获取对话信息
            try:
                chat = await event.get_chat()
            except Exception as e:
                logger.debug("获取对话信息失败: %s", e)
                return
            
            # 判断是否是群聊消息
            from telethon.tl.types import Channel, Chat
            is_group = isinstance(chat, (Channel, Chat))
            
            # 记录收到的所有消息（用于调试）
            chat_title = getattr(chat, 'title', None) or getattr(chat, 'username', None) or f"Chat_{event.chat_id}"
            message_preview = (getattr(event.message, 'message', '') or '')[:50]
            logger.debug(
                "📨 收到消息 | 来源: %s | 发送者: %s | 类型: %s | 内容预览: %s",
                chat_title,
                sender_username or sender_first_name or f"ID:{sender_id}",
                "群聊" if is_group else "私聊",
                message_preview or "[媒体文件]"
            )
            
            if is_group:
                # 群聊消息：应用群聊下载规则
                await self._handle_group_message_with_rules(event, chat, sender)
                return
            
            # 以下是原有的Bot私聊消息处理逻辑
            if not self._bot_username:
                logger.debug("Bot用户名未配置，忽略消息")
                return
                
            chat_username = getattr(chat, "username", None)
            
            # 检查是否是发送给Bot的消息
            is_bot_chat = False
            if chat_username == self._bot_username:
                is_bot_chat = True
            elif isinstance(chat, User) and chat.username == self._bot_username:
                is_bot_chat = True
            elif isinstance(chat, User) and chat.id == sender_id:
                # 私聊消息，检查sender是否是管理员用户
                if sender_id in (self.settings.admin_user_ids or []):
                    is_bot_chat = True
            
            if not is_bot_chat:
                logger.debug("消息不是发送给Bot %s的，已忽略", self._bot_username)
                return
            
            # 验证管理员权限（必须配置管理员ID）
            if not self.settings.admin_user_ids:
                logger.warning("未配置管理员用户ID，忽略所有消息")
                return
            
            if not sender_id:
                logger.warning("无法获取发送者ID，忽略消息")
                return
            
            # 只接收来自管理员用户的消息
            if sender_id not in self.settings.admin_user_ids:
                logger.debug("用户账户：消息来自非管理员用户 ID=%d（管理员ID列表：%s），已忽略", sender_id, self.settings.admin_user_ids)
                return
                
            logger.info("用户账户：收到管理员用户 ID=%d 发送给Bot的消息", sender_id)
            
            # 记录所有收到的消息（不仅仅是媒体文件）
            # sender信息已在上面获取，这里只需要获取其他信息
            sender_last_name = getattr(sender, "last_name", None)
            message_text = getattr(event.message, "message", None) or getattr(event.message, "text", None)
            
            # 获取转发信息
            forward_from_id = None
            forward_from_username = None
            forward_from_first_name = None
            forward_from_last_name = None
            if event.message.fwd_from:
                # 尝试获取转发者信息
                try:
                    if hasattr(event.message.fwd_from, "from_id"):
                        from_id = event.message.fwd_from.from_id
                        if from_id:
                            # 尝试获取转发者详细信息
                            try:
                                forward_sender = await event.client.get_entity(from_id)
                                forward_from_id = getattr(forward_sender, "id", None)
                                forward_from_username = getattr(forward_sender, "username", None)
                                forward_from_first_name = getattr(forward_sender, "first_name", None)
                                forward_from_last_name = getattr(forward_sender, "last_name", None)
                            except Exception:
                                # 如果无法获取详细信息，至少保存ID
                                if hasattr(from_id, "user_id"):
                                    forward_from_id = from_id.user_id
                                elif hasattr(from_id, "channel_id"):
                                    forward_from_id = -from_id.channel_id
                except Exception as e:
                    logger.debug("获取转发信息失败: %s", e)
            
            # 检查是否有媒体
            has_media = bool(event.message.video or event.message.document or 
                           event.message.photo or event.message.audio or 
                           event.message.voice or event.message.video_note)
            
            media_type = None
            file_name = None
            if event.message.video:
                media_type = "video"
                file_name = getattr(event.message.video, "file_name", None) or getattr(event.message.file, "name", None)
            elif event.message.document:
                media_type = "document"
                file_name = getattr(event.message.document, "file_name", None) or getattr(event.message.file, "name", None)
            elif event.message.photo:
                media_type = "photo"
            elif event.message.audio:
                media_type = "audio"
                file_name = getattr(event.message.audio, "file_name", None) or getattr(event.message.file, "name", None)
            elif event.message.voice:
                media_type = "voice"
            elif event.message.video_note:
                media_type = "video_note"
            
            # 记录消息到数据库
            try:
                self.database.add_message(
                    message_id=event.message.id,
                    chat_id=event.chat_id or 0,
                    sender_id=sender_id or 0,
                    sender_username=sender_username,
                    sender_first_name=sender_first_name,
                    sender_last_name=sender_last_name,
                    message_text=message_text,
                    has_media=has_media,
                    media_type=media_type,
                    file_name=file_name,
                    forward_from_id=forward_from_id,
                    forward_from_username=forward_from_username,
                    forward_from_first_name=forward_from_first_name,
                    forward_from_last_name=forward_from_last_name,
                )
                forward_info = ""
                if forward_from_id or forward_from_username or forward_from_first_name:
                    forward_info = f" (转发自: {forward_from_username or forward_from_first_name or f'ID:{forward_from_id}'})"
                logger.info(
                    "收到消息: ID=%d, 发送者=%s, 类型=%s, 有媒体=%s%s",
                    event.message.id,
                    sender_username or sender_first_name or f"ID:{sender_id}",
                    media_type or "text",
                    has_media,
                    forward_info
                )
            except Exception as exc:
                logger.exception("记录消息失败: %s", exc)

            # 发给 Bot 的私聊消息统一由 BotCommandHandler 处理，这里不再重复下载
            if is_bot_chat:
                return

            # 如果是视频或文档，则下载
            # 注意：Bot收到的消息由bot_handler处理，这里只处理用户账户收到的其他消息
            if event.message.video or event.message.document:
                file_name = file_name or getattr(event.message.file, "name", None) or f"telegram_{event.message.id}"
                bot_username = self._bot_username
                logger.info("开始下载文件: %s (消息ID: %d)", file_name, event.message.id)
                download_id = self.database.add_download(
                    message_id=event.message.id,
                    chat_id=event.chat_id or 0,
                    bot_username=bot_username or "unknown",
                    file_name=file_name,
                    status="downloading",
                )
                try:
                    target_path = Path(self.settings.download_dir) / file_name
                    
                    # 获取文件大小
                    file_size = 0
                    if event.message.file:
                        file_size = getattr(event.message.file, "size", 0) or 0
                    
                    # 下载文件并跟踪进度
                    import time
                    downloaded_bytes = 0
                    last_update_time = time.time()
                    last_downloaded = 0
                    download_speed = 0.0
                    
                    def progress_callback(current: int, total: int) -> None:
                        nonlocal downloaded_bytes, last_update_time, last_downloaded, download_speed
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
                        
                        # 更新数据库（异步操作需要在后台任务中执行）
                        try:
                            self.database.update_download(
                                download_id,
                                progress=progress,
                                download_speed=download_speed,
                            )
                        except Exception as e:
                            logger.debug("更新下载进度失败: %s", e)
                    
                    await event.message.download_media(
                        file=target_path,
                        progress_callback=progress_callback if file_size > 0 else None
                    )
                    self.database.update_download(
                        download_id, 
                        status="completed", 
                        file_path=str(target_path),
                        progress=100.0,
                        download_speed=0.0
                    )
                    logger.info("下载完成: %s", file_name)
                except Exception as exc:  # pylint: disable=broad-except
                    logger.exception("Failed to download media: %s", exc)
                    self.database.update_download(
                        download_id, status="failed", error=str(exc)
                    )

        return handler

    async def stop(self) -> None:
        if self._client:
            await self._client.disconnect()
            self._client = None
        self._event_handler_added = False
        self._clear_login_context()


    async def _fetch_password_hint(self, client: TelegramClient) -> Optional[str]:
        try:
            password_info = await client(functions.account.GetPasswordRequest())
            return getattr(password_info, "hint", None)
        except Exception as exc:  # pragma: no cover - defensive logging only
            logger.debug("Failed to fetch password hint: %s", exc)
            return None

    def _describe_user(self, user: Optional[User]) -> dict[str, Any]:
        if not user:
            return {}
        return {
            "id": getattr(user, "id", None),
            "username": getattr(user, "username", None),
            "first_name": getattr(user, "first_name", None),
            "last_name": getattr(user, "last_name", None),
            "phone": getattr(user, "phone", None),
        }

    def _clear_login_context(self) -> None:
        """清除登录上下文"""
        self._login_context = None
    
    def get_login_state(self) -> dict[str, Any]:
        """获取当前登录状态"""
        if not self._login_context:
            return {
                "state": "idle",
                "phone_number": None,
                "has_password_hint": False,
            }
        return {
            "state": self._login_context.state,
            "phone_number": self._login_context.phone_number,
            "has_password_hint": self._login_context.password_hint is not None,
            "password_hint": self._login_context.password_hint,
        }
    
    def _format_size(self, size: int) -> str:
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"
    
    async def _handle_group_message_with_rules(self, event: events.NewMessage.Event, chat: Any, sender: Any) -> None:
        """处理群聊消息并应用下载规则"""
        try:
            chat_title = getattr(chat, 'title', 'Unknown')
            chat_id = chat.id
            messages_to_process: list[Any] = [event.message]

            # 如果是媒体相册（同一条消息里包含多张图/多个视频），只在相册中的最小 ID 消息上处理一次，并遍历所有媒体
            grouped_id = getattr(event.message, "grouped_id", None)
            if grouped_id:
                try:
                    media_group = await event.message.get_media_group()
                    media_msgs = [
                        m for m in media_group
                        if (getattr(m, "video", None) or getattr(m, "document", None) or getattr(m, "photo", None) or getattr(m, "audio", None))
                    ]
                    if media_msgs:
                        min_id = min(m.id for m in media_msgs)
                        # 只让相册中的最小 ID 消息负责触发下载，避免重复触发
                        if event.message.id != min_id:
                            logger.debug("相册媒体由另一条消息处理 (grouped_id=%s)", grouped_id)
                            return
                        messages_to_process = media_msgs
                except Exception as e:  # pragma: no cover - 防御性
                    logger.debug("获取相册媒体失败: %s", e)
            
            # 获取该群的所有启用的监控规则
            rules = self.database.get_group_rules_for_chat(
                chat_id=chat_id,
                mode='monitor',
                only_enabled=True
            )
            
            logger.debug("🔔 群聊 '%s' (ID:%d) 收到新消息", chat_title, chat_id)
            
            if not rules:
                logger.debug("  ℹ️  该群聊没有配置监控下载规则，跳过处理")
                return
            
            logger.debug("  📋 找到 %d 条启用的监控规则", len(rules))
            
            # 针对待处理的每个媒体消息进行匹配
            any_matched = False
            for msg in messages_to_process:
                if not (msg.video or msg.document or msg.photo or msg.audio):
                    continue

                logger.debug("  📎 消息包含媒体文件，开始逐条检查规则...")

                for idx, rule in enumerate(rules, 1):
                    logger.debug("\n检查第 %d/%d 条规则...", idx, len(rules))
                    if self._should_download_by_rule(msg, rule):
                        logger.info("✅ 消息匹配规则 ID:%d，开始下载 (message_id=%s)", rule['id'], getattr(msg, 'id', None))
                        # 创建下载任务并跟踪
                        task = asyncio.create_task(self._download_file_by_rule(msg, rule, chat, sender))
                        # 任务完成后清理
                        task.add_done_callback(lambda t: self._cleanup_download_task(t))
                        any_matched = True
                        break  # 当前消息匹配到一条规则就下载，避免重复

            if not any_matched:
                logger.debug("❌ 消息/相册不匹配任何规则，不下载")
                    
        except Exception as e:
            logger.exception("处理群聊消息规则时出错: %s", e)
    
    def _should_download_by_rule(self, message: Any, rule: dict) -> bool:
        """检查消息是否符合下载规则"""
        try:
            rule_id = rule.get('id', 'Unknown')
            logger.info("=" * 60)
            logger.info("开始检查规则 ID:%s", rule_id)
            
            # 获取文件名
            file_name = None
            if message.video:
                file_name = getattr(message.video, "file_name", None) or getattr(message.file, "name", None)
                logger.info("  ✓ 文件类型: 视频")
            elif message.document:
                file_name = getattr(message.document, "file_name", None) or getattr(message.file, "name", None)
                logger.info("  ✓ 文件类型: 文档")
            elif message.audio:
                file_name = getattr(message.audio, "file_name", None) or getattr(message.file, "name", None)
                logger.info("  ✓ 文件类型: 音频")
            elif message.photo:
                file_name = f"photo_{message.id}.jpg"
                logger.info("  ✓ 文件类型: 图片")
            
            logger.info("  ✓ 文件名: %s", file_name or "无")
            
            # 检查文件类型
            if rule.get('include_extensions'):
                if not file_name:
                    logger.info("  ✗ 文件扩展名检查: 失败 - 无文件名")
                    logger.info("=" * 60)
                    return False
                ext = file_name.split('.')[-1].lower() if '.' in file_name else ''
                allowed_exts = [e.strip().lower() for e in rule['include_extensions'].split(',') if e.strip()]
                if ext not in allowed_exts:
                    logger.info("  ✗ 文件扩展名检查: 失败 - '%s' 不在允许列表 %s 中", ext, allowed_exts)
                    logger.info("=" * 60)
                    return False
                logger.info("  ✓ 文件扩展名检查: 通过 - '%s' 在允许列表中", ext)
            else:
                logger.info("  - 文件扩展名检查: 跳过（未配置）")
            
            # 检查文件大小（支持范围）
            min_size_bytes = rule.get('min_size_bytes', 0) or 0
            max_size_bytes = rule.get('max_size_bytes', 0) or 0
            
            if min_size_bytes > 0 or max_size_bytes > 0:
                file_size = 0
                if message.file:
                    file_size = getattr(message.file, "size", 0) or 0
                file_size_mb = file_size / (1024 * 1024)
                
                # 检查最小值
                if min_size_bytes > 0 and file_size < min_size_bytes:
                    min_size_mb = min_size_bytes / (1024 * 1024)
                    logger.info("  ✗ 文件大小检查: 失败 - %.2f MB < %.2f MB（最小值）", file_size_mb, min_size_mb)
                    logger.info("=" * 60)
                    return False
                
                # 检查最大值
                if max_size_bytes > 0 and file_size > max_size_bytes:
                    max_size_mb = max_size_bytes / (1024 * 1024)
                    logger.info("  ✗ 文件大小检查: 失败 - %.2f MB > %.2f MB（最大值）", file_size_mb, max_size_mb)
                    logger.info("=" * 60)
                    return False
                
                # 通过检查
                if min_size_bytes > 0 and max_size_bytes > 0:
                    logger.info("  ✓ 文件大小检查: 通过 - %.2f MB 在范围 [%.2f, %.2f] MB 内", 
                               file_size_mb, min_size_bytes / (1024 * 1024), max_size_bytes / (1024 * 1024))
                elif min_size_bytes > 0:
                    logger.info("  ✓ 文件大小检查: 通过 - %.2f MB >= %.2f MB", file_size_mb, min_size_bytes / (1024 * 1024))
                else:
                    logger.info("  ✓ 文件大小检查: 通过 - %.2f MB <= %.2f MB", file_size_mb, max_size_bytes / (1024 * 1024))
            else:
                logger.info("  - 文件大小检查: 跳过（未配置或为0）")
            
            # 检查关键词
            match_mode = rule.get('match_mode', 'all')
            if match_mode == 'include' and rule.get('include_keywords'):
                keywords = [k.strip() for k in rule['include_keywords'].split(',') if k.strip()]
                message_text = getattr(message, 'message', '') or ''
                combined_text = f"{file_name or ''} {message_text}".lower()
                if not any(kw.lower() in combined_text for kw in keywords):
                    logger.info("  ✗ 关键词检查: 失败 - 未找到任何必需关键词 %s", keywords)
                    logger.info("=" * 60)
                    return False
                logger.info("  ✓ 关键词检查: 通过 - 找到必需关键词")
            
            elif match_mode == 'exclude' and rule.get('exclude_keywords'):
                keywords = [k.strip() for k in rule['exclude_keywords'].split(',') if k.strip()]
                message_text = getattr(message, 'message', '') or ''
                combined_text = f"{file_name or ''} {message_text}".lower()
                if any(kw.lower() in combined_text for kw in keywords):
                    logger.info("  ✗ 关键词检查: 失败 - 包含排除关键词 %s", keywords)
                    logger.info("=" * 60)
                    return False
                logger.info("  ✓ 关键词检查: 通过 - 不包含排除关键词")
            else:
                logger.info("  - 关键词检查: 跳过（模式为'all'或未配置）")
            
            logger.info("  ✓✓✓ 规则 ID:%s 全部检查通过！准备下载", rule_id)
            logger.info("=" * 60)
            return True
            
        except Exception as e:
            logger.exception("检查规则时出错: %s", e)
            return False
    
    async def _download_file_by_rule(self, message: Any, rule: dict, chat: Any, sender: Any) -> None:
        """按规则下载文件"""
        try:
            import time
            from datetime import datetime
            from telethon.tl.types import KeyboardButtonCallback
            from telethon.tl.custom.button import Button
            
            # 获取原始文件名
            original_file_name = None
            media_type = None
            if message.video:
                original_file_name = getattr(message.video, "file_name", None) or getattr(message.file, "name", None)
                media_type = "视频"
            elif message.document:
                original_file_name = getattr(message.document, "file_name", None) or getattr(message.file, "name", None)
                media_type = "文档"
            elif message.audio:
                original_file_name = getattr(message.audio, "file_name", None) or getattr(message.file, "name", None)
                media_type = "音频"
            elif message.photo:
                original_file_name = f"photo_{message.id}.jpg"
                media_type = "图片"

            original_file_name = original_file_name or f"file_{message.id}"

            # 从 Telegram 媒体对象中提取文件 ID，用于去重
            tg_file_id = None
            tg_access_hash = None
            media_obj = getattr(message, "document", None) or getattr(message, "photo", None)
            if media_obj is not None:
                tg_file_id = getattr(media_obj, "id", None)
                tg_access_hash = getattr(media_obj, "access_hash", None)

            # 如果之前已经有相同 Telegram 文件的已完成下载，则跳过本次按规则下载
            if tg_file_id is not None and tg_access_hash is not None:
                existing = self.database.find_download_by_telegram_file(tg_file_id, tg_access_hash)
                if existing:
                    logger.info(
                        "检测到已下载的 Telegram 文件 (download_id=%s)，按规则下载将被跳过",
                        existing.get("id"),
                    )
                    return

            # 应用文件名模板
            filename_template = rule.get('filename_template') or "{message_id}_{file_name}"
            chat_title = getattr(chat, 'title', 'Unknown').replace('/', '_').replace('\\', '_')
            timestamp = int(time.time())
            
            # 先创建下载记录（初始状态为pending）
            download_id = self.database.add_download(
                message_id=message.id,
                chat_id=chat.id,
                bot_username=self._bot_username or "unknown",
                file_name=original_file_name,
                status="pending",
                source="rule",
                tg_file_id=tg_file_id,
                tg_access_hash=tg_access_hash,
            )
            
            # 检查全局并发限制
            can_start = True
            if self.queue_manager:
                can_start = await self.queue_manager.try_start_download(download_id)
            else:
                # 没有队列管理器，直接标记为downloading
                self.database.update_download(download_id, status="downloading")
            
            # 如果任务进入队列，发送通知但不执行下载
            if not can_start:
                logger.info(f"规则下载任务 {download_id} 进入队列，等待其他任务完成")
                
                # 获取文件大小（用于通知）
                file_size = 0
                if message.file:
                    file_size = getattr(message.file, "size", 0) or 0
                
                # 发送队列通知给管理员用户
                if self._bot_client and self.settings.admin_user_ids:
                    try:
                        from telethon.tl.types import User as TgUser
                        from telethon.tl.custom.button import Button

                        sender_name = (
                            getattr(sender, "username", None)
                            or getattr(sender, "first_name", None)
                            or f"ID:{getattr(sender, 'id', 'Unknown')}"
                        )

                        notification_text = (
                            f"📋 **任务已加入队列**\n\n"
                            f"**来源群聊：** {chat_title}\n"
                            f"**发送者：** {sender_name}\n"
                            f"**文件名：** {original_file_name}\n"
                            f"**类型：** {media_type or '未知'}\n"
                            f"**大小：** {self._format_size(file_size)}\n"
                            f"**任务ID：** `{download_id}`\n"
                            f"**规则ID：** {rule.get('id', 'Unknown')}\n\n"
                            f"**状态：** 队列中\n"
                            f"当前有5个任务正在下载，本任务将在队列中等待..."
                        )

                        buttons = [
                            [
                                Button.inline("⭐ 置顶优先", f"priority_{download_id}"),
                            ],
                            [Button.inline("🗑️ 删除", f"delete_{download_id}")],
                        ]

                        for admin_id in self.settings.admin_user_ids:
                            try:
                                entity = await self._bot_client.get_entity(admin_id)
                                if isinstance(entity, TgUser):
                                    await self._bot_client.send_message(
                                        entity.id,
                                        notification_text,
                                        parse_mode="markdown",
                                        buttons=buttons,
                                    )
                                    logger.info("已向管理员用户 %s 发送队列通知", entity.id)
                                    break
                            except Exception as inner_e:
                                logger.warning("向管理员 %s 发送队列通知失败: %s", admin_id, inner_e)
                    except Exception as e:
                        logger.warning("发送队列通知失败: %s", e)
                
                return
            
            # 注册当前任务以便跟踪和取消
            current_task = asyncio.current_task()
            if current_task:
                self._download_tasks[download_id] = current_task
            
            # 替换模板变量
            file_name = filename_template.replace('{task_id}', str(download_id))
            file_name = file_name.replace('{message_id}', str(message.id))
            file_name = file_name.replace('{chat_title}', chat_title)
            file_name = file_name.replace('{timestamp}', str(timestamp))
            file_name = file_name.replace('{file_name}', original_file_name)
            
            # 确保文件名有扩展名
            if '.' in original_file_name and '.' not in file_name:
                ext = original_file_name.split('.')[-1]
                file_name = f"{file_name}.{ext}"
            
            # 应用保存路径
            save_dir = rule.get('save_dir') or self.settings.download_dir
            target_path = Path(save_dir) / file_name
            target_path.parent.mkdir(parents=True, exist_ok=True)
            
            logger.info("开始下载文件: %s -> %s", original_file_name, target_path)
            
            # 获取文件大小
            file_size = 0
            if message.file:
                file_size = getattr(message.file, "size", 0) or 0
            
            # 发送Bot通知给管理员用户（如果Bot客户端可用，且管理员为用户账号而非频道/群）
            bot_message = None
            if self._bot_client and self.settings.admin_user_ids:
                try:
                    from telethon.tl.types import User as TgUser

                    sender_name = (
                        getattr(sender, "username", None)
                        or getattr(sender, "first_name", None)
                        or f"ID:{getattr(sender, 'id', 'Unknown')}"
                    )

                    notification_text = (
                        f"📥 **群聊自动下载**\n\n"
                        f"**来源群聊：** {chat_title}\n"
                        f"**发送者：** {sender_name}\n"
                        f"**文件名：** {original_file_name}\n"
                        f"**类型：** {media_type or '未知'}\n"
                        f"**大小：** {self._format_size(file_size)}\n"
                        f"**任务ID：** `{download_id}`\n"
                        f"**规则ID：** {rule.get('id', 'Unknown')}\n\n"
                        f"**状态：** 正在下载...\n"
                        f"**进度：** 0%"
                    )

                    # 创建内联键盘按钮
                    buttons = [
                        [
                            Button.inline("⏸️ 暂停", f"pause_{download_id}"),
                            Button.inline("⭐ 置顶优先", f"priority_{download_id}"),
                        ],
                        [Button.inline("🗑️ 删除", f"delete_{download_id}")],
                    ]

                    # 只向“管理员用户”（User 类型）发送私聊通知，避免误填频道/群ID导致发消息到频道
                    for admin_id in self.settings.admin_user_ids:
                        try:
                            entity = await self._bot_client.get_entity(admin_id)
                            if isinstance(entity, TgUser):
                                bot_message = await self._bot_client.send_message(
                                    entity.id,
                                    notification_text,
                                    parse_mode="markdown",
                                    buttons=buttons,
                                )
                                logger.info("已向管理员用户 %s 发送下载通知", entity.id)
                                break
                        except Exception as inner_e:  # pragma: no cover - 防御性
                            logger.warning("向管理员 %s 发送通知失败: %s", admin_id, inner_e)

                    if not bot_message:
                        logger.warning("未找到可用的管理员用户账号，群聊自动下载通知已跳过")
                except Exception as e:
                    logger.warning("发送Bot通知失败: %s", e)
            
            # 下载文件并跟踪进度
            downloaded_bytes = 0
            last_update_time = time.time()
            last_downloaded = 0
            download_speed = 0.0
            
            def progress_callback(current: int, total: int) -> None:
                nonlocal downloaded_bytes, last_update_time, last_downloaded, download_speed
                
                # 检查是否已取消
                if download_id in self._cancelled_downloads:
                    raise asyncio.CancelledError("下载已被用户取消")
                
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
                try:
                    self.database.update_download(
                        download_id,
                        progress=progress,
                        download_speed=download_speed,
                    )
                except Exception as e:
                    logger.debug("更新下载进度失败: %s", e)
            
            # 检查是否在开始前就被取消
            if download_id in self._cancelled_downloads:
                raise asyncio.CancelledError("下载已被用户取消")
            
            await message.download_media(
                file=target_path,
                progress_callback=progress_callback if file_size > 0 else None
            )
            
            self.database.update_download(
                download_id, 
                status="completed", 
                file_path=str(target_path),
                progress=100.0,
                download_speed=0.0
            )
            logger.info("下载完成: %s", file_name)
            
            # 通知队列管理器，尝试启动下一个任务
            if self.queue_manager:
                await self.queue_manager.on_download_finished(download_id)
            
            # 更新Bot通知为完成状态
            if bot_message and self._bot_client:
                try:
                    sender_name = getattr(sender, 'username', None) or getattr(sender, 'first_name', None) or f"ID:{getattr(sender, 'id', 'Unknown')}"
                    
                    completed_text = (
                        f"✅ **下载完成**\n\n"
                        f"**来源群聊：** {chat_title}\n"
                        f"**发送者：** {sender_name}\n"
                        f"**文件名：** {file_name}\n"
                        f"**类型：** {media_type or '未知'}\n"
                        f"**大小：** {self._format_size(file_size)}\n"
                        f"**任务ID：** `{download_id}`\n"
                        f"**规则ID：** {rule.get('id', 'Unknown')}\n"
                        f"**保存路径：** `{target_path}`\n\n"
                        f"**状态：** 已完成"
                    )
                    
                    # 完成后只保留删除按钮
                    buttons = [
                        [Button.inline("🗑️ 删除文件", f"delete_{download_id}")]
                    ]
                    
                    await self._bot_client.edit_message(
                        bot_message.chat_id,
                        bot_message.id,
                        completed_text,
                        parse_mode='markdown',
                        buttons=buttons
                    )
                except Exception as e:
                    logger.warning("更新Bot通知失败: %s", e)
            
        except asyncio.CancelledError:
            # 下载被取消
            logger.info("下载任务 %d 已被取消", download_id if 'download_id' in locals() else 0)
            if 'download_id' in locals():
                # 删除未完成的文件
                if 'target_path' in locals() and target_path.exists():
                    try:
                        target_path.unlink()
                        logger.info("已删除未完成的文件: %s", target_path)
                    except Exception as e:
                        logger.warning("删除未完成文件失败: %s", e)
                
                # 数据库状态已在 cancel_download 中更新，这里不需要再更新
                # Bot消息已在 _handle_delete_download 中更新
            raise  # 重新抛出以便任务正确结束
            
        except Exception as exc:
            logger.exception("按规则下载文件失败: %s", exc)
            if 'download_id' in locals():
                self.database.update_download(
                    download_id, status="failed", error=str(exc)
                )
                
                # 通知队列管理器，尝试启动下一个任务
                if self.queue_manager:
                    await self.queue_manager.on_download_finished(download_id)
                
                # 更新Bot通知为失败状态
                if 'bot_message' in locals() and bot_message and self._bot_client:
                    try:
                        sender_name = getattr(sender, 'username', None) or getattr(sender, 'first_name', None) or f"ID:{getattr(sender, 'id', 'Unknown')}"
                        
                        failed_text = (
                            f"❌ **下载失败**\n\n"
                            f"**来源群聊：** {chat_title}\n"
                            f"**发送者：** {sender_name}\n"
                            f"**文件名：** {original_file_name}\n"
                            f"**任务ID：** `{download_id}`\n"
                            f"**规则ID：** {rule.get('id', 'Unknown')}\n\n"
                            f"**错误：** {str(exc)}"
                        )
                        
                        buttons = [
                            [Button.inline("🔄 重试", f"retry_{download_id}")],
                            [Button.inline("🗑️ 删除记录", f"delete_{download_id}")]
                        ]
                        
                        await self._bot_client.edit_message(
                            bot_message.chat_id,
                            bot_message.id,
                            failed_text,
                            parse_mode='markdown',
                            buttons=buttons
                        )
                    except Exception as e:
                        logger.warning("更新Bot失败通知失败: %s", e)

