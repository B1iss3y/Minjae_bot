"""운영/테스트 봇 실행 환경을 안전하게 분리하는 설정 로더."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class RuntimeConfig:
    environment: str
    env_file: str
    token: str | None
    database_path: str
    allowed_guild_id: int | None


def parse_guild_id(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        guild_id = int(value)
    except ValueError as exc:
        raise ValueError("ALLOWED_GUILD_ID는 숫자여야 합니다.") from exc
    if guild_id <= 0:
        raise ValueError("ALLOWED_GUILD_ID는 양수여야 합니다.")
    return guild_id


def load_runtime_config() -> RuntimeConfig:
    """BOT_ENV_FILE이 가리키는 dotenv 파일에서 실행 설정을 읽는다."""

    env_file = os.getenv("BOT_ENV_FILE", ".env")
    load_dotenv(dotenv_path=env_file, override=True)
    environment = os.getenv("BOT_ENVIRONMENT", "default").strip() or "default"
    database_path = os.getenv("DB_PATH", "minjae_bot.db").strip()
    if not database_path:
        raise ValueError("DB_PATH는 비워둘 수 없습니다.")

    return RuntimeConfig(
        environment=environment,
        env_file=env_file,
        token=os.getenv("DISCORD_TOKEN"),
        database_path=database_path,
        allowed_guild_id=parse_guild_id(os.getenv("ALLOWED_GUILD_ID")),
    )
