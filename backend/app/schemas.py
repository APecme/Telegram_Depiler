from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ProxySettings(BaseModel):
    type: Optional[str] = "http"  # 'http', 'socks4', 'socks5'
    host: str
    port: int
    user: Optional[str] = None
    password: Optional[str] = None


class ConfigPayload(BaseModel):
    api_id: int
    api_hash: str
    phone_number: str
    bot_token: Optional[str] = None
    bot_username: str
    admin_user_ids: Optional[str] = None  # 逗号分隔的管理员ID字符串
    proxy: Optional[ProxySettings] = None


class SendCodeRequest(BaseModel):
    phone_number: str
    force: bool = False


class VerifyCodeRequest(BaseModel):
    phone_number: str
    code: str


class SubmitPasswordRequest(BaseModel):
    phone_number: str
    password: str


class VerifyRequest(BaseModel):
    """统一的验证请求，支持验证码和密码"""
    phone_number: str
    step: Literal["code", "password"] = "code"
    code: Optional[str] = None
    password: Optional[str] = None


class StartBotRequest(BaseModel):
    bot_username: str
    download_existing: bool = False


class RestartRequest(BaseModel):
    reset_session: bool = True


class DownloadRecord(BaseModel):
    id: int
    message_id: int
    chat_id: int
    bot_username: str
    file_name: str
    file_path: str
    status: Literal["pending", "queued", "downloading", "paused", "completed", "failed", "cancelled"]
    progress: float
    download_speed: Optional[float] = None  # 下载速度（字节/秒）
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class GroupRuleCreate(BaseModel):
    chat_id: int = Field(..., description="群聊ID")
    chat_title: Optional[str] = None
    rule_name: Optional[str] = Field(default=None, description="规则名称（可自定义）")
    mode: Literal["monitor", "history"] = "monitor"
    enabled: bool = True
    include_extensions: Optional[str] = None  # 逗号分隔的扩展名，如 mp4,mp3,jpg
    size_range: Optional[str] = "0"  # 体积范围，格式: "0" / "10" / "10-100"
    save_dir: Optional[str] = None  # 保存目录
    filename_template: Optional[str] = None  # 文件名模板，例如 "{task_id}_{message_id}_{chat_title}"
    include_keywords: Optional[str] = None  # 逗号分隔的包含关键词
    exclude_keywords: Optional[str] = None  # 逗号分隔的排除关键词
    match_mode: Literal["all", "include", "exclude"] = "all"
    start_time: Optional[datetime] = None  # 仅 history 模式使用
    end_time: Optional[datetime] = None  # 仅 history 模式使用
    min_message_id: Optional[int] = None  # 仅 history 模式使用：消息ID起始
    max_message_id: Optional[int] = None  # 仅 history 模式使用：消息ID结束
    add_download_suffix: bool = False  # 是否为未完成文件添加.download后缀
    move_after_complete: bool = False  # 下载完成后再移动到目标目录


class GroupRuleUpdate(BaseModel):
    chat_title: Optional[str] = None
    rule_name: Optional[str] = None
    mode: Optional[Literal["monitor", "history"]] = None
    enabled: Optional[bool] = None
    include_extensions: Optional[str] = None
    size_range: Optional[str] = None  # 体积范围，格式: "0" / "10" / "10-100"
    save_dir: Optional[str] = None
    filename_template: Optional[str] = None
    include_keywords: Optional[str] = None
    exclude_keywords: Optional[str] = None
    match_mode: Optional[Literal["all", "include", "exclude"]] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    min_message_id: Optional[int] = None
    max_message_id: Optional[int] = None
    add_download_suffix: Optional[bool] = None  # 是否为未完成文件添加.download后缀
    move_after_complete: Optional[bool] = None  # 下载完成后再移动到目标目录


class AdminLoginRequest(BaseModel):
    """面板登录请求"""
    username: str
    password: str


class AdminCredentialsUpdate(BaseModel):
    """面板账号密码修改请求"""
    username: Optional[str] = None
    password: Optional[str] = None
