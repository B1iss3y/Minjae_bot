import discord
from discord.ext import commands
from discord import app_commands

class SteamCodeModal(discord.ui.Modal, title="스팀 친구 코드 입력"):
    steam_code = discord.ui.TextInput(
        label="스팀 친구 코드",
        placeholder="예: 123456789",
        required=True,
        max_length=50
    )

    def __init__(self, os_type: str, bot, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.os_type = os_type
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        guild_id = interaction.guild.id
        nickname = interaction.user.display_name
        steam_friend_code = self.steam_code.value

        try:
            await self.bot.db.upsert_user(
                user_id,
                guild_id,
                nickname,
                self.os_type,
                steam_friend_code
            )

            embed = discord.Embed(title="내 정보 등록 완료", color=discord.Color.blue())
            embed.add_field(name="닉네임", value=nickname, inline=True)
            embed.add_field(name="운영체제", value=self.os_type, inline=True)
            embed.add_field(name="스팀 친구 코드", value=steam_friend_code, inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"정보 등록 중 오류가 발생했습니다: {e}", ephemeral=True)

class OSSelect(discord.ui.Select):
    def __init__(self, bot):
        self.bot = bot
        options = [
            discord.SelectOption(label="Windows", description="Windows 운영체제 사용"),
            discord.SelectOption(label="macOS", description="macOS 운영체제 사용")
        ]
        super().__init__(placeholder="사용 중인 운영체제를 선택하세요...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        os_type = self.values[0]
        modal = SteamCodeModal(os_type=os_type, bot=self.bot)
        await interaction.response.send_modal(modal)

class UserRegisterView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.add_item(OSSelect(bot))

class UserCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="내정보등록", description="내 정보를 등록합니다.")
    async def register_info(self, interaction: discord.Interaction):
        view = UserRegisterView(self.bot)
        await interaction.response.send_message("아래 메뉴에서 사용 중인 운영체제를 선택해주세요.", view=view, ephemeral=True)

    @app_commands.command(name="친구코드", description="서버에 등록된 유저들의 스팀 친구 코드를 확인합니다.")
    async def friend_codes(self, interaction: discord.Interaction):
        users = await self.bot.db.get_all_users(interaction.guild_id)
        if not users:
            await interaction.response.send_message(
                "📭 등록된 유저가 없습니다. `/내정보등록`으로 먼저 등록해주세요.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🎮 스팀 친구 코드 목록",
            color=discord.Color.blue(),
        )
        for user in users:
            os_emoji = "🍎" if user.get("os_type") == "macOS" else "🪟"
            code = user.get("steam_friend_code") or "미등록"
            embed.add_field(
                name=f"{os_emoji} {user.get('nickname', '알 수 없음')}",
                value=f"`{code}`",
                inline=True,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(UserCog(bot))
