from dataclasses import dataclass
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


def positive_id(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("ID 必须是正整数")
    value = str(value).strip()
    if re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError("ID 必须是正整数")
    number = int(value)
    if number <= 0:
        raise ValueError("ID 必须是正整数")
    return str(number)


@dataclass(frozen=True)
class Settings:
    ws_url: str = "ws://napcat:3001/"
    token: str = ""
    allowed_users: frozenset[str] = frozenset()
    allowed_groups: frozenset[str] = frozenset()
    download_dir: Path = Path("/downloads")
    group_allow_all: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.environ.get("ONEBOT_TOKEN", "").strip()
        if not token:
            raise ValueError("ONEBOT_TOKEN 未设置或为空；请填写 jm-bot.environment 中的值并重新创建容器")
        if token.startswith("REPLACE_"):
            raise ValueError("ONEBOT_TOKEN 仍是示例占位符；请替换为实际口令并重新创建容器")
        if "\r" in token or "\n" in token:
            raise ValueError("ONEBOT_TOKEN 含有换行；请使用单行口令并重新创建容器")
        url = os.environ.get("ONEBOT_WS_URL", "ws://napcat:3001/").strip()
        try:
            parsed = urlsplit(url)
            parsed.port
        except ValueError:
            raise ValueError("ONEBOT_WS_URL 地址或端口格式错误") from None
        if parsed.scheme not in ("ws", "wss") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("ONEBOT_WS_URL 必须是 ws/wss 地址，口令只通过 ONEBOT_TOKEN 设置")
        group_mode = os.environ.get("GROUP_ALLOW_ALL", "false").strip().lower()
        if group_mode not in ("true", "false"):
            raise ValueError("GROUP_ALLOW_ALL 只能填写 true 或 false")

        def ids(key: str) -> frozenset[str]:
            value = os.environ.get(key, "").strip()
            if not value:
                return frozenset()
            try:
                return frozenset(positive_id(item) for item in value.split(","))
            except ValueError:
                raise ValueError(f"{key} 只能填写逗号分隔的正整数 QQ 号/群号") from None

        return cls(url, token, ids("ALLOWED_USERS"), ids("ALLOWED_GROUPS"),
                   Path(os.environ.get("DOWNLOAD_DIR", "/downloads")).resolve(),
                   group_allow_all=group_mode == "true")
