"""Persistent settings for the single administrator and managed NapCat instance."""
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
import re

from .config import positive_id


def permissions(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {"allowed_users", "allowed_groups", "group_allow_all"}:
        raise ValueError("请完整填写群聊和用户权限设置")
    result = {}
    for key in ("allowed_users", "allowed_groups"):
        items = value[key]
        if not isinstance(items, list) or len(items) > 10000:
            raise ValueError("白名单必须是 QQ 号或群号列表")
        result[key] = sorted({positive_id(item) for item in items}, key=int)
    if type(value["group_allow_all"]) is not bool:
        raise ValueError("群成员权限必须选择全员可用或仅名单用户")
    result["group_allow_all"] = value["group_allow_all"]
    return result


def atomic_json(path: Path, value: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".jm-bot-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class StateStore:
    def __init__(self, directory: Path, env=None):
        self.path = directory / "config.json"
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        if self.path.exists():
            try:
                state = json.loads(self.path.read_text(encoding="utf-8"))
                if type(state["schema_version"]) is not int or state["schema_version"] != 1:
                    raise ValueError
                for key in ("onebot_token", "napcat_token"):
                    if not isinstance(state[key], str) or re.fullmatch(r"[A-Za-z0-9_-]{32,}", state[key]) is None:
                        raise ValueError
                state["permissions"] = permissions(state["permissions"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("data/config.json 无效；请从备份恢复，程序不会覆盖已有配置") from None
        else:
            env = os.environ if env is None else env
            mode = env.get("GROUP_ALLOW_ALL", "true").strip().lower()
            if mode not in ("true", "false"):
                raise ValueError("GROUP_ALLOW_ALL 只能是 true 或 false")
            state = {
                "schema_version": 1,
                "onebot_token": secrets.token_urlsafe(32),
                "napcat_token": secrets.token_urlsafe(32),
                "permissions": permissions({
                    "allowed_users": [x.strip() for x in env.get("ALLOWED_USERS", "").split(",") if x.strip()],
                    "allowed_groups": [x.strip() for x in env.get("ALLOWED_GROUPS", "").split(",") if x.strip()],
                    "group_allow_all": mode == "true",
                }),
            }
            atomic_json(self.path, state)
        self.state = state

    def get_permissions(self) -> dict:
        return permissions(self.state["permissions"])

    def save_permissions(self, value: dict) -> dict:
        value = permissions(value)
        updated = {**self.state, "permissions": value}
        atomic_json(self.path, updated)
        self.state = updated
        return self.get_permissions()

    def prepare_napcat(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        if os.name == "posix" and os.geteuid() == 0:
            os.chown(directory, 1000, 1000)
        path = directory / "webui.json"
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(previous, dict):
            raise ValueError("napcat-config/webui.json 无效；请先恢复备份")
        if previous.get("enable2FA"):
            raise ValueError("本项目 NapCat 已开启二次验证，暂不能自动接管；请先在旧管理页关闭二次验证")
        updated = {**previous, "host": "0.0.0.0", "port": 6099,
                   "token": self.state["napcat_token"], "disableWebUI": False}
        if previous != updated:
            backup = directory / "webui.json.before-jm-bot"
            if path.exists() and not backup.exists():
                shutil.copy2(path, backup)
                backup.chmod(0o600)
            atomic_json(path, updated)
        path.chmod(0o600)
        if os.name == "posix" and os.geteuid() == 0:
            os.chown(path, 1000, 1000)
