import discord
from discord.ext import commands
from discord import app_commands
import json
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')

class ArchiveCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def archive_session(self, guild_id: int, session: dict, game: dict):
        completed = await self.bot.db.complete_session(
            session['id'], game['app_id'], game['title']
        )
        if completed is None:
            return False

        session = completed['session']
        attendee_ids = completed['attendee_ids']
        
        # Send celebration embed
        channel = self.bot.get_channel(session['channel_id'])
        if channel:
            mentions = " ".join(f"<@{user_id}>" for user_id in attendee_ids)
            
            embed = discord.Embed(
                title=f"🎉 제{session['session_number']}회 모임 플레이 완료!",
                color=discord.Color.green()
            )
            embed.add_field(name="플레이 게임", value=game['title'], inline=False)
            embed.add_field(name="참여자", value=mentions if mentions else "없음", inline=False)
            
            # Formatting confirmed datetime
            dt = (
                datetime.fromisoformat(session['confirmed_datetime'])
                if session.get('confirmed_datetime')
                else datetime.now(KST)
            )
            embed.add_field(name="확정 일시", value=dt.strftime("%Y-%m-%d %H:%M"), inline=False)
            
            if game.get('header_image'):
                embed.set_image(url=game['header_image'])
                
            await channel.send(embed=embed)
        return True

    @app_commands.command(name="모임기록", description="이전 모임 기록을 확인합니다.")
    @app_commands.describe(session_number="확인할 모임의 회차 (선택사항)")
    async def meeting_history(self, interaction: discord.Interaction, session_number: int = None):
        guild_id = interaction.guild_id
        
        if session_number is None:
            # Show list of past meetings
            history = await self.bot.db.get_meeting_history(guild_id)
            if not history:
                await interaction.response.send_message("기록된 모임이 없습니다.", ephemeral=True)
                return
                
            embed = discord.Embed(title="📚 지난 모임 기록", color=discord.Color.blue())
            
            # Showing top 10 recent meetings or all
            for record in history[:10]:
                title = record.get('game_title', '알 수 없는 게임')
                embed.add_field(
                    name=f"제{record['session_number']}회 모임",
                    value=f"게임: {title}\n일시: {record['confirmed_datetime'][:10]}",
                    inline=False
                )
            
            if len(history) > 10:
                embed.set_footer(text="최근 10개의 기록만 표시됩니다. 특정 회차를 보려면 회차를 입력하세요.")
                
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            # Show specific meeting
            history = await self.bot.db.get_meeting_history(guild_id, session_number=session_number)
            if not history:
                await interaction.response.send_message(f"제{session_number}회 모임 기록을 찾을 수 없습니다.", ephemeral=True)
                return
                
            record = history[0] if isinstance(history, list) else history
            
            embed = discord.Embed(
                title=f"제{record['session_number']}회 모임 기록",
                color=discord.Color.blue()
            )
            embed.add_field(name="플레이 게임", value=record.get('game_title', '알 수 없음'), inline=False)
            
            attendees_list = json.loads(record['attendees']) if record.get('attendees') else []
            mentions = " ".join([f"<@{uid}>" for uid in attendees_list])
            embed.add_field(name="참여자", value=mentions if mentions else "없음", inline=False)
            
            dt = datetime.fromisoformat(record['confirmed_datetime']) if record.get('confirmed_datetime') else None
            dt_str = dt.strftime("%Y-%m-%d %H:%M") if dt else "알 수 없음"
            embed.add_field(name="확정 일시", value=dt_str, inline=False)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="모임기록삭제", description="[관리자] 지정한 회차의 모임 기록을 삭제합니다.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(회차번호="삭제할 모임의 회차 번호")
    async def delete_meeting_history(self, interaction: discord.Interaction, 회차번호: int):
        guild_id = interaction.guild_id
        await interaction.response.defer()

        deleted_record = await self.bot.db.delete_meeting_history(guild_id, 회차번호)
        
        if not deleted_record:
            await interaction.followup.send(
                f"⚠️ 제{회차번호}회 모임 기록을 찾을 수 없습니다.", 
                ephemeral=True
            )
            return
            
        game_title = deleted_record.get('game_title', '알 수 없는 게임')
        desc = f"제{회차번호}회 모임 기록이 성공적으로 삭제되었습니다."
        
        if deleted_record.get('game_app_id'):
            desc += f"\n🔄 연결된 게임(**{game_title}**) 상태가 '미플레이'로 복원되었습니다."
            
        embed = discord.Embed(
            title="🗑️ 모임 기록 삭제",
            description=desc,
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ArchiveCog(bot))
