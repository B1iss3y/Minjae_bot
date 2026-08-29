"""
Minjae_bot — 멀티게임 크루 일정·추첨 디스코드 봇
진입점: Bot 초기화, DB 연결, Cog 로드
"""

import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import os

from db.manager import DatabaseManager

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

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


class MinjaeBot(commands.Bot):
    """연결 재개와 무관하게 초기화가 한 번만 수행되는 Bot."""

    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.db = DatabaseManager()

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
            synced = await self.tree.sync()
            print(f"✅ {len(synced)}개 슬래시 커맨드 동기화 완료")
        except Exception as exc:
            print(f"❌ 커맨드 동기화 실패: {exc}")

    async def close(self):
        """백그라운드 마감 작업과 DB 연결을 정리한다."""

        vote_cog = self.get_cog("VoteCog")
        if vote_cog:
            await vote_cog.shutdown()
        await self.db.close()
        await super().close()


bot = MinjaeBot()


@bot.event
async def on_ready():
    """재연결 때는 상태를 다시 초기화하지 않고 접속 정보만 알린다."""
    print(f"🤖 {bot.user.name} 온라인! (서버 {len(bot.guilds)}개)")


@bot.event
async def on_guild_join(guild: discord.Guild):
    """새 서버 참가 시 기본 설정 생성"""
    await bot.db.get_or_create_config(guild.id)
    print(f"📥 새 서버 참가: {guild.name} (ID: {guild.id})")


async def main():
    if not TOKEN:
        print("❌ DISCORD_TOKEN이 .env 파일에 설정되지 않았습니다.")
        print("   .env.example을 참고하여 .env 파일을 생성해주세요.")
        return

    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
