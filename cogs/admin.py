import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import logging
import sys  # Import sys for restart
import os  # Import os for restart

from utils import (
    is_wearer, dm_wearer_on_use, save_wearer_id, save_session_state,
    auto_unlatch, update_session_time, format_time, check_is_wearer,
    api_request, check_is_privileged  # Added imports
)

logger = logging.getLogger(__name__)

# --- Admin Command Group (Moved from core.py) --- #

class AdminGroup(app_commands.Group):
    def __init__(self, bot: commands.Bot):
        super().__init__(name="admin", description="Administrative commands.")
        self.bot = bot

    @app_commands.command(name="marco", description="Check if the API server is responding")
    async def marco(self, interaction: discord.Interaction):
        """Check if the API server is responding"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.bot.API_BASE_URL}/api/marco", timeout=5) as response:
                    if response.status == 200:
                        data = await response.text()
                        await interaction.response.send_message(f"Server says: {data}", ephemeral=True)
                    else:
                        await interaction.response.send_message(f"Server responded with status {response.status}", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.response.send_message("Failed to reach server: Request timed out.", ephemeral=True)
        except aiohttp.ClientConnectorError:
            await interaction.response.send_message("Failed to reach server: Connection error.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"An unexpected error occurred: {e}", ephemeral=True)

    @app_commands.command(name="status", description="Shows the current status of the bot and session.")
    async def status(self, interaction: discord.Interaction):
        """Displays the current status."""
        # Check service reachability first
        api_status = "Unknown"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.bot.API_BASE_URL}/api/getPumpState", timeout=5) as response:
                    if response.status == 200:
                        api_status = "Reachable"
                    else:
                        api_status = f"Error ({response.status})"
        except Exception:
            api_status = "Unreachable"  # Service likely down

        # Session Info
        session_time_str = format_time(self.bot.session_time_remaining)
        banked_time_str = format_time(self.bot.banked_time)
        latch_status = "Latched" if self.bot.latch_active else "Unlatched"
        latch_reason_str = f" ({self.bot.latch_reason})" if self.bot.latch_reason else ""
        latch_status += latch_reason_str
        pump_status = "Unknown"

        if self.bot.last_pump_time:
            # Check if a pump task is running
            if self.bot.pump_task and not self.bot.pump_task.done():
                pump_status = "ON (Timed/Banked)"
            else:
                # Check API for actual pump state if no task is running
                pump_state = await api_request(self.bot, "getPumpState")
                if pump_state is not None:
                    try:
                        # Handle both text ("0"/"1") and JSON responses
                        if isinstance(pump_state, dict):
                            pump_status = "ON" if pump_state.get('is_on') else "OFF"
                        else:
                            # Try to convert text value to bool
                            try:
                                is_on = bool(int(str(pump_state)))
                                pump_status = "ON" if is_on else "OFF"
                            except (ValueError, TypeError):
                                pump_status = "UNKNOWN"
                    except Exception as e:
                        logger.error(f"Failed to parse pump state: {e}")
                        pump_status = "UNKNOWN"
                else:
                    pump_status = "OFF (API check failed)"
        else:
            pump_status = "OFF (Never run)"

        embed = discord.Embed(title="lBIS Status", color=discord.Color.blue())
        embed.add_field(name="API Service", value=api_status, inline=False)
        embed.add_field(name="Session Time", value=session_time_str, inline=True)
        embed.add_field(name="Banked Time", value=banked_time_str, inline=True)
        embed.add_field(name="Latch", value=latch_status, inline=True)
        embed.add_field(name="Pump", value=pump_status, inline=True)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="reboot", description="[Privileged Only] Restarts the bot.")
    @check_is_privileged()
    @dm_wearer_on_use("admin reboot")
    async def reboot(self, interaction: discord.Interaction):
        """Restarts the bot application."""
        await interaction.response.send_message("Rebooting...", ephemeral=True)
        logger.warning(f"Reboot initiated by {interaction.user} ({interaction.user.id})")
        # Ensure session state is saved before exiting
        from utils import save_session_state  # Local import to avoid circular dependency if moved
        save_session_state(self.bot)
        logger.info("Session state saved before reboot.")
        # Use os.execv to replace the current process with a new instance
        os.execv(sys.executable, ['python'] + sys.argv)

    @app_commands.command(name="wearer", description="Register yourself as this device's wearer (DM only, requires secret)")
    @app_commands.describe(secret="Your secret")
    async def wearer(self, interaction: discord.Interaction, secret: str):
        """Registers the user as the wearer if the secret is correct."""
        if interaction.guild is not None:
            await interaction.response.send_message("This command can only be used in DMs.", ephemeral=True)
            return

        # Access wearer_secret from bot's config
        if secret == self.bot.config.get("wearer_secret"):
            # Update config directly if needed, or just save
            self.bot.config['wearer_id'] = interaction.user.id  # Update config in memory
            save_wearer_id(self.bot, interaction.user.id)  # Save to bot.json
            await self.bot.request_status_update()  # Use bot method
            await interaction.response.send_message("You are now registered as this device's wearer!", ephemeral=True)
            logger.info(f"Wearer registered: {interaction.user} ({interaction.user.id})")
        else:
            await interaction.response.send_message("Incorrect secret.", ephemeral=True)
            logger.warning(f"Incorrect secret provided by {interaction.user} ({interaction.user.id}) for set_wearer.")

# --- Bank Command Group --- #

class BankGroup(app_commands.Group):
    def __init__(self, bot: commands.Bot):
        super().__init__(name="bank", description="Manage the banked time.")
        self.bot = bot

    @app_commands.command(name="add", description="[Wearer Only] Manually add time to the bank.")
    @check_is_wearer()
    @app_commands.describe(seconds="Number of seconds to add to the bank.")
    @dm_wearer_on_use("bank add")
    async def add(self, interaction: discord.Interaction, seconds: int):
        if seconds <= 0:
            await interaction.response.send_message("Please provide a positive number of seconds.", ephemeral=True)
            return

        max_bank = self.bot.config.get('max_banked_time', 3600)
        old_banked_time = self.bot.banked_time
        self.bot.banked_time = min(old_banked_time + seconds, max_bank)
        added_time = self.bot.banked_time - old_banked_time

        save_session_state(self.bot)
        logger.info(f"Wearer manually banked {added_time}s. New banked time: {self.bot.banked_time}s.")

        await interaction.response.send_message(
            f"Added {format_time(added_time)} to the bank. "
            f"Total banked time is now {format_time(self.bot.banked_time)}.",
            ephemeral=True
        )
        await self.bot.request_status_update()

    @app_commands.command(name="rem", description="[Wearer Only] Manually remove time from the bank.")
    @check_is_wearer()
    @app_commands.describe(seconds="Number of seconds to remove from the bank.")
    @dm_wearer_on_use("bank rem")
    async def rem(self, interaction: discord.Interaction, seconds: int):
        if seconds <= 0:
            await interaction.response.send_message("Please provide a positive number of seconds.", ephemeral=True)
            return

        old_banked_time = self.bot.banked_time
        self.bot.banked_time = max(0, old_banked_time - seconds)
        removed_time = old_banked_time - self.bot.banked_time

        save_session_state(self.bot)
        logger.info(f"Wearer manually removed {removed_time}s from bank. New banked time: {self.bot.banked_time}s.")

        await interaction.response.send_message(
            f"Removed {format_time(removed_time)} from the bank. "
            f"Total banked time is now {format_time(self.bot.banked_time)}.",
            ephemeral=True
        )
        await self.bot.request_status_update()

    @app_commands.command(name="set", description="[Wearer Only] Manually set the banked time.")
    @check_is_wearer()
    @app_commands.describe(seconds="Number of seconds to set the bank to.")
    @dm_wearer_on_use("bank set")
    async def set(self, interaction: discord.Interaction, seconds: int):
        if seconds < 0:
            await interaction.response.send_message("Please provide a non-negative number of seconds.", ephemeral=True)
            return

        max_bank = self.bot.config.get('max_banked_time', 3600)
        old_banked_time = self.bot.banked_time
        self.bot.banked_time = min(seconds, max_bank)

        save_session_state(self.bot)
        logger.info(f"Wearer manually set bank time to {self.bot.banked_time}s (was {old_banked_time}s). Limit was {max_bank}s.")

        await interaction.response.send_message(
            f"Banked time set to {format_time(self.bot.banked_time)}.",
            ephemeral=True
        )
        await self.bot.request_status_update()

    @app_commands.command(name="reset", description="[Wearer Only] Resets the banked time to zero.")
    @check_is_wearer()
    @dm_wearer_on_use("bank reset")
    async def reset(self, interaction: discord.Interaction):
        old_banked_time = self.bot.banked_time
        self.bot.banked_time = 0
        save_session_state(self.bot)
        logger.info(f"Wearer reset banked time from {format_time(old_banked_time)} to 0.")

        await interaction.response.send_message(
            f"Banked time has been reset to 0 (was {format_time(old_banked_time)}).",
            ephemeral=True
        )
        await self.bot.request_status_update()

# --- Admin Cog --- #

class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Add the command groups
        bot.tree.add_command(BankGroup(bot))
        bot.tree.add_command(AdminGroup(bot))  # Added AdminGroup

    async def cog_unload(self):
        # Remove the command groups
        self.bot.tree.remove_command("bank")
        self.bot.tree.remove_command("admin")  # Added AdminGroup removal

async def setup(bot):
    await bot.add_cog(AdminCog(bot))
