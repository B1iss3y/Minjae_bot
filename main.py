"""
Minjae_bot — 멀티게임 크루 일정·추첨 디스코드 봇
진입점: Bot 초기화, DB 연결, Cog 로드
"""

import asyncio
import discord
from discord import app_commands
from discord.ext import commands

from db.manager import DatabaseManager
from utils.runtime import RuntimeConfig, load_runtime_config

RUNTIME_CONFIG = load_runtime_config()
TOKEN = RUNTIME_CONFIG.token

# ──────────────────────── Bot 설정 ────────────────────────

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

# 로드할 Cog 목록
COG_EXTENSIONS = [
    "cogs.config",
    "cogs.user",
    "cogs.wishlist",
    "cogs.vote",
    "cogs.lottery",
    "cogs.archive",
]


class RestrictedCommandTree(app_commands.CommandTree):
    """설정된 한 서버에서만 슬래시 명령 실행을 허용한다."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        allowed_guild_id = self.client.allowed_guild_id
        if allowed_guild_id is None or interaction.guild_id == allowed_guild_id:
            return True

        message = "이 봇은 지정된 서버에서만 사용할 수 있습니다."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return False


class MinjaeBot(commands.Bot):
    """연결 재개와 무관하게 초기화가 한 번만 수행되는 Bot."""

    def __init__(self, runtime_config: RuntimeConfig):
        self.runtime_config = runtime_config
        self.allowed_guild_id = runtime_config.allowed_guild_id
        super().__init__(
            command_prefix="!",
            intents=intents,
            tree_cls=RestrictedCommandTree,
        )
        self.db = DatabaseManager(runtime_config.database_path)

    def is_allowed_guild(self, guild_id: int | None) -> bool:
        return self.allowed_guild_id is None or guild_id == self.allowed_guild_id

    async def setup_hook(self):
        """Discord 연결 전에 DB, Cog, persistent View를 준비한다."""

        await self.db.init()
        print("✅ DB 초기화 완료")

        for ext in COG_EXTENSIONS:
            try:
                await self.load_extension(ext)
                print(f"  ✅ {ext} 로드 완료")
            except Exception as exc:
                print(f"  ❌ {ext} 로드 실패: {exc}")

        try:
            if self.allowed_guild_id is not None:
                guild = discord.Object(id=self.allowed_guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                sync_target = f"서버 {self.allowed_guild_id}"
            else:
                synced = await self.tree.sync()
                sync_target = "전역"
            print(
                f"✅ {len(synced)}개 슬래시 커맨드 동기화 완료 "
                f"({sync_target})"
            )
        except Exception as exc:
            print(f"❌ 커맨드 동기화 실패: {exc}")

    async def close(self):
        """백그라운드 마감 작업과 DB 연결을 정리한다."""

        vote_cog = self.get_cog("VoteCog")
        if vote_cog:
            await vote_cog.shutdown()
        await self.db.close()
        await super().close()


bot = MinjaeBot(RUNTIME_CONFIG)


@bot.event
async def on_ready():
    """재연결 때는 상태를 다시 초기화하지 않고 접속 정보만 알린다."""
    print(
        f"🤖 {bot.user.name} 온라인! "
        f"(환경: {bot.runtime_config.environment}, 서버 {len(bot.guilds)}개, "
        f"DB: {bot.runtime_config.database_path})"
    )
    unexpected = [guild for guild in bot.guilds if not bot.is_allowed_guild(guild.id)]
    for guild in unexpected:
        print(f"⚠️ 허용되지 않은 서버 연결 감지: {guild.name} ({guild.id})")


@bot.event
async def on_guild_join(guild: discord.Guild):
    """새 서버 참가 시 기본 설정 생성"""
    if not bot.is_allowed_guild(guild.id):
        print(f"⚠️ 허용되지 않은 서버 참가 무시: {guild.name} ({guild.id})")
        return
    await bot.db.get_or_create_config(guild.id)
    print(f"📥 새 서버 참가: {guild.name} (ID: {guild.id})")


async def main():
    if not TOKEN:
        print(
            f"❌ DISCORD_TOKEN이 {RUNTIME_CONFIG.env_file} 파일에 "
            "설정되지 않았습니다."
        )
        print("   환경별 example 파일을 복사해 토큰과 서버 ID를 입력해주세요.")
        return

    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
