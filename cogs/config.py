import discord
from discord.ext import commands
from discord import app_commands
import re

class ConfigCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="서버설정", description="서버의 기본 시간을 설정합니다.")
    @app_commands.checks.has_permissions(administrator=True)
    async def config_server(self, interaction: discord.Interaction, 기본_낮시간: str = '14:00', 기본_밤시간: str = '24:00', 기본_마감기한: int = 24):
        time_pattern = re.compile(r"^([01]?\d|2[0-4]):([0-5]\d)$")
        
        if not time_pattern.match(기본_낮시간):
            await interaction.response.send_message("기본_낮시간 형식이 올바르지 않습니다. HH:MM 형식으로 입력해주세요.", ephemeral=True)
            return
            
        if not time_pattern.match(기본_밤시간):
            await interaction.response.send_message("기본_밤시간 형식이 올바르지 않습니다. HH:MM 형식으로 입력해주세요.", ephemeral=True)
            return

        try:
            await self.bot.db.upsert_server_config(
                interaction.guild.id, 
                기본_낮시간, 
                기본_밤시간, 
                기본_마감기한
            )
            
            embed = discord.Embed(title="서버 설정 완료", color=discord.Color.green())
            embed.add_field(name="낮 시간", value=기본_낮시간, inline=True)
            embed.add_field(name="밤 시간", value=기본_밤시간, inline=True)
            embed.add_field(name="마감 기한", value=f"{기본_마감기한}시간", inline=True)
            
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message(f"설정 저장 중 오류가 발생했습니다: {e}", ephemeral=True)

    @config_server.error
    async def config_server_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("이 명령어를 사용할 권한이 없습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("오류가 발생했습니다.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ConfigCog(bot))
