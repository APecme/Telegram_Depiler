from __future__ import annotations

import asyncio
import logging
import time
import hashlib
import secrets
import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, Body, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse
from telethon import events

from .config import get_settings
from .database import Database
from .schemas import (
    ConfigPayload,
    RestartRequest,
    SendCodeRequest,
    StartBotRequest,
    VerifyCodeRequest,
    SubmitPasswordRequest,
    VerifyRequest,
    GroupRuleCreate,
    GroupRuleUpdate,
    AdminLoginRequest,
    AdminCredentialsUpdate,
)
from .telegram_worker import TelegramWorker
from .bot_handler import BotCommandHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
database = Database(settings.data_dir / "state.db")
settings.load_from_mapping(database.get_config())


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _ensure_default_admin() -> None:
    """确保面板管理账号存在，默认 admin/admin。"""
    cfg = database.get_config()
    if "ui_admin_username" not in cfg or "ui_admin_password_hash" not in cfg:
        logging.getLogger(__name__).info("初始化默认面板账号 admin/admin")
        database.set_config(
            {
                "ui_admin_username": cfg.get("ui_admin_username", "admin"),
                "ui_admin_password_hash": cfg.get(
                    "ui_admin_password_hash", _hash_password("admin")
                ),
            }
        )


_ensure_default_admin()


def _ensure_inside_download_dir(rel_path: str) -> Path:
    """将相对路径限制在下载目录内，防止目录穿越。"""
    root = Path(settings.download_dir).resolve()
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root)):
        raise HTTPException(status_code=400, detail="路径不合法")
    return target


def _get_admin_credentials() -> tuple[str, str]:
    cfg = database.get_config()
    username = cfg.get("ui_admin_username") or "admin"
    pwd_hash = cfg.get("ui_admin_password_hash") or _hash_password("admin")
    return username, pwd_hash


# 简单的内存会话令牌，用于面板登录校验（容器重启后需重新登录）
ADMIN_TOKENS: set[str] = set()


class DownloadQueueManager:
    """全局下载队列管理器，限制最多同时5个下载任务"""
    
    MAX_CONCURRENT = 5
    
    def __init__(self, database: Database):
        self.database = database
        self._lock = asyncio.Lock()
    
    async def try_start_download(self, download_id: int) -> bool:
        """尝试开始一个下载任务。如果当前并发数已满，返回False并将任务标记为queued"""
        async with self._lock:
            # 统计当前正在下载的任务数
            downloads = self.database.list_downloads(limit=1000)
            downloading_count = sum(1 for d in downloads if d.get('status') == 'downloading')
            
            if downloading_count >= self.MAX_CONCURRENT:
                # 超过并发限制，标记为队列中
                self.database.update_download(download_id, status='queued')
                logger.info(f"下载任务 {download_id} 进入队列（当前 {downloading_count}/{self.MAX_CONCURRENT}）")
                return False
            else:
                # 可以开始下载
                self.database.update_download(download_id, status='downloading')
                logger.info(f"下载任务 {download_id} 开始（当前 {downloading_count + 1}/{self.MAX_CONCURRENT}）")
                return True
    
    async def on_download_finished(self, download_id: int):
        """下载完成/失败/取消时调用，尝试启动队列中的下一个任务"""
        async with self._lock:
            # 查找最早的queued任务（按优先级和创建时间排序）
            downloads = self.database.list_downloads(limit=1000)
            queued_tasks = [
                d for d in downloads 
                if d.get('status') == 'queued'
            ]
            
            if not queued_tasks:
                logger.info(f"下载任务 {download_id} 完成，队列为空")
                return
            
            # 按优先级（降序）和创建时间（升序）排序
            queued_tasks.sort(key=lambda d: (-(d.get('priority') or 0), d.get('created_at') or ''))
            next_task = queued_tasks[0]
            next_id = next_task.get('id')
            
            if next_id is None:
                return
            
            # 将队列中的任务标记为downloading
            self.database.update_download(next_id, status='downloading')
            logger.info(f"下载任务 {download_id} 完成，启动队列任务 {next_id}")
            
            # 根据来源触发下载恢复
            source = next_task.get('source', 'bot')
            if source == 'rule' and worker:
                # 规则下载：通过message_id和chat_id恢复
                message_id = next_task.get('message_id')
                chat_id = next_task.get('chat_id')
                if message_id and chat_id:
                    # 在后台任务中恢复下载
                    asyncio.create_task(worker.restore_queued_download(next_id, message_id, chat_id))
                else:
                    logger.warning(f"规则下载任务 {next_id} 缺少message_id或chat_id，无法恢复")
            elif source == 'bot' and bot_handler:
                # Bot 下载：通过数据库记录恢复
                asyncio.create_task(bot_handler.restore_queued_download(next_task))


download_queue_manager = DownloadQueueManager(database)
worker = TelegramWorker(settings, database, queue_manager=download_queue_manager)
bot_handler: Optional[BotCommandHandler] = None


async def _resume_incomplete_downloads() -> None:
    """容器重启后，自动继续之前的 downloading / queued / pending 任务。

    策略：
    - 将 status 为 downloading/pending 的任务统一重置为 queued；
    - 然后按优先级和创建时间排序，依次交给 DownloadQueueManager.try_start_download；
    - 由来源 source 决定调用 TelegramWorker 或 BotCommandHandler 进行实际恢复。
    """
    # 先获取尽量多的记录（与队列管理器保持一致，最多 1000 条）
    downloads = database.list_downloads(limit=1000)
    if not downloads:
        return

    # 需要恢复的状态
    resume_statuses = {"downloading", "queued", "pending"}
    candidates = [d for d in downloads if d.get("status") in resume_statuses]
    if not candidates:
        return

    logger.info("检测到 %s 个未完成的下载任务，准备尝试自动恢复", len(candidates))

    # 第一步：将 downloading/pending 状态统一重置为 queued，避免误判为正在下载但实际上任务已丢失
    for d in candidates:
        status = d.get("status")
        if status in ("downloading", "pending"):
            try:
                database.update_download(int(d["id"]), status="queued")
            except Exception as exc:  # pragma: no cover - 防御性
                logger.warning("重置下载任务 %s 状态失败: %s", d.get("id"), exc)

    # 重新拉取一遍，拿到最新的 queued 列表
    downloads = database.list_downloads(limit=1000)
    queued_tasks = [d for d in downloads if d.get("status") == "queued"]
    if not queued_tasks:
        logger.info("状态重置后队列为空，无需恢复任务")
        return

    # 按优先级（降序）和创建时间（升序）排序
    queued_tasks.sort(key=lambda d: (-(d.get("priority") or 0), d.get("created_at") or ""))

    started_count = 0
    for task in queued_tasks:
        download_id = task.get("id")
        if download_id is None:
            continue

        can_start = await download_queue_manager.try_start_download(int(download_id))
        if not can_start:
            # 并发已满，后续任务保持 queued，等运行中的任务完成后由 on_download_finished 接力
            continue

        source = task.get("source", "bot")
        try:
            if source == "rule" and worker:
                message_id = task.get("message_id")
                chat_id = task.get("chat_id")
                if message_id and chat_id:
                    asyncio.create_task(worker.restore_queued_download(int(download_id), int(message_id), int(chat_id)))
                    started_count += 1
                else:
                    logger.warning("规则下载任务 %s 缺少 message_id 或 chat_id，无法自动恢复", download_id)
            elif source == "bot" and bot_handler:
                asyncio.create_task(bot_handler.restore_queued_download(task))
                started_count += 1
        except Exception as exc:  # pragma: no cover - 防御性
            logger.warning("自动恢复下载任务 %s 失败: %s", download_id, exc)

    logger.info("自动恢复下载任务完成，本次共启动 %s 个任务", started_count)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动和关闭时的生命周期管理"""
    global bot_handler
    
    # 记录当前配置
    logger.info("应用启动，当前配置:")
    logger.info("  - Bot Token: %s", "已配置" if settings.bot_token else "未配置")
    logger.info("  - Bot Username: %s", settings.bot_username or "未配置")
    logger.info("  - 管理员用户ID列表: %s", settings.admin_user_ids if settings.admin_user_ids else "未配置")
    
    # 启动时：尝试自动启动 Bot 命令处理器（如果满足条件）
    try:
        await _ensure_bot_handler_running()
    except Exception as e:
        logger.warning(f"初始化 Bot 命令处理器失败: {e}")
    # 启动时：自动尝试恢复未完成的下载任务
    try:
        await _resume_incomplete_downloads()
    except Exception as e:  # pragma: no cover - 防御性
        logger.warning("自动恢复未完成下载任务失败: %s", e)
    
    yield
    
    # 关闭时：断开连接
    if bot_handler:
        await bot_handler.stop()
    await worker.stop()


async def _ensure_bot_handler_running() -> None:
    """在满足条件时自动启动 BotCommandHandler。

    条件：
    - 已配置 bot_token 和 bot_username
    - 用户账户已登录（is_user_authorized 为 True 或数据库中有登录状态）
    - 当前没有正在运行的 bot_handler
    """
    global bot_handler

    if bot_handler is not None:
        return

    if not settings.bot_token or not settings.bot_username:
        logger.info("Bot Token 或 Bot Username 未配置，跳过自动启动 Bot 命令处理器")
        return

    # 检查数据库中的登录状态，如果客户端连接失败但数据库中有状态，说明可能是连接问题
    login_state = database.get_login_state()
    user_logged_in_db = login_state and login_state.get("is_authorized", False)

    try:
        client = await worker._get_client()
    except Exception as exc:  # pragma: no cover - 防御性
        logger.warning("获取 Telegram 客户端失败，无法自动启动 Bot 命令处理器: %s", exc)
        return

    try:
        user_authorized = await client.is_user_authorized()

        # 如果客户端说未授权，但数据库中有登录状态，可能需要重新连接
        if not user_authorized and user_logged_in_db:
            logger.info("客户端连接状态与数据库不一致，尝试重新连接...")
            # 重新获取客户端（可能重建连接）
            try:
                client = await worker._get_client()
                user_authorized = await client.is_user_authorized()
                if user_authorized:
                    logger.info("重新连接成功，用户账户已确认登录")
                else:
                    logger.warning("重新连接后仍未授权，清除数据库登录状态")
                    database.clear_login_state()
                    return
            except Exception as reconnect_exc:
                logger.warning("重新连接失败: %s", reconnect_exc)
                return
        elif not user_authorized:
            logger.info("用户账户未登录，暂不自动启动 Bot 命令处理器")
            return

        user_info = await client.get_me()
        logger.info("用户账户已登录: @%s (ID: %s)", user_info.username, user_info.id)

        bot_handler = BotCommandHandler(settings, database, client, worker, queue_manager=download_queue_manager)
        await bot_handler.start()
        logger.info("Bot 命令处理器已自动启动")

        # 设置Bot客户端到worker，用于发送群聊下载通知
        worker.set_bot_client(bot_handler._bot_client)

        # 同时启动用户账户的事件监听器，用于监控群聊消息
        await worker.start_bot_listener(settings.bot_username)
        logger.info("用户账户事件监听器已自动启动，开始监控群聊消息")

        asyncio.create_task(worker.catch_up_missed_group_messages())
    except Exception as exc:  # pragma: no cover - 防御性
        logger.warning("自动启动 Bot 命令处理器失败: %s", exc)


api = FastAPI(title="Telegram Download Manager", lifespan=lifespan)
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@api.get("/health")
async def health_check() -> dict:
    return {"status": "ok", "version": settings.version}


@api.get("/config")
async def read_config() -> dict:
    stored = database.get_config()
    return {
        "api_id": stored.get("api_id"),
        "api_hash": stored.get("api_hash"),
        "phone_number": stored.get("phone_number"),
        "bot_token": stored.get("bot_token"),
        "bot_username": stored.get("bot_username"),
        "admin_user_ids": stored.get("admin_user_ids", ""),
        "proxy": {
            "type": stored.get("proxy_type") or "http",
            "host": stored.get("proxy_host"),
            "port": stored.get("proxy_port"),
            "user": stored.get("proxy_user"),
            "password": stored.get("proxy_password"),
        },
    }


def _require_admin(token: str | None) -> None:
    """验证管理员token"""
    logger.debug(f"验证管理员token: token存在={token is not None}, token长度={len(token) if token else 0}, ADMIN_TOKENS数量={len(ADMIN_TOKENS)}")
    if not token:
        logger.warning("管理员token为空")
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    if token not in ADMIN_TOKENS:
        logger.warning(f"管理员token无效: token前10位={token[:10] if len(token) > 10 else token}, ADMIN_TOKENS中的token数量={len(ADMIN_TOKENS)}")
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    logger.debug("管理员token验证通过")


@api.post("/config")
async def update_config(
    payload: ConfigPayload,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    # 仅允许已登录的面板管理员修改配置
    _require_admin(x_admin_token)
    global bot_handler

    old_bot_token = settings.bot_token
    old_bot_username = settings.bot_username

    settings.api_id = payload.api_id
    settings.api_hash = payload.api_hash
    settings.phone_number = payload.phone_number
    settings.bot_token = payload.bot_token
    settings.bot_username = payload.bot_username
    
    # 更新管理员ID列表
    if payload.admin_user_ids:
        try:
            settings.admin_user_ids = [int(x.strip()) for x in payload.admin_user_ids.split(",") if x.strip() and x.strip().isdigit()]
        except (TypeError, ValueError):
            settings.admin_user_ids = []
    else:
        settings.admin_user_ids = []

    if payload.proxy:
        settings.proxy_type = payload.proxy.type or "http"
        settings.proxy_host = payload.proxy.host
        settings.proxy_port = payload.proxy.port
        settings.proxy_user = payload.proxy.user
        settings.proxy_password = payload.proxy.password

    database.set_config(
        {
            "api_id": payload.api_id,
            "api_hash": payload.api_hash,
            "phone_number": payload.phone_number,
            "bot_token": payload.bot_token or "",
            "bot_username": payload.bot_username,
            "admin_user_ids": payload.admin_user_ids or "",
            "proxy_type": payload.proxy.type if payload.proxy else "http",
            "proxy_host": payload.proxy.host if payload.proxy else "",
            "proxy_port": payload.proxy.port if payload.proxy else "",
            "proxy_user": payload.proxy.user if payload.proxy else "",
            "proxy_password": payload.proxy.password if payload.proxy else "",
        }
    )
    token_changed = (old_bot_token or "") != (settings.bot_token or "")
    username_changed = (old_bot_username or "") != (settings.bot_username or "")

    if token_changed or username_changed:
        if bot_handler is not None:
            try:
                await bot_handler.stop()
            except Exception as exc:  # pragma: no cover - 防御性
                logger.warning("停止旧的 Bot 命令处理器时出错: %s", exc)
            bot_handler = None

        bot_session_base = settings.data_dir / "bot_session"
        for bot_session_path in [bot_session_base, bot_session_base.with_suffix(".session")]:
            try:
                if bot_session_path.exists():
                    bot_session_path.unlink()
            except Exception as exc:  # pragma: no cover - 防御性
                logger.warning("删除旧的 Bot 会话文件失败: %s", exc)

    try:
        await _ensure_bot_handler_running()
    except Exception as exc:  # pragma: no cover - 防御性
        logger.warning("配置更新后自动启动 Bot 命令处理器失败: %s", exc)

    return {"status": "saved"}


@api.post("/admin/login")
async def admin_login(body: AdminLoginRequest) -> dict:
    """面板登录，默认账号密码 admin/admin。"""
    username, pwd_hash = _get_admin_credentials()
    if body.username != username or _hash_password(body.password) != pwd_hash:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = secrets.token_hex(32)
    ADMIN_TOKENS.add(token)
    logger.info(f"用户 {username} 登录成功，生成token: {token[:10]}... (长度: {len(token)}), ADMIN_TOKENS数量: {len(ADMIN_TOKENS)}")
    return {"token": token, "username": username}


@api.get("/admin/me")
async def admin_me(x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")) -> dict:
    """校验当前面板会话是否有效。"""
    _require_admin(x_admin_token)
    username, _ = _get_admin_credentials()
    return {"username": username}


@api.post("/admin/credentials")
async def update_admin_credentials(
    body: AdminCredentialsUpdate,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    """修改面板账号密码，需要已登录会话。"""
    _require_admin(x_admin_token)

    if not body.username and not body.password:
        raise HTTPException(status_code=400, detail="至少提供用户名或密码之一")

    cfg_update: dict[str, str] = {}
    if body.username:
        cfg_update["ui_admin_username"] = body.username
    if body.password:
        cfg_update["ui_admin_password_hash"] = _hash_password(body.password)

    database.set_config(cfg_update)

    # 修改凭据后清空所有现有令牌，要求重新登录
    ADMIN_TOKENS.clear()

    return {"status": "updated"}


@api.post("/auth/send-code")
async def send_code(body: SendCodeRequest) -> dict:
    try:
        result = await worker.send_login_code(body.phone_number, force=body.force)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (RuntimeError, ConnectionError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return result


@api.post("/auth/verify-code")
async def verify_code(body: VerifyCodeRequest) -> dict:
    """提交验证码 - 参考 telegram-message-bot-main 的实现方式"""
    try:
        result = await worker.submit_verification_code(
            phone_number=body.phone_number,
            code=body.code,
        )
    except (ValueError, ConnectionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(result, dict) and result.get("status") == "connected":
        await _ensure_bot_handler_running()
    return result


@api.post("/auth/submit-password")
async def submit_password(body: SubmitPasswordRequest) -> dict:
    """提交二步验证密码 - 参考 telegram-message-bot-main 的实现方式"""
    try:
        result = await worker.submit_password(
            phone_number=body.phone_number,
            password=body.password,
        )
    except (ValueError, ConnectionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(result, dict) and result.get("status") == "connected":
        await _ensure_bot_handler_running()
    return result


@api.post("/auth/verify")
async def verify(body: VerifyRequest) -> dict:
    """统一的验证接口，根据 step 字段处理验证码或密码"""
    if body.step == "code":
        if not body.code or not body.code.strip():
            raise HTTPException(status_code=400, detail="验证码不能为空")
        try:
            result = await worker.submit_verification_code(
                phone_number=body.phone_number,
                code=body.code.strip(),
            )
        except ValueError as exc:
            # ValueError 通常是业务逻辑错误，返回 400
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ConnectionError as exc:
            # ConnectionError 是连接问题，返回 503
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            # 其他未知错误，记录日志并返回 500
            import logging
            logger = logging.getLogger(__name__)
            logger.exception("Unexpected error in verify code")
            raise HTTPException(status_code=500, detail=f"验证失败: {str(exc)}") from exc
        if isinstance(result, dict) and result.get("status") == "connected":
            await _ensure_bot_handler_running()
        return result
    elif body.step == "password":
        if not body.password or not body.password.strip():
            raise HTTPException(status_code=400, detail="密码不能为空")
        try:
            result = await worker.submit_password(
                phone_number=body.phone_number,
                password=body.password.strip(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ConnectionError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            import logging
            logger = logging.getLogger(__name__)
            logger.exception("Unexpected error in verify password")
            raise HTTPException(status_code=500, detail=f"验证失败: {str(exc)}") from exc
        if isinstance(result, dict) and result.get("status") == "connected":
            await _ensure_bot_handler_running()
        return result
    else:
        raise HTTPException(status_code=400, detail=f"无效的步骤: {body.step}")


@api.get("/auth/login-state")
async def get_login_state() -> dict:
    """获取当前登录状态"""
    return worker.get_login_state()


@api.post("/auth/restart")
async def restart_client(body: RestartRequest) -> dict:
    return await worker.restart_client(body.reset_session)


@api.post("/bot/start")
async def start_bot(body: StartBotRequest) -> dict:
    """启动与 Bot 相关的监听逻辑。

    - 如果配置了 bot_token，则启动 BotCommandHandler，由 Bot 收消息并回复开始/完成等信息；
    - 否则退回到旧行为，仅使用用户账户监听 Bot 对话。
    """
    global bot_handler

    try:
        settings.bot_username = body.bot_username
        database.set_config({"bot_username": body.bot_username})

        # 必须配置 bot_token 才允许启动监听，否则提示用户先在设置页保存 Bot Token
        if not settings.bot_token:
            raise HTTPException(
                status_code=400,
                detail="未配置 Bot Token，请先在设置页保存 Bot Token 后再启动监听",
            )

        client = await worker._get_client()
        if not await client.is_user_authorized():
            raise HTTPException(status_code=400, detail="用户账户未登录，请先完成登录再启动 Bot")

        # 如已有运行中的 handler，先尝试停止（用于重新启动场景）
        if bot_handler is not None:
            try:
                await bot_handler.stop()
            except Exception:  # pragma: no cover - 防御性
                pass

        # 始终通过 BotCommandHandler 来处理 Bot 收到的消息并回复
        bot_handler = BotCommandHandler(settings, database, client, worker, queue_manager=download_queue_manager)
        await bot_handler.start()
        
        # 设置Bot客户端到worker，用于发送群聊下载通知
        worker.set_bot_client(bot_handler._bot_client)
        
        # 同时启动用户账户的事件监听器，用于监控群聊消息
        await worker.start_bot_listener(body.bot_username)
        logger.info("用户账户事件监听器已启动，开始监控群聊消息")
        asyncio.create_task(worker.catch_up_missed_group_messages())
        
        return {"status": "bot_started", "bot_username": body.bot_username}
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/bot/stop")
async def stop_bot() -> dict:
    """停止 Bot 监听。"""
    global bot_handler

    if bot_handler is not None:
        try:
            await bot_handler.stop()
        except Exception as exc:  # pragma: no cover - 防御性日志
            logger.warning("停止 Bot 命令处理器时出错: %s", exc)
        finally:
            bot_handler = None

    return {"status": "stopped", "bot_username": settings.bot_username}


@api.get("/bot/status")
async def bot_status() -> dict:
    """查询 Bot 监听状态。"""
    return {
        "running": bool(bot_handler),
        "bot_username": settings.bot_username,
    }


@api.get("/downloads")
async def list_downloads(
    page: int = Query(1, ge=1, description="页码（从1开始）"),
    page_size: int = Query(20, ge=1, le=200, description="每页数量"),
    status: str | None = Query(default=None, description="状态筛选，多值用逗号分隔，如 completed,downloading,queued"),
    rule_id: int | None = Query(default=None, description="按规则ID筛选"),
    save_dir: str | None = Query(default=None, description="按保存路径模糊匹配"),
    min_size_mb: float | None = Query(default=None, description="最小大小（MB）"),
    max_size_mb: float | None = Query(default=None, description="最大大小（MB）"),
    start_time: str | None = Query(default=None, description="开始时间，格式 YYYY-MM-DD HH:MM:SS"),
    end_time: str | None = Query(default=None, description="结束时间，格式 YYYY-MM-DD HH:MM:SS"),
) -> dict:
    """带筛选和分页的下载记录列表。"""
    statuses: list[str] | None = None
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]

    min_size_bytes = int(min_size_mb * 1024 * 1024) if min_size_mb is not None else None
    max_size_bytes = int(max_size_mb * 1024 * 1024) if max_size_mb is not None else None

    result = database.search_downloads(
        page=page,
        page_size=page_size,
        statuses=statuses,
        rule_id=rule_id,
        save_dir_like=save_dir,
        min_size_bytes=min_size_bytes,
        max_size_bytes=max_size_bytes,
        start_time=start_time,
        end_time=end_time,
    )
    return result


@api.post("/downloads/{download_id}/pause")
async def pause_download(download_id: int) -> dict:
    """暂停下载（支持 Bot 与群聊规则任务）。"""
    downloads = database.list_downloads(limit=1000)
    download = next((d for d in downloads if d.get("id") == download_id), None)

    if not download:
        raise HTTPException(status_code=404, detail="下载记录不存在")

    status = download.get("status")
    if status != "downloading":
        return {"success": False, "message": f"当前状态({status})无法暂停"}

    source = download.get("source") or "bot"
    success = False

    # 群聊规则任务由 TelegramWorker 管理
    if source == "rule" and worker is not None:
        success = await worker.cancel_download(download_id)
    # Bot 任务由 BotCommandHandler 管理
    elif bot_handler is not None:
        success = await bot_handler.pause_download(download_id)

    if success:
        database.update_download(download_id, status="paused", error="用户暂停")
        return {"success": True, "message": "已暂停下载"}

    return {"success": False, "message": "暂停失败"}


@api.post("/downloads/{download_id}/resume")
async def resume_download(download_id: int) -> dict:
    """恢复下载（支持 Bot 与群聊规则任务）。"""
    downloads = database.list_downloads(limit=1000)
    download = next((d for d in downloads if d.get("id") == download_id), None)

    if not download:
        raise HTTPException(status_code=404, detail="下载记录不存在")

    status = download.get("status")
    if status not in ("paused", "queued"):
        return {"success": False, "message": f"当前状态({status})无法恢复"}

    source = download.get("source") or "bot"
    
    # 检查全局并发限制
    can_start = await download_queue_manager.try_start_download(download_id)
    
    if can_start:
        # 如果是规则下载，需要实际触发下载
        if source == "rule" and worker:
            message_id = download.get("message_id")
            chat_id = download.get("chat_id")
            if message_id and chat_id:
                # 在后台任务中恢复下载
                asyncio.create_task(worker.restore_queued_download(download_id, message_id, chat_id))
        # Bot下载的恢复逻辑在bot_handler中处理
        return {"success": True, "message": "已恢复下载"}
    else:
        return {"success": True, "message": "任务已加入队列，等待其他任务完成"}


@api.post("/downloads/{download_id}/priority")
async def set_download_priority(download_id: int) -> dict:
    """设置下载优先级，并在需要时抢占其他下载任务。"""
    downloads = database.list_downloads(limit=1000)
    download = next((d for d in downloads if d.get("id") == download_id), None)

    if not download:
        raise HTTPException(status_code=404, detail="下载记录不存在")

    current_priority = download.get("priority", 0)
    new_priority = 10 if current_priority < 10 else 0

    database.update_download(download_id, priority=new_priority)

    # 如果设置为高优先级，则抢占最早开始的其他下载任务
    if new_priority > 0:
        other_candidates = [
            d
            for d in downloads
            if d.get("status") == "downloading" and d.get("id") != download_id
        ]
        if other_candidates:
            other_candidates.sort(key=lambda d: d.get("created_at") or "")
            victim = other_candidates[0]
            victim_id = victim.get("id")
            victim_source = victim.get("source") or "bot"

            if victim_id is not None:
                # 规则任务：交给 TelegramWorker 取消
                if victim_source == "rule" and worker is not None:
                    await worker.cancel_download(int(victim_id))
                # Bot 任务：交给 BotCommandHandler 处理
                elif bot_handler is not None:
                    await bot_handler.pause_download(int(victim_id))

                database.update_download(
                    int(victim_id),
                    status="paused",
                    error="被高优先级任务抢占",
                )

    return {"success": True, "priority": new_priority, "message": "已更新优先级"}


@api.delete("/downloads/{download_id}")
async def delete_download(
    download_id: int,
    delete_file: bool = Query(default=False, description="是否同时删除已下载的文件"),
) -> dict:
    """删除下载任务，可选择是否删除已下载文件"""
    downloads = database.list_downloads(limit=1000)
    download = next((d for d in downloads if d.get('id') == download_id), None)
    
    if not download:
        raise HTTPException(status_code=404, detail="下载记录不存在")
    # 如果正在下载，先取消（根据来源路由到相应下载管理器）
    if download.get('status') == 'downloading':
        source = download.get('source') or 'bot'
        if source == 'rule' and worker is not None:
            await worker.cancel_download(download_id)
        elif bot_handler is not None:
            await bot_handler.pause_download(download_id)
    
    # 删除文件（可选）
    if delete_file and download.get('file_path'):
        import os
        try:
            file_path = download['file_path']
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"已删除文件: {file_path}")
            else:
                logger.debug(f"文件不存在，跳过删除: {file_path}")
        except Exception as e:
            logger.warning(f"删除文件失败: {e}")

    # 如果有 Bot 回复消息，尝试更新为“已删除”
    reply_msg_id = download.get("reply_message_id")
    reply_chat_id = download.get("reply_chat_id")
    if bot_handler and reply_msg_id and reply_chat_id:
        try:
            if bot_handler._bot_client:
                await bot_handler._bot_client.edit_message(
                    reply_chat_id,
                    reply_msg_id,
                    "🗑️ 此下载任务已删除",
                )
        except Exception as exc:
            logger.debug("更新 Bot 消息为已删除失败: %s", exc)

    # 删除数据库记录
    database.delete_download(download_id)
    
    return {"success": True, "message": "已删除下载任务", "delete_file": delete_file}


@api.get("/fs/dirs")
async def list_dirs(
    base: str = Query(default="", description="相对根目录的路径，空字符串表示从容器根目录开始"),
    admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    """列出容器内的目录结构，用于保存路径选择。
    
    如果base为空，列出容器根目录（/）下的所有目录（排除/app）。
    如果base不为空，列出base路径下的子目录。
    """
    logger.debug(f"/fs/dirs 请求: base={base}, admin_token存在={admin_token is not None}, admin_token长度={len(admin_token) if admin_token else 0}")
    _require_admin(admin_token)
    
    # 容器根目录
    container_root = Path("/").resolve()
    app_dir = Path("/app").resolve()
    
    # 系统目录列表（需要排除的）
    system_dirs = {"bin", "boot", "dev", "etc", "lib", "lib32", "lib64", "libx32", "media", "mnt", "opt", 
                   "proc", "root", "run", "sbin", "srv", "sys", "tmp", "usr", "var", "home"}
    
    if not base:
        # 从容器根目录开始，只列出用户相关的目录（排除系统目录和/app）
        dirs: list[str] = []
        try:
            for item in container_root.iterdir():
                if item.is_dir() and item != app_dir and item.name not in system_dirs:
                    # 返回相对于容器根目录的路径（去掉开头的/）
                    rel_path = str(item.relative_to(container_root)).lstrip("/")
                    if rel_path:  # 确保不是空字符串
                        dirs.append(rel_path)
        except PermissionError:
            pass
        dirs = sorted(set(dirs))
        return {"items": dirs}
    else:
        # 确保路径在容器根目录内
        base_path = (container_root / base.lstrip("/")).resolve()
        if not str(base_path).startswith(str(container_root)):
            raise HTTPException(status_code=400, detail="路径不合法")
        
        if not base_path.exists() or not base_path.is_dir():
            return {"items": []}

        dirs: list[str] = []
        # 只列出base路径下的直接子目录（不递归，用于浏览）
        try:
            for item in base_path.iterdir():
                if item.is_dir():
                    full = item
                    try:
                        rel = str(full.relative_to(container_root)).lstrip("/")
                        if rel:  # 确保不是空字符串
                            dirs.append(rel)
                    except ValueError:
                        pass
        except PermissionError:
            pass

        dirs = sorted(set(dirs))
        return {"items": dirs}


@api.post("/fs/dirs")
async def create_dir(
    body: dict = Body(default={}),
    admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    """在下载目录下创建文件夹。"""
    _require_admin(admin_token)
    parent_path = body.get("parent_path", "") or ""
    name = body.get("name", "")
    if not name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="文件夹名称不合法")

    parent = _ensure_inside_download_dir(parent_path)
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / name
    target.mkdir(parents=True, exist_ok=True)

    rel = str(target.resolve().relative_to(Path(settings.download_dir).resolve()))
    return {"success": True, "path": rel}


@api.put("/fs/dirs/rename")
async def rename_dir(
    body: dict = Body(default={}),
    admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    """重命名下载目录下的文件夹。"""
    _require_admin(admin_token)
    path = body.get("path", "")
    new_name = body.get("new_name", "")
    if not path:
        raise HTTPException(status_code=400, detail="路径不能为空")
    if not new_name or "/" in new_name or "\\" in new_name:
        raise HTTPException(status_code=400, detail="新文件夹名称不合法")

    target = _ensure_inside_download_dir(path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="目录不存在")

    new_target = target.parent / new_name
    new_target = _ensure_inside_download_dir(str(new_target.relative_to(Path(settings.download_dir).resolve())))
    target.rename(new_target)

    rel = str(new_target.resolve().relative_to(Path(settings.download_dir).resolve()))
    return {"success": True, "path": rel}


@api.get("/messages")
async def list_messages() -> dict:
    return {"items": database.list_messages()}


@api.get("/dialogs")
async def list_dialogs() -> dict:
    try:
        items = await worker.list_dialogs()
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - 防御性
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"items": items}


@api.get("/config/default-download-path")
async def get_default_download_path(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    """获取默认下载路径"""
    _require_admin(x_admin_token)
    default_path = database.get_config("default_download_path")
    if not default_path:
        default_path = str(settings.download_dir)
        # 初始化默认路径到数据库
        database.set_config({"default_download_path": default_path})
    return {"path": default_path}


@api.get("/config/default-filename-template")
async def get_default_filename_template(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    """获取默认文件名模板"""
    _require_admin(x_admin_token)
    template = database.get_config("default_filename_template")
    if not template:
        template = "{task_id}_{file_name}"
        # 初始化默认模板到数据库
        database.set_config({"default_filename_template": template})
    return {"template": template}


@api.put("/config/default-filename-template")
async def update_default_filename_template(
    body: dict = Body(default={}),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    """更新默认文件名模板"""
    _require_admin(x_admin_token)

    template = (body or {}).get("template", "").strip()
    if not template:
        raise HTTPException(status_code=400, detail="文件名模板不能为空")

    database.set_config({"default_filename_template": template})
    return {"status": "updated", "template": template}


@api.put("/config/default-download-path")
async def update_default_download_path(
    body: dict = Body(default={}),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    """更新默认下载路径，支持自定义到任意挂载目录（如 /overwach）。"""
    _require_admin(x_admin_token)

    raw_path = (body or {}).get("path")
    if not raw_path or not str(raw_path).strip():
        raise HTTPException(status_code=400, detail="路径不能为空")

    path_str = str(raw_path).strip()
    # 统一转为绝对路径，避免相对路径导致下载到容器内部随机目录
    from pathlib import Path as _Path

    dl_path = _Path(path_str)
    if not dl_path.is_absolute():
        dl_path = _Path("/") / dl_path

    try:
        dl_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"无法创建目录: {exc}") from exc

    # 更新配置与运行时 settings
    abs_str = str(dl_path)
    database.set_config({"default_download_path": abs_str, "download_dir": abs_str})
    settings.download_dir = dl_path
    settings.ensure_directories()

    return {"status": "updated", "path": abs_str}


@api.get("/group-rules")
async def list_group_rules(chat_id: int | None = None, mode: str | None = None) -> dict:
    items = database.list_group_rules(chat_id=chat_id, mode=mode)
    return {"items": items}


@api.post("/group-rules")
async def create_group_rule(body: GroupRuleCreate) -> dict:
    # 解析体积范围字符串
    size_range = body.size_range or "0"
    min_size_bytes, max_size_bytes = database.parse_size_range(size_range)
    
    # 如果保存路径为空，使用默认下载路径
    save_dir = body.save_dir
    if not save_dir or save_dir.strip() == "":
        default_path = database.get_config("default_download_path")
        if not default_path:
            default_path = str(settings.download_dir)
        save_dir = default_path
    
    # 规范化保存路径为绝对路径
    if save_dir:
        save_path_obj = Path(save_dir)
        if not save_path_obj.is_absolute():
            save_path_obj = Path("/") / save_path_obj
        save_dir = str(save_path_obj)
    
    rule_id = database.add_group_rule(
        chat_id=body.chat_id,
        chat_title=body.chat_title,
        rule_name=body.rule_name,
        mode=body.mode,
        enabled=body.enabled,
        include_extensions=body.include_extensions,
        min_size_bytes=min_size_bytes,
        max_size_bytes=max_size_bytes,
        size_range=size_range,
        save_dir=save_dir,
        filename_template=body.filename_template,
        include_keywords=body.include_keywords,
        exclude_keywords=body.exclude_keywords,
        match_mode=body.match_mode,
        start_time=body.start_time.isoformat() if body.start_time else None,
        end_time=body.end_time.isoformat() if body.end_time else None,
        add_download_suffix=body.add_download_suffix,
        move_after_complete=body.move_after_complete,
        auto_catch_up=body.auto_catch_up,
    )
    rule = database.get_group_rule(rule_id)
    return {"id": rule_id, "rule": rule}


@api.put("/group-rules/{rule_id}")
async def update_group_rule(rule_id: int, body: GroupRuleUpdate) -> dict:
    # 解析体积范围字符串（如果提供）
    min_size_bytes = None
    max_size_bytes = None
    size_range = body.size_range
    
    if size_range is not None:
        min_size_bytes, max_size_bytes = database.parse_size_range(size_range)

    # 如果保存路径为空，使用默认下载路径
    save_dir = body.save_dir
    if save_dir is not None and (not save_dir or save_dir.strip() == ""):
        default_path = database.get_config("default_download_path")
        if not default_path:
            default_path = str(settings.download_dir)
        save_dir = default_path
    
    # 规范化保存路径为绝对路径
    if save_dir is not None and save_dir:
        save_path_obj = Path(save_dir)
        if not save_path_obj.is_absolute():
            save_path_obj = Path("/") / save_path_obj
        save_dir = str(save_path_obj)

    database.update_group_rule(
        rule_id,
        chat_title=body.chat_title,
        rule_name=body.rule_name,
        mode=body.mode,
        enabled=body.enabled,
        include_extensions=body.include_extensions,
        min_size_bytes=min_size_bytes,
        max_size_bytes=max_size_bytes,
        size_range=size_range,
        save_dir=save_dir,
        filename_template=body.filename_template,
        include_keywords=body.include_keywords,
        exclude_keywords=body.exclude_keywords,
        match_mode=body.match_mode,
        start_time=body.start_time.isoformat() if body.start_time else None,
        end_time=body.end_time.isoformat() if body.end_time else None,
        add_download_suffix=body.add_download_suffix,
        move_after_complete=body.move_after_complete,
        auto_catch_up=body.auto_catch_up,
    )
    rule = database.get_group_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    return {"rule": rule}


@api.delete("/group-rules/{rule_id}")
async def delete_group_rule(rule_id: int) -> dict:
    database.delete_group_rule(rule_id)
    return {"status": "deleted", "id": rule_id}


@api.post("/messages/test")
async def create_test_message(payload: dict = Body(default={})) -> dict:
    """发送一条测试消息到 Telegram 管理员账号，并在本地插入对应记录。

    行为：
    - 优先使用已启动的 BotCommandHandler 所在的 Bot 客户端发送消息；
    - 如果 Bot 尚未启动，则回退到用户账户客户端发送；
    - 发送成功后，将该消息写入 messages 表，便于前端展示；
    - 如果未配置管理员ID或发送失败，则返回明确的错误。
    """

    text = (payload or {}).get("text") or "这是来自后端的测试消息"

    # 解析管理员ID列表
    admin_ids = settings.admin_user_ids or []
    if isinstance(admin_ids, str):
        try:
            admin_ids = [
                int(x.strip()) for x in admin_ids.split(",") if x.strip() and x.strip().isdigit()
            ]
        except Exception:  # pragma: no cover - 防御性
            admin_ids = []

    if not admin_ids:
        raise HTTPException(status_code=400, detail="未配置管理员用户ID，无法发送测试消息")

    target_id = int(admin_ids[0])

    # 选择发送客户端：优先使用 Bot 客户端，其次使用用户账户客户端
    send_client = None
    is_bot_client = False

    global bot_handler
    if bot_handler is not None and getattr(bot_handler, "_bot_client", None) is not None:
        send_client = bot_handler._bot_client
        is_bot_client = True
    else:
        try:
            send_client = await worker._get_client()
        except Exception as exc:  # pragma: no cover - 防御性
            logger.warning("获取用户账户客户端失败，无法发送测试消息: %s", exc)
            raise HTTPException(status_code=500, detail=f"获取客户端失败: {exc}") from exc

    try:
        # 确保客户端已连接
        if not send_client.is_connected():
            await send_client.connect()

        # 发送测试消息到管理员
        sent_msg = await send_client.send_message(target_id, text)
    except Exception as exc:  # pragma: no cover - 防御性
        logger.warning("发送 Telegram 测试消息失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"发送 Telegram 测试消息失败: {exc}") from exc

    # 发送成功后，将该消息写入本地 messages 表，便于前端展示
    try:
        database.add_message(
            message_id=sent_msg.id or int(time.time()),
            chat_id=sent_msg.chat_id or 0,
            sender_id=getattr(sent_msg.sender, "id", 0) if getattr(sent_msg, "sender", None) else (bot_handler._bot_id if is_bot_client and bot_handler else 0),
            sender_username=getattr(sent_msg.sender, "username", None) if getattr(sent_msg, "sender", None) else (getattr(settings, "bot_username", None) if is_bot_client else None),
            sender_first_name=getattr(sent_msg.sender, "first_name", None) if getattr(sent_msg, "sender", None) else None,
            sender_last_name=getattr(sent_msg.sender, "last_name", None) if getattr(sent_msg, "sender", None) else None,
            message_text=text,
            has_media=False,
            media_type=None,
            file_name=None,
        )
    except Exception as exc:  # pragma: no cover - 防御性
        logger.warning("插入测试消息记录失败: %s", exc)
        # 发送已成功，这里仅记录日志，不再向上抛出

    return {
        "status": "ok",
        "message_id": sent_msg.id,
        "chat_id": sent_msg.chat_id,
        "via_bot": is_bot_client,
    }


@api.get("/status")
async def status() -> dict:
    downloads = database.list_downloads(limit=10)
    login_state = database.get_login_state()
    return {
        "bot_username": settings.bot_username,
        "download_count": len(downloads),
        "last_download": downloads[0] if downloads else None,
        "login_state": login_state,
    }


@api.get("/auth/status")
async def auth_status() -> dict:
    """获取登录状态"""
    login_state = database.get_login_state()
    if not login_state:
        return {"is_authorized": False, "account_type": None}
    
    return {
        "is_authorized": bool(login_state.get("is_authorized")),
        "account_type": login_state.get("account_type"),
        "user_id": login_state.get("user_id"),
        "username": login_state.get("username"),
        "first_name": login_state.get("first_name"),
        "last_name": login_state.get("last_name"),
        "phone_number": login_state.get("phone_number"),
        "last_login": login_state.get("last_login"),
    }


@api.get("/logs")
async def get_logs(limit: int = 50) -> dict:
    """获取应用日志
    
    注意：这是一个简单的实现，返回空日志列表。
    如果需要实际的日志功能，需要配置日志处理器将日志写入文件或数据库。
    """
    # 目前返回空列表，避免前端404错误
    # 未来可以实现从日志文件读取或从数据库读取
    return {"logs": []}

app = FastAPI(lifespan=lifespan)
app.mount("/api", api)

if settings.static_dir.exists():
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        # 前端路由兜底：非 /api 请求全部返回 index.html
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")

        candidate = (settings.static_dir / full_path).resolve()
        root = settings.static_dir.resolve()
        try:
            if candidate.is_file() and str(candidate).startswith(str(root)):
                return FileResponse(candidate)
        except Exception:
            pass

        index_file = settings.static_dir / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        raise HTTPException(status_code=404, detail="Not Found")

    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="frontend")
