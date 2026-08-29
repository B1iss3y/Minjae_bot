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

bot = commands.Bot(command_prefix="!", intents=intents)
bot.db = DatabaseManager()

# 로드할 Cog 목록
COG_EXTENSIONS = [
    "cogs.config",
    "cogs.user",
    "cogs.wishlist",
    "cogs.vote",
    "cogs.lottery",
    "cogs.archive",
]


@bot.event
async def on_ready():
    """봇 준비 완료 시 DB 초기화 및 슬래시 커맨드 동기화"""
    await bot.db.init()
    print(f"✅ DB 초기화 완료")

    # Cog 로드
    for ext in COG_EXTENSIONS:
        try:
            await bot.load_extension(ext)
            print(f"  ✅ {ext} 로드 완료")
        except Exception as e:
            print(f"  ❌ {ext} 로드 실패: {e}")

    # 슬래시 커맨드 동기화
    try:
        synced = await bot.tree.sync()
        print(f"✅ {len(synced)}개 슬래시 커맨드 동기화 완료")
    except Exception as e:
        print(f"❌ 커맨드 동기화 실패: {e}")

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
