import json
import math
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.steam import (
    parse_app_id,
    search_games,
    get_app_details,
    is_steam_url,
    format_price
)

class GameSelect(discord.ui.Select):
    def __init__(self, results):
        options = []
        for r in results:
            options.append(discord.SelectOption(
                label=r["name"][:100],
                value=str(r["app_id"])
            ))
        super().__init__(placeholder="게임을 선택해주세요", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        app_id = int(self.values[0])
        await self.view.handle_selection(interaction, app_id)

class GameSelectView(discord.ui.View):
    def __init__(self, cog, results):
        super().__init__(timeout=60)
        self.cog = cog
        self.add_item(GameSelect(results))

    async def handle_selection(self, interaction: discord.Interaction, app_id: int):
        await interaction.response.defer(ephemeral=True)
        details = await get_app_details(app_id)
        if not details:
            await interaction.followup.send("게임 상세 정보를 가져오는데 실패했습니다.", ephemeral=True)
            self.stop()
            return
        
        await self.cog.process_game_request(interaction, details)
        self.stop()

class PaginatorView(discord.ui.View):
    def __init__(self, items: list, title: str):
        super().__init__(timeout=120)
        self.items = items
        self.title = title
        self.current_page = 0
        self.per_page = 10
        self.max_page = math.ceil(len(items) / self.per_page)
        self.update_buttons()

    def update_buttons(self):
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.max_page - 1

    def generate_embed(self) -> discord.Embed:
        embed = discord.Embed(title=self.title, color=discord.Color.blue())
        start_idx = self.current_page * self.per_page
        end_idx = start_idx + self.per_page
        page_items = self.items[start_idx:end_idx]

        description = ""
        for item in page_items:
            # Assuming db returns dict with these keys
            title = item.get("title", "알 수 없음")
            status = item.get("status", "미플레이")
            price = item.get("price_overview")
            is_free = item.get("is_free", False)
            
            # format price if stored as json string or dict
            if isinstance(price, str) and price:
                try:
                    price = json.loads(price)
                except json.JSONDecodeError:
                    pass
            
            formatted_price = format_price(price, is_free)
            added_at = item.get("added_at", "")
            if added_at:
                added_at = f" ({added_at[:10]})"
            
            status_emoji = "⏳"
            if status == "완료": status_emoji = "✅"
            elif status == "진행 중": status_emoji = "▶️"
            elif status == "보관": status_emoji = "📦"
            elif status == "미플레이": status_emoji = "🆕"

            description += f"{status_emoji} **{title}** - {formatted_price}{added_at}\n"

        embed.description = description if description else "등록된 게임이 없습니다."
        embed.set_footer(text=f"페이지 {self.current_page + 1} / max(1, {self.max_page})")
        return embed

    @discord.ui.button(label="이전", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)

    @discord.ui.button(label="다음", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.generate_embed(), view=self)


class Wishlist(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def process_game_request(self, interaction: discord.Interaction, details: dict):
        guild_id = interaction.guild_id
        app_id = details["app_id"]
        
        # Check if already registered
        existing = await self.bot.db.get_wishlist_game(guild_id, app_id)
        if existing:
            # If interaction is already responded to (deferred), use followup
            if interaction.response.is_done():
                await interaction.followup.send("❌ 이미 위시리스트에 등록된 게임입니다.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ 이미 위시리스트에 등록된 게임입니다.", ephemeral=True)
            return

        # Prepare DB fields (DB manager handles JSON serialization)
        tags_raw = details.get("tags", [])
        platforms_raw = details.get("platforms", {})
        price_raw = details.get("price_overview")

        # Insert to DB
        await self.bot.db.add_wishlist_game(
            guild_id=guild_id,
            app_id=app_id,
            title=details.get("title", "Unknown"),
            tags=tags_raw,
            platforms=platforms_raw,
            is_free=details.get("is_free", False),
            price_overview=price_raw,
            added_by=interaction.user.id,
            header_image=details.get("header_image", "")
        )

        # Notify success stat ephemerally
        if interaction.response.is_done():
            await interaction.followup.send("✅ 성공적으로 위시리스트에 등록되었습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("✅ 성공적으로 위시리스트에 등록되었습니다.", ephemeral=True)

        # Send rich embed to the channel
        embed = discord.Embed(
            title=details.get("title", "Unknown"),
            url=details.get("store_url", f"https://store.steampowered.com/app/{app_id}"),
            color=discord.Color.green()
        )
        if details.get("header_image"):
            embed.set_thumbnail(url=details.get("header_image"))

        # Tags
        tags_list = details.get("tags", [])
        if tags_list:
            embed.add_field(name="태그", value=", ".join(tags_list[:5]), inline=False)
        
        # Platform support
        platforms_dict = details.get("platforms", {})
        platform_icons = []
        if isinstance(platforms_dict, dict):
            if platforms_dict.get("windows"): platform_icons.append("🪟")
            if platforms_dict.get("mac"): platform_icons.append("🍎")
            if platforms_dict.get("linux"): platform_icons.append("🐧")
        
        if platform_icons:
            embed.add_field(name="플랫폼", value=" ".join(platform_icons), inline=True)

        # Price
        price_text = format_price(details.get("price_overview"), details.get("is_free", False))
        embed.add_field(name="가격", value=price_text, inline=True)
        embed.set_footer(text=f"신청자: {interaction.user.display_name}")

        view = discord.ui.View()
        store_url = details.get("store_url", f"https://store.steampowered.com/app/{app_id}")
        view.add_item(discord.ui.Button(label="스팀 상점 보기", url=store_url, style=discord.ButtonStyle.link))

        await interaction.channel.send(embed=embed, view=view)


    @app_commands.command(name="게임신청", description="스팀 게임을 위시리스트에 등록합니다.")
    @app_commands.describe(query="게임의 스팀 URL 또는 검색어를 입력하세요.")
    async def request_game(self, interaction: discord.Interaction, query: str):
        if is_steam_url(query):
            app_id = parse_app_id(query)
            if not app_id:
                await interaction.response.send_message("유효하지 않은 스팀 URL입니다.", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            details = await get_app_details(app_id)
            if not details:
                await interaction.followup.send("게임 상세 정보를 가져오는데 실패했습니다.", ephemeral=True)
                return
            
            await self.process_game_request(interaction, details)
        else:
            await interaction.response.defer(ephemeral=True)
            results = await search_games(query, max_results=3)
            if not results:
                await interaction.followup.send("검색 결과가 없습니다.", ephemeral=True)
                return
            
            view = GameSelectView(self, results)
            await interaction.followup.send("검색 결과에서 게임을 선택해주세요:", view=view, ephemeral=True)


    @app_commands.command(name="위시리스트목록", description="등록된 게임 위시리스트를 확인합니다.")
    @app_commands.describe(status_filter="필터링할 게임 상태")
    @app_commands.choices(status_filter=[
        app_commands.Choice(name="미플레이", value="미플레이"),
        app_commands.Choice(name="진행 중", value="진행 중"),
        app_commands.Choice(name="완료", value="완료"),
        app_commands.Choice(name="보관", value="보관"),
    ])
    async def wishlist_list(self, interaction: discord.Interaction, status_filter: Optional[app_commands.Choice[str]] = None):
        await interaction.response.defer(ephemeral=True)
        status_val = status_filter.value if status_filter else None
        items = await self.bot.db.get_wishlist(interaction.guild_id, status_val)
        
        if not items:
            await interaction.followup.send("위시리스트에 등록된 게임이 없습니다.")
            return

        title_suffix = f" ({status_val})" if status_val else " (전체)"
        view = PaginatorView(items, f"게임 위시리스트{title_suffix}")
        await interaction.followup.send(embed=view.generate_embed(), view=view)


    @app_commands.command(name="게임상태변경", description="[관리자] 위시리스트의 게임 상태를 변경합니다.")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(game_title="상태를 변경할 게임 이름 (일부 입력 가능)", status="변경할 상태")
    @app_commands.choices(status=[
        app_commands.Choice(name="미플레이", value="미플레이"),
        app_commands.Choice(name="진행 중", value="진행 중"),
        app_commands.Choice(name="완료", value="완료"),
        app_commands.Choice(name="보관", value="보관"),
    ])
    async def update_status(self, interaction: discord.Interaction, game_title: str, status: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True)
        items = await self.bot.db.get_wishlist(interaction.guild_id)
        
        matches = [item for item in items if game_title.lower() in item.get("title", "").lower()]
        
        if not matches:
            await interaction.followup.send("해당 이름이 포함된 게임을 위시리스트에서 찾을 수 없습니다.", ephemeral=True)
            return
            
        if len(matches) > 1:
            match_titles = ", ".join([m.get("title", "Unknown") for m in matches])
            await interaction.followup.send(f"여러 게임이 검색되었습니다. 이름을 더 정확히 입력해주세요: {match_titles}", ephemeral=True)
            return
            
        target = matches[0]
        app_id = target["app_id"]
        
        await self.bot.db.update_game_status(interaction.guild_id, app_id, status.value)
        
        embed = discord.Embed(
            title="게임 상태 변경 완료",
            description=f"**{target['title']}**의 상태가 `{status.value}`(으)로 변경되었습니다.",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="게임삭제", description="본인이 신청한 위시리스트 게임을 삭제합니다.")
    @app_commands.describe(game_title="삭제할 게임 이름 (일부 입력 가능)")
    async def delete_game(self, interaction: discord.Interaction, game_title: str):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        user_id = interaction.user.id

        # 본인이 신청한 게임만 검색
        all_games = await self.bot.db.get_wishlist(guild_id)
        my_matches = [
            g for g in all_games
            if game_title.lower() in g.get("title", "").lower()
            and g.get("added_by") == user_id
        ]

        if not my_matches:
            await interaction.followup.send(
                f"❌ '{game_title}'과(와) 일치하는 본인이 신청한 게임을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        if len(my_matches) > 1:
            titles = "\n".join(f"- {g['title']}" for g in my_matches[:5])
            await interaction.followup.send(
                f"⚠️ 여러 게임이 검색되었습니다. 좀 더 정확한 이름을 입력해주세요:\n{titles}",
                ephemeral=True,
            )
            return

        game = my_matches[0]
        try:
            deleted_title = await self.bot.db.delete_wishlist_game(
                guild_id, game["app_id"], user_id
            )
            if deleted_title:
                embed = discord.Embed(
                    title="🗑️ 게임 삭제 완료",
                    description=f"**{deleted_title}**이(가) 위시리스트에서 삭제되었습니다.",
                    color=discord.Color.orange(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(
                    "❌ 게임을 찾을 수 없습니다.", ephemeral=True
                )
        except PermissionError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Wishlist(bot))
