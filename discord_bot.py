import os
from typing import Any

import discord
import requests
from discord import app_commands
from discord.ext import commands


APP_BASE_URL = os.environ.get("APP_BASE_URL", "https://trading-app-kb38.onrender.com").rstrip("/")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "").strip()
REQUEST_TIMEOUT = 20


def get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
    response = requests.get(f"{APP_BASE_URL}{path}", params=params or {}, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def format_money(value: Any) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    sign = "+" if amount > 0 else ""
    return f"{sign}${amount:,.2f}"


def format_percent(value: Any) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    sign = "+" if amount > 0 else ""
    return f"{sign}{amount:.2f}%"


def build_price_embed(payload: dict[str, Any]) -> discord.Embed:
    color = 0x22C55E if float(payload.get("change", 0) or 0) >= 0 else 0xEF4444
    embed = discord.Embed(
        title=f"{payload.get('ticker', 'Ticker')} Snapshot",
        description=f"{format_money(payload.get('price'))} | {format_percent(payload.get('change'))}",
        color=color,
    )
    embed.add_field(name="Source", value=str(payload.get("data_source", "unknown")).title(), inline=True)
    embed.add_field(name="Cached", value="Yes" if payload.get("is_cached") else "No", inline=True)
    embed.add_field(name="Demo", value="Yes" if payload.get("is_demo") else "No", inline=True)
    return embed


def build_analysis_embed(payload: dict[str, Any]) -> discord.Embed:
    signal = (payload.get("indicators") or {}).get("trade_signal") or {}
    action = signal.get("action", "WAIT")
    color = 0x22C55E if action == "BUY" else 0xEF4444 if action == "SELL" else 0x94A3B8
    embed = discord.Embed(
        title=f"{payload.get('ticker', 'Ticker')} Analysis",
        description=f"{format_money(payload.get('price'))} | {format_percent(payload.get('change'))}",
        color=color,
    )
    embed.add_field(name="Setup", value=f"{signal.get('grade', '--')} {action}", inline=True)
    embed.add_field(name="Momentum", value=str((payload.get("momentum_score") or {}).get("value", "--")), inline=True)
    embed.add_field(name="Mode", value=str((payload.get("market_mode") or {}).get("label", "--")), inline=True)
    embed.add_field(name="Why Moving", value=str((payload.get("why_moving") or {}).get("summary", "No summary yet."))[:1024], inline=False)
    embed.add_field(name="Trade Plan", value=f"Entry {payload.get('plan', {}).get('entry', '--')} | Stop {payload.get('plan', {}).get('stop', '--')}", inline=False)
    reason = signal.get("reason") or payload.get("summary") or "No explanation yet."
    embed.set_footer(text=str(reason)[:200])
    return embed


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    try:
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=int(DISCORD_GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Discord bot ready as {bot.user}. Synced {len(synced)} guild commands.")
        else:
            synced = await bot.tree.sync()
            print(f"Discord bot ready as {bot.user}. Synced {len(synced)} global commands.")
    except Exception as exc:
        print("Discord sync failed:", exc)


@bot.tree.command(name="ping", description="Check whether the trading bot is online.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Trading bot is online.", ephemeral=True)


@bot.tree.command(name="price", description="Get the latest price snapshot for a ticker.")
@app_commands.describe(ticker="Ticker symbol, for example AAPL")
async def price(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer()
    try:
        payload = get_json("/watchlist/data", {"tickers": ticker.upper()})
        row = payload[0] if isinstance(payload, list) and payload else {}
        if not row:
            row = get_json("/live-price", {"ticker": ticker.upper()})
            row = {
                "ticker": ticker.upper(),
                "price": row.get("price", 0),
                "change": 0,
                "data_source": row.get("data_source"),
                "is_cached": row.get("is_cached"),
                "is_demo": False
            }
        await interaction.followup.send(embed=build_price_embed(row))
    except Exception as exc:
        await interaction.followup.send(f"Could not load {ticker.upper()} right now. {exc}")


@bot.tree.command(name="analyze", description="Run the app's trading analysis for a ticker.")
@app_commands.describe(ticker="Ticker symbol", strategy="scalp, day, swing, momentum, or mean", risk="conservative, balanced, or aggressive")
async def analyze(interaction: discord.Interaction, ticker: str, strategy: str = "day", risk: str = "balanced"):
    await interaction.response.defer()
    try:
        payload = get_json("/analyze", {"ticker": ticker.upper(), "strategy": strategy, "risk": risk})
        await interaction.followup.send(embed=build_analysis_embed(payload))
    except Exception as exc:
        await interaction.followup.send(f"Could not analyze {ticker.upper()} right now. {exc}")


@bot.tree.command(name="movers", description="Show the current top movers from the app's tracked universe.")
async def movers(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        payload = get_json("/scanner")
        rows = (payload.get("rows") or [])[:5]
        if not rows:
            await interaction.followup.send("No movers were available right now.")
            return

        embed = discord.Embed(title="Top Movers", description="Current leaders from the tracked universe.", color=0x38BDF8)
        for index, row in enumerate(rows, start=1):
            embed.add_field(
                name=f"#{index} {row.get('ticker', '--')}",
                value=(
                    f"{format_money(row.get('price'))} | {format_percent(row.get('change'))}\n"
                    f"{row.get('setup_grade', '--')} {row.get('setup_action', '')} | "
                    f"Momentum {(row.get('momentum_score') or {}).get('value', '--')}"
                ),
                inline=False
            )
        await interaction.followup.send(embed=embed)
    except Exception as exc:
        await interaction.followup.send(f"Could not load movers right now. {exc}")


@bot.tree.command(name="news", description="Show the latest headlines and move driver for a ticker.")
@app_commands.describe(ticker="Ticker symbol")
async def news(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer()
    try:
        payload = get_json("/analyze", {"ticker": ticker.upper(), "strategy": "day", "risk": "balanced"})
        news_payload = payload.get("news") or {}
        articles = (news_payload.get("articles") or [])[:3]
        embed = discord.Embed(
            title=f"{ticker.upper()} News",
            description=str(news_payload.get("driver", "No headline driver found.")),
            color=0xF59E0B
        )
        for article in articles:
            embed.add_field(
                name=str(article.get("title", "Headline"))[:256],
                value=str(article.get("link", "No link"))[:1024],
                inline=False
            )
        await interaction.followup.send(embed=embed)
    except Exception as exc:
        await interaction.followup.send(f"Could not load news for {ticker.upper()} right now. {exc}")


def main():
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("Missing DISCORD_BOT_TOKEN")
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
