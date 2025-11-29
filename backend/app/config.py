from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional


def _read_version() -> str:
    """读取版本号，优先从上级目录中的 VERSION 文件获取。"""
    config_path = Path(__file__).resolve()
    # 从当前文件所在目录一路向上查找 VERSION 文件，兼容源码运行和 Docker 容器内的路径结构
    for parent in config_path.parents:
        candidate = parent / "VERSION"
        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8").strip()
            except OSError:
                continue
    # 找不到 VERSION 时返回一个开发标记
    return "dev"


@dataclass
class Settings:
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    phone_number: Optional[str] = None
    bot_token: Optional[str] = None
    bot_username: Optional[str] = None
    admin_user_ids: list[int] = field(default_factory=list)  # 管理员用户ID列表
    proxy_type: Optional[str] = None  # 'http', 'socks4', 'socks5'
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_user: Optional[str] = None
    proxy_password: Optional[str] = None
    download_dir: Path = field(default_factory=lambda: Path("./downloads"))
    data_dir: Path = field(default_factory=lambda: Path("./data"))
    static_dir: Path = field(default_factory=lambda: Path("./app/static"))
    database_url: str = "sqlite:///./data/state.db"
    version: str = field(default_factory=_read_version)

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.static_dir.mkdir(parents=True, exist_ok=True)

    def load_from_mapping(self, config: Dict[str, Any]) -> None:
        if not config:
            return
        if "api_id" in config and config["api_id"]:
            try:
                self.api_id = int(config["api_id"])
            except (TypeError, ValueError):
                self.api_id = None
        self.api_hash = config.get("api_hash") or self.api_hash
        self.phone_number = config.get("phone_number") or self.phone_number
        self.bot_token = config.get("bot_token") or self.bot_token
        self.bot_username = config.get("bot_username") or self.bot_username
        
        # 解析管理员ID列表（支持逗号分隔的字符串）
        admin_ids_str = config.get("admin_user_ids", "")
        if admin_ids_str:
            try:
                self.admin_user_ids = [int(x.strip()) for x in str(admin_ids_str).split(",") if x.strip() and x.strip().isdigit()]
            except (TypeError, ValueError):
                self.admin_user_ids = []
        else:
            self.admin_user_ids = []
        
        self.proxy_type = config.get("proxy_type") or self.proxy_type or "http"
        self.proxy_host = config.get("proxy_host") or self.proxy_host
        proxy_port = config.get("proxy_port")
        if proxy_port:
            try:
                self.proxy_port = int(proxy_port)
            except (TypeError, ValueError):
                self.proxy_port = None
        self.proxy_user = config.get("proxy_user") or self.proxy_user
        self.proxy_password = config.get("proxy_password") or self.proxy_password


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings

