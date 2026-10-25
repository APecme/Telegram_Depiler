"""解析 t.me 消息链接，供 Bot 链接下载使用。

支持的形态：

* ``https://t.me/c/<内部ID>/<消息ID>`` —— 私有频道/超级群；内部 ID 加上 ``-100``
  前缀即为 Telegram 的 chat_id。
* ``https://t.me/<用户名>/<消息ID>`` —— 公开频道/群组，按用户名解析。
* 可以没有 scheme，也可以是 ``telegram.me`` / ``www.`` 前缀。
* 查询参数（``?thread=<话题ID>``、``?single``、``?comment=``）与下载无关：路径里的
  消息 ID 已经是要下载的那条消息，因此直接忽略。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Union

#: 私有频道/超级群的 chat_id 前缀。
CHANNEL_ID_PREFIX = -100

TELEGRAM_LINK_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/"
    r"(?:c/(?P<internal_id>\d+)|(?P<username>[A-Za-z0-9_]{4,}))"
    r"/(?P<message_id>\d+)"
)


@dataclass(frozen=True)
class TelegramLink:
    """一条指向具体消息的 Telegram 链接。"""

    #: 私有链接是标记过的 chat_id（负数），公开链接是用户名。
    chat_ref: Union[int, str]
    message_id: int


def parse_telegram_link(text: Optional[str]) -> Optional[TelegramLink]:
    """从文本里取出第一条可下载的消息链接，找不到时返回 ``None``。"""
    if not text:
        return None
    match = TELEGRAM_LINK_PATTERN.search(text)
    if not match:
        return None
    message_id = int(match.group("message_id"))
    if message_id <= 0:
        return None
    internal_id = match.group("internal_id")
    if internal_id is not None:
        chat_ref: Union[int, str] = int(f"{CHANNEL_ID_PREFIX}{internal_id}")
    else:
        chat_ref = match.group("username")
    return TelegramLink(chat_ref=chat_ref, message_id=message_id)
