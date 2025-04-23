import discord
from discord.ext import commands
from discord import app_commands
import logging # Added logging
from utils import is_wearer, format_time, update_session_time, save_session_state, dm_wearer_on_use, check_is_wearer # Added imports

logger = logging.getLogger(__name__) # Added logger

# --- Session Command Group --- #

class SessionGroup(app_commands.Group):
    def __init__(self, bot: commands.Bot):
        super().__init__(name="session", description="Manage the session timer.")
        self.bot = bot

    @app_commands.command(name="add", description="[Wearer Only] Add time to the current session.")
    @check_is_wearer()
    @app_commands.describe(minutes="Minutes to add to the session.")
    @dm_wearer_on_use("session add")
    async def add(self, interaction: discord.Interaction, minutes: int):
        max_session = self.bot.config.get('max_session_time', 7200) # Default 2hr

        if minutes <= 0:
            await interaction.response.send_message("Please specify a positive number of minutes.", ephemeral=True)
            return

        # Calculate potential new time without exceeding max
        current_time = self.bot.session_time_remaining
        time_to_add = minutes * 60
        new_time = min(current_time + time_to_add, max_session)
        actual_added = new_time - current_time

        if actual_added <= 0 and time_to_add > 0:
             await interaction.response.send_message(f"Session time is already at or above the maximum ({format_time(max_session)}). Cannot add more time.", ephemeral=True)
             return

        self.bot.session_time_remaining = new_time
        save_session_state(self.bot)
        await self.bot.request_status_update()
        await interaction.response.send_message(f"Added {format_time(actual_added)} to session. {format_time(self.bot.session_time_remaining)} remaining.", ephemeral=True)

    @app_commands.command(name="rem", description="[Wearer Only] Remove time from the current session.")
    @check_is_wearer()
    @app_commands.describe(minutes="Minutes to remove from the session.")
    @dm_wearer_on_use("session rem")
    async def rem(self, interaction: discord.Interaction, minutes: int):
        if minutes <= 0:
            await interaction.response.send_message("Please specify a positive number of minutes.", ephemeral=True)
            return

        current_time = self.bot.session_time_remaining
        time_to_remove = minutes * 60
        new_time = max(0, current_time - time_to_remove)
        actual_removed = current_time - new_time

        self.bot.session_time_remaining = new_time
        save_session_state(self.bot)
        await self.bot.request_status_update()
        await interaction.response.send_message(f"Removed {format_time(actual_removed)} from session. {format_time(self.bot.session_time_remaining)} remaining.", ephemeral=True)


    @app_commands.command(name="set", description="[Wearer Only] Set the session timer to a specific value.")
    @check_is_wearer()
    @app_commands.describe(minutes="Minutes to set the session timer to.")
    @dm_wearer_on_use("session set")
    async def set(self, interaction: discord.Interaction, minutes: int):
        max_session = self.bot.config.get('max_session_time', 7200) # Default 2hr

        if minutes < 0:
            await interaction.response.send_message("Please specify a non-negative number of minutes.", ephemeral=True)
            return

        new_time_seconds = min(minutes * 60, max_session)

        self.bot.session_time_remaining = new_time_seconds
        # Note: We are NOT updating default_session_time here anymore. Reset handles that.
        self.bot.session_pump_start = None # Clear pump start if setting time manually
        save_session_state(self.bot)
        await self.bot.request_status_update()
        await interaction.response.send_message(f"Session time set to {format_time(self.bot.session_time_remaining)}.", ephemeral=True)


    @app_commands.command(name="reset", description="[Wearer Only] Reset the session timer to the default duration.")
    @check_is_wearer()
    @dm_wearer_on_use("session reset")
    async def reset(self, interaction: discord.Interaction):
        # Use the stored default time from config
        default_session_time = self.bot.config.get('default_session_time', 1800)
        self.bot.session_time_remaining = default_session_time
        self.bot.session_pump_start = None # Also clear pump start time
        save_session_state(self.bot)
        await self.bot.request_status_update()
        await interaction.response.send_message(f"Session timer has been reset to the default: {format_time(self.bot.session_time_remaining)}.", ephemeral=True)

# --- Cog Setup --- #

class SessionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Add the command group to the cog/tree
        bot.tree.add_command(SessionGroup(bot))

    async def cog_unload(self):
        # Remove the command group when unloading
        self.bot.tree.remove_command("session")

    # Removed old standalone commands: add_time, reset_time, check_time, set_time

async def setup(bot):
  await bot.add_cog(SessionCog(bot))
