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

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.environ.get("ONEBOT_TOKEN", "").strip()
        if not token or token.startswith("REPLACE_") or "\r" in token or "\n" in token:
            raise ValueError("请在 Compose 中设置自己的 ONEBOT_TOKEN，并在 NapCat 中填写相同值")
        url = os.environ.get("ONEBOT_WS_URL", "ws://napcat:3001/").strip()
        try:
            parsed = urlsplit(url)
            parsed.port
        except ValueError:
            raise ValueError("ONEBOT_WS_URL 地址或端口格式错误") from None
        if parsed.scheme not in ("ws", "wss") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("ONEBOT_WS_URL 必须是 ws/wss 地址，口令只通过 ONEBOT_TOKEN 设置")

        def ids(key: str) -> frozenset[str]:
            value = os.environ.get(key, "").strip()
            if not value:
                return frozenset()
            try:
                return frozenset(positive_id(item) for item in value.split(","))
            except ValueError:
                raise ValueError(f"{key} 只能填写逗号分隔的正整数 QQ 号/群号") from None

        return cls(url, token, ids("ALLOWED_USERS"), ids("ALLOWED_GROUPS"),
                   Path(os.environ.get("DOWNLOAD_DIR", "/downloads")).resolve())
