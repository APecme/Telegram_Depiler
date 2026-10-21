from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class ProxySettings(BaseModel):
    type: Optional[str] = "http"  # 'http', 'socks4', 'socks5'
    host: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    password: Optional[str] = None


class ProxyTestRequest(BaseModel):
    proxy: ProxySettings


class ConfigPayload(BaseModel):
    api_id: int
    api_hash: str
    phone_number: Optional[str] = None
    bot_token: Optional[str] = None
    bot_username: Optional[str] = None
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


class QuotaLimitIn(BaseModel):
    """一条下载容量限额配置（前端传"数值 + 单位"，也可直接传 bytes）。"""

    id: Optional[int] = Field(default=None, description="已有限额的ID；不传表示新增")
    period: Literal["hour", "day", "week", "month", "custom"] = Field(
        default="day", description="自然周期：小时/天/周/月，或 custom 自定义"
    )
    custom_value: Optional[int] = Field(default=None, ge=1, le=100000, description="custom 周期的 N")
    custom_unit: Optional[Literal["hours", "days"]] = Field(
        default=None, description="custom 周期的单位：小时或天"
    )
    limit_value: Optional[float] = Field(default=None, gt=0, le=1024, description="限额数值")
    limit_unit: Optional[Literal["MB", "GB", "TB"]] = Field(default="GB", description="限额单位（1024 进制）")
    limit_bytes: Optional[int] = Field(default=None, gt=0, description="限额（字节），优先于 value/unit")

    @model_validator(mode="after")
    def _validate_limit(self) -> "QuotaLimitIn":
        if self.limit_bytes is None and self.limit_value is None:
            raise ValueError("请填写限额容量")
        if self.period == "custom":
            if not self.custom_value:
                raise ValueError("自定义限额必须填写间隔小时数或天数")
            if self.custom_unit not in ("hours", "days"):
                raise ValueError("自定义限额必须选择小时或天")
        return self


class QuotaPayload(BaseModel):
    """规则的下载限额配置。limits 为空表示不限额。"""

    enabled: bool = False
    limits: list[QuotaLimitIn] = Field(default_factory=list)


class GroupRuleCreate(BaseModel):
    chat_id: int = Field(..., description="群聊ID")
    chat_title: Optional[str] = None
    rule_name: Optional[str] = Field(default=None, description="规则名称（可自定义）")
    mode: Literal["monitor", "history"] = "monitor"
    content_type: Literal["media", "message_text"] = "media"
    text_format: Literal["txt", "json"] = "txt"
    text_merge: bool = False
    enabled: bool = True
    text_preprocess: Optional[str] = None
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
    auto_catch_up: bool = False  # 启动时自动回补遗漏消息
    include_comments: bool = False  # 是否包含评论/回复/讨论组内容
    quota_enabled: bool = False  # 是否开启下载容量限额（默认关闭，兼容老配置）
    quota_limits: Optional[list[QuotaLimitIn]] = None  # 限额条目（可多条，全部满足才准入）


class GroupRuleUpdate(BaseModel):
    chat_title: Optional[str] = None
    rule_name: Optional[str] = None
    mode: Optional[Literal["monitor", "history"]] = None
    content_type: Optional[Literal["media", "message_text"]] = None
    text_format: Optional[Literal["txt", "json"]] = None
    text_merge: Optional[bool] = None
    enabled: Optional[bool] = None
    include_extensions: Optional[str] = None
    text_preprocess: Optional[str] = None
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
    auto_catch_up: Optional[bool] = None
    include_comments: Optional[bool] = None
    quota_enabled: Optional[bool] = None  # 不传表示保持现值
    quota_limits: Optional[list[QuotaLimitIn]] = None  # 不传保持现值；传 [] 表示清除限额


class AdminLoginRequest(BaseModel):
    """面板登录请求"""
    username: str
    password: str


class AdminCredentialsUpdate(BaseModel):
    """面板账号密码修改请求"""
    username: Optional[str] = None
    password: Optional[str] = None
