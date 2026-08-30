import discord
from discord.ext import commands
from discord import app_commands
import json
import random
from datetime import datetime
from zoneinfo import ZoneInfo
from utils.steam import format_price

KST = ZoneInfo('Asia/Seoul')

class PlayCompleteView(discord.ui.View):
    def __init__(self, bot, session, game):
        super().__init__(timeout=None)
        self.bot = bot
        self.session = session
        self.game = game

    @discord.ui.button(
        label="플레이 완료",
        style=discord.ButtonStyle.success,
        custom_id="game:complete",
    )
    async def complete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Admin check
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("관리자만 사용할 수 있는 버튼입니다.", ephemeral=True)
            return

        # 게임·세션·기록을 하나의 멱등 트랜잭션으로 완료한다.
        archive_cog = self.bot.get_cog('ArchiveCog')
        if archive_cog:
            completed = await archive_cog.archive_session(
                interaction.guild_id, self.session, self.game
            )
            if completed:
                await interaction.response.send_message(
                    "플레이가 완료되어 기록이 저장되었습니다.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "이미 완료되었거나 현재 완료할 수 없는 모임입니다.",
                    ephemeral=True,
                )
                return
        else:
            await interaction.response.send_message("ArchiveCog를 찾을 수 없습니다.", ephemeral=True)
            return
            
        # Disable button
        button.disabled = True
        await interaction.message.edit(view=self)

class LotteryCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="게임선정", description="플레이할 게임을 추첨합니다.")
    async def draw_game(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        
        # 1. Load latest confirmed session
        session = await self.bot.db.get_latest_confirmed_session(guild_id)
        if not session:
            await interaction.response.send_message("확정된 모임 세션이 없습니다.", ephemeral=True)
            return
            
        attendees = await self.bot.db.get_attendees(session['id'])
        if not attendees:
            await interaction.response.send_message("확정된 세션에 참여자가 없습니다.", ephemeral=True)
            return

        # 2. Check for macOS users
        has_mac_user = False
        for att in attendees:
            user = await self.bot.db.get_user(att['user_id'], guild_id)
            if user and user.get('os_type') == 'macOS':
                has_mac_user = True
                break
                
        # 3. Get eligible games
        games = await self.bot.db.get_eligible_games(guild_id)
        if not games:
            await interaction.response.send_message("플레이 가능한(미플레이) 게임이 위시리스트에 없습니다.", ephemeral=True)
            return
            
        # 4. Calculate weights
        weights = []
        weight_breakdowns = []
        now = datetime.now(KST)
        
        for game in games:
            base = 10
            breakdown = ["기본 점수: +10"]
            
            # OS compatibility
            platforms = json.loads(game['platforms']) if game.get('platforms') else {}
            if has_mac_user and platforms.get('mac', False):
                base += 25
                breakdown.append("macOS 지원 (macOS 유저 포함): +25")
                
            # Price benefit
            if game.get('is_free'):
                base += 15
                breakdown.append("무료 게임: +15")
            else:
                price_overview = game.get('price_overview')
                price = json.loads(price_overview) if price_overview else {}
                if price.get('on_sale', False) or price.get('discount_percent', 0) > 0:
                    base += 15
                    breakdown.append("할인 중: +15")
                    
            # Wait time
            added_at = game.get('added_at')
            if added_at:
                added = datetime.fromisoformat(added_at)
                if added.tzinfo is None:
                    added = added.replace(tzinfo=KST)
                days = (now - added).days
                if days >= 30:
                    base += 20
                    breakdown.append(f"대기 30일 이상 ({days}일): +20")
                elif days >= 14:
                    base += 10
                    breakdown.append(f"대기 14일 이상 ({days}일): +10")
                    
            weights.append(base)
            weight_breakdowns.append((game['app_id'], base, breakdown))
            
        # 5. Weighted random selection
        selected_game = random.choices(games, weights=weights, k=1)[0]
        
        # 6. Update game status + 세션에 게임 연결
        await self.bot.db.update_game_status(guild_id, selected_game['app_id'], '진행 중')
        await self.bot.db.set_session_game(session['id'], selected_game['app_id'])
        
        # Find breakdown for selected game
        selected_breakdown = next(b for b in weight_breakdowns if b[0] == selected_game['app_id'])
        
        # 7. Output embed card
        embed = discord.Embed(
            title="🎲 게임 선정 결과!",
            description=f"**[{selected_game['title']}](https://store.steampowered.com/app/{selected_game['app_id']})**",
            color=discord.Color.gold()
        )
        if selected_game.get('header_image'):
            embed.set_image(url=selected_game['header_image'])
            
        embed.add_field(name="총 가중치", value=f"{selected_breakdown[1]}점", inline=False)
        embed.add_field(name="가중치 상세", value="\n".join(selected_breakdown[2]), inline=False)
        
        view = PlayCompleteView(self.bot, session, selected_game)
        await interaction.response.send_message(embed=embed, view=view)

    # ────────────────── 게임 수동 지정 ──────────────────

    @app_commands.command(
        name="게임선정수동",
        description="[관리자] 위시리스트에서 게임을 직접 지정합니다.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.describe(게임명="지정할 게임 이름 (일부 입력 가능)")
    async def manual_draw(self, interaction: discord.Interaction, 게임명: str):
        guild_id = interaction.guild_id

        # 확정 세션 확인
        session = await self.bot.db.get_latest_confirmed_session(guild_id)
        if not session:
            await interaction.response.send_message(
                "⚠️ 확정된 모임 세션이 없습니다. 먼저 일정을 확정해주세요.",
                ephemeral=True,
            )
            return

        # 미플레이 게임 검색
        games = await self.bot.db.get_eligible_games(guild_id)
        matches = [
            g for g in games
            if 게임명.lower() in g.get("title", "").lower()
        ]

        if not matches:
            await interaction.response.send_message(
                f"❌ '{게임명}'과(와) 일치하는 미플레이 게임을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        if len(matches) > 1:
            titles = "\n".join(f"- {g['title']}" for g in matches[:10])
            await interaction.response.send_message(
                f"⚠️ 여러 게임이 검색되었습니다. 좀 더 정확한 이름을 입력해주세요:\n{titles}",
                ephemeral=True,
            )
            return

        selected_game = matches[0]

        # 상태 변경 + 세션 연결
        await self.bot.db.update_game_status(
            guild_id, selected_game["app_id"], "진행 중"
        )
        await self.bot.db.set_session_game(
            session["id"], selected_game["app_id"]
        )

        # 임베드 카드
        embed = discord.Embed(
            title="🎯 게임 수동 선정!",
            description=(
                f"**[{selected_game['title']}]"
                f"(https://store.steampowered.com/app/{selected_game['app_id']})**"
            ),
            color=discord.Color.gold(),
        )
        if selected_game.get("header_image"):
            embed.set_image(url=selected_game["header_image"])

        price_overview = selected_game.get("price_overview")
        if isinstance(price_overview, str) and price_overview:
            try:
                price_overview = json.loads(price_overview)
            except json.JSONDecodeError:
                price_overview = None
        price_text = format_price(
            price_overview, selected_game.get("is_free", False)
        )
        embed.add_field(name="가격", value=price_text, inline=True)
        embed.set_footer(text="관리자에 의해 직접 선정되었습니다.")

        view = PlayCompleteView(self.bot, session, selected_game)
        await interaction.response.send_message(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(LotteryCog(bot))

