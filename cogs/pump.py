import asyncio
import logging
import time
import discord
from discord import app_commands
from discord.ext import commands

from utils import (
    api_request, format_time, save_session_state, dm_wearer_on_use,
    update_session_time, check_is_privileged, toggle_latch, set_latch_reason
)
from utils.permissions import check_is_wearer

logger = logging.getLogger(__name__)

# --- Latch Command Group (Moved from core.py) --- #

class LatchGroup(app_commands.Group):
    def __init__(self, bot: commands.Bot):
        super().__init__(name="latch", description="Controls the safety latch.")
        self.bot = bot

    @app_commands.command(name="on", description="[Wearer Only] Engages the latch.")
    @check_is_wearer()
    @app_commands.describe(reason="Optional reason for latching.", duration="Optional duration in seconds to latch for.")
    @dm_wearer_on_use("latch on")
    async def latch_on(self, interaction: discord.Interaction, reason: str = None, duration: int = None):
        success, message = await toggle_latch(self.bot, True, reason, duration)
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await self.bot.request_status_update()

    @app_commands.command(name="off", description="[Wearer Only] Disengages the latch.")
    @check_is_wearer()
    @dm_wearer_on_use("latch off")
    async def latch_off(self, interaction: discord.Interaction):
        success, message = await toggle_latch(self.bot, False)
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await self.bot.request_status_update()

    @app_commands.command(name="reason", description="[Wearer Only] Sets or clears the reason for the latch.")
    @check_is_wearer()
    @app_commands.describe(reason="The reason for the latch (leave blank to clear).")
    @dm_wearer_on_use("latch reason")
    async def latch_reason(self, interaction: discord.Interaction, reason: str = None):
        success, message = await set_latch_reason(self.bot, reason)
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await self.bot.request_status_update()

    @app_commands.command(name="toggle", description="[Wearer Only] Toggles the safety latch on or off.")
    @check_is_wearer()
    @dm_wearer_on_use("latch toggle")
    async def latch_toggle(self, interaction: discord.Interaction):
        """Toggles the safety latch on or off."""
        new_state = not self.bot.latch_active
        success, message = await toggle_latch(self.bot, new_state)
        await interaction.response.send_message(message, ephemeral=True)
        if success:
            await self.bot.request_status_update()

# --- Helper Functions ---

async def _check_interruptions(bot) -> tuple[bool, str]:
    """Checks for conditions that should interrupt the pump loop."""
    if bot.latch_active:
        logger.info("Pump interruption: Latch active.")
        return True, "latched"
    if not bot.service_was_up:
        logger.info("Pump interruption: API service down.")
        return True, "service down"
    return False, ""

async def _cleanup_pump_task(bot, actual_run_duration: float, consumed_session_time: int, interruption_reason: str = ""):
    """Handles the common cleanup tasks after a pump loop finishes or is interrupted."""
    logger.info(f"Pump task cleanup. Duration: {actual_run_duration:.2f}s, Consumed Session: {consumed_session_time}s, Reason: '{interruption_reason}'")
    if await api_request(bot, "setPumpState", method="POST", data={"pump": 0}):
        logger.info("Pump turned off via API.")
        bot.last_pump_time = time.time()
    else:
        logger.error("Failed to turn off pump via API during cleanup.")

    if consumed_session_time > 0:
        update_session_time(bot, -consumed_session_time)

    bot.pump_task = None
    bot.pump_task_end_time = None

    save_session_state(bot)
    await bot.request_status_update()

    if interruption_reason:
        logger.warning(f"Pump task interrupted: {interruption_reason}. Ran for {format_time(int(actual_run_duration))}.")
    else:
        logger.info(f"Pump task completed successfully. Ran for {format_time(int(actual_run_duration))}.")

async def _timed_pump_loop(bot, initial_run_seconds: int):
    start_time = asyncio.get_event_loop().time()
    actual_run_duration = 0
    interrupted = False
    interruption_reason = ""

    try:
        logger.info(f"Starting timed pump loop. Target end time: {bot.pump_task_end_time}")
        while asyncio.get_event_loop().time() < bot.pump_task_end_time:
            interrupted, interruption_reason = await _check_interruptions(bot)
            if interrupted:
                break
            await asyncio.sleep(0.5)

        actual_run_duration = asyncio.get_event_loop().time() - start_time
        logger.info(f"Timed pump loop finished or interrupted after {actual_run_duration:.2f}s.")

        if interrupted:
            remaining_intended = bot.pump_task_end_time - asyncio.get_event_loop().time()
            if remaining_intended > 0:
                max_bank = bot.config.get('max_banked_time', 3600)
                old_banked = bot.banked_time
                bot.banked_time = min(old_banked + int(remaining_intended), max_bank)
                banked_amount = bot.banked_time - old_banked
                if banked_amount > 0:
                    logger.info(f"Banking {banked_amount}s due to interruption ({interruption_reason}).")
                    save_session_state(bot)

    except asyncio.CancelledError:
        logger.info("Timed pump task cancelled.")
        actual_run_duration = asyncio.get_event_loop().time() - start_time
        interrupted = True
        interruption_reason = "cancelled"
    finally:
        await _cleanup_pump_task(bot, actual_run_duration, int(actual_run_duration), interruption_reason)

async def _banked_pump_loop(bot, initial_run_seconds: int):
    start_time = asyncio.get_event_loop().time()
    actual_run_duration = 0
    interrupted = False
    interruption_reason = ""
    decremented_bank = 0
    decremented_session = 0

    try:
        logger.info(f"Starting banked pump loop. Target end time: {bot.pump_task_end_time}")
        last_decrement_time = start_time

        while asyncio.get_event_loop().time() < bot.pump_task_end_time:
            interrupted, interruption_reason = await _check_interruptions(bot)
            if interrupted:
                break

            if bot.banked_time <= 0:
                interrupted = True
                interruption_reason = "bank empty"
                logger.info("Bank ran out during banked pump.")
                break
            if bot.session_time_remaining <= 0:
                interrupted = True
                interruption_reason = "session empty"
                logger.info("Session time ran out during banked pump.")
                break

            current_time = asyncio.get_event_loop().time()
            elapsed_since_decrement = current_time - last_decrement_time
            if elapsed_since_decrement >= 1.0:
                decrement_amount = int(elapsed_since_decrement)
                actual_decrement = min(decrement_amount, bot.banked_time, bot.session_time_remaining)

                if actual_decrement > 0:
                    bot.banked_time -= actual_decrement
                    decremented_bank += actual_decrement
                    decremented_session += actual_decrement
                    last_decrement_time = current_time
                else:
                    interrupted = True
                    interruption_reason = "bank or session empty on decrement"
                    logger.info("Stopping banked pump as bank or session reached zero during decrement check.")
                    break

            await asyncio.sleep(0.2)

        actual_run_duration = asyncio.get_event_loop().time() - start_time
        logger.info(f"Banked pump loop finished or interrupted after {actual_run_duration:.2f}s.")

    except asyncio.CancelledError:
        logger.info("Banked pump task cancelled.")
        actual_run_duration = asyncio.get_event_loop().time() - start_time
        interrupted = True
        interruption_reason = "cancelled"
    finally:
        await _cleanup_pump_task(bot, actual_run_duration, decremented_session, interruption_reason)
        if decremented_bank > 0:
            logger.info(f"Consumed {format_time(decremented_bank)} from bank.")

async def _start_timed_pump(bot, interaction: discord.Interaction, seconds: int):
    max_pump_duration = bot.config.get('max_pump_duration', 60)

    if seconds <= 0:
        await interaction.response.send_message("Please provide a positive duration in seconds.", ephemeral=True)
        return

    if seconds > max_pump_duration:
        await interaction.response.send_message(f"Maximum duration allowed is {max_pump_duration} seconds (configurable via 'max_pump_duration' in bot.json).", ephemeral=True)
        return

    if bot.latch_active:
        await interaction.response.send_message("Pump is latched, cannot start timed pump.", ephemeral=True)
        return

    if not bot.service_was_up:
        await interaction.response.send_message("API service is down, cannot control pump.", ephemeral=True)
        return

    loop = asyncio.get_event_loop()
    current_time = loop.time()
    max_bank = bot.config.get('max_banked_time', 3600)
    response_message = ""

    if bot.pump_task and not bot.pump_task.done():
        logger.info(f"Pump task already running. Extending timer.")
        remaining_current = max(0, bot.pump_task_end_time - current_time)
        max_possible_additional = max(0, bot.session_time_remaining - remaining_current)
        effective_max_duration_for_extension = min(remaining_current + max_possible_additional, max_pump_duration)

        time_to_add = max(0, effective_max_duration_for_extension - remaining_current)
        time_to_add = min(time_to_add, seconds)

        overflow = max(0, seconds - time_to_add)

        banked_amount = 0
        if overflow > 0:
            old_banked = bot.banked_time
            bot.banked_time = min(old_banked + int(overflow), max_bank)
            banked_amount = bot.banked_time - old_banked
            if banked_amount > 0:
                logger.info(f"Banking {banked_amount}s overflow from inflate extension.")
                save_session_state(bot)

        bot.pump_task_end_time = current_time + remaining_current + time_to_add
        logger.info(f"Extended pump task. New end time: {bot.pump_task_end_time}. Added: {time_to_add:.2f}s.")

        response_message = f"Pump timer already running. Extended by {format_time(int(time_to_add))} using session time."
        if banked_amount > 0:
            response_message += f" Banked {format_time(banked_amount)} overflow (max session/pump duration or input limit reached)."
        elif time_to_add < seconds:
            response_message += f" Could not add full duration due to session/pump limits."

        await interaction.response.send_message(response_message)

    else:
        if bot.session_time_remaining <= 0:
            await interaction.response.send_message("No session time remaining.", ephemeral=True)
            return

        run_seconds = min(seconds, bot.session_time_remaining)

        if run_seconds <= 0:
            await interaction.response.send_message("Cannot run pump (calculated duration is zero, likely no session time).", ephemeral=True)
            return

        logger.info(f"Starting new timed pump for {run_seconds}s at intensity {bot.pump_intensity:.2f}.")
        if await api_request(bot, "setPumpState", method="POST", data={"pump": bot.pump_intensity}):
            bot.last_pump_time = time.time()
            bot.pump_task_end_time = current_time + run_seconds
            bot.pump_task = asyncio.create_task(_timed_pump_loop(bot, run_seconds))

            response_message = f"Pump started for {format_time(run_seconds)} at intensity {bot.pump_intensity:.2f} using session time."
            if run_seconds < seconds:
                response_message += f" (Limited by session time)."
            await interaction.response.send_message(response_message)

            await bot.request_status_update()
        else:
            await interaction.response.send_message("Failed to start pump via API.", ephemeral=True)

async def _start_banked_pump(bot, interaction: discord.Interaction, seconds: int):
    max_pump_duration = bot.config.get('max_pump_duration', 60)

    if seconds <= 0:
        await interaction.response.send_message("Please provide a positive duration.", ephemeral=True)
        return

    if bot.latch_active:
        await interaction.response.send_message("Pump is latched, cannot start banked pump.", ephemeral=True)
        return

    if not bot.service_was_up:
        await interaction.response.send_message("API service is down, cannot control pump.", ephemeral=True)
        return

    if bot.pump_task and not bot.pump_task.done():
        await interaction.response.send_message("Another pump operation is already running.", ephemeral=True)
        return

    if bot.banked_time <= 0:
        await interaction.response.send_message("No time in the bank.", ephemeral=True)
        return

    if bot.session_time_remaining <= 0:
        await interaction.response.send_message("No session time remaining (required to use bank).", ephemeral=True)
        return

    run_seconds = min(seconds, bot.banked_time, bot.session_time_remaining, max_pump_duration)

    if run_seconds <= 0:
        await interaction.response.send_message("Cannot run pump (calculated duration is zero based on available time).", ephemeral=True)
        return

    logger.info(f"Starting banked pump for {run_seconds}s at intensity {bot.pump_intensity:.2f}.")
    if await api_request(bot, "setPumpState", method="POST", data={"pump": bot.pump_intensity}):
        bot.last_pump_time = time.time()
        bot.pump_task_end_time = asyncio.get_event_loop().time() + run_seconds
        bot.pump_task = asyncio.create_task(_banked_pump_loop(bot, run_seconds))

        response_message = f"Pump started using banked time for {format_time(run_seconds)} at intensity {bot.pump_intensity:.2f}."
        if run_seconds < seconds:
            response_message += f" (Limited by bank, session time, or max duration)."
        await interaction.response.send_message(response_message)

        await bot.request_status_update()
    else:
        await interaction.response.send_message("Failed to start pump via API.", ephemeral=True)

async def _send_pump_state_api(bot, interaction: discord.Interaction, intensity: float):
    """Helper function to send pump state via API, cancelling existing tasks."""
    # Cancel any running timed pump task first
    if bot.pump_task and not bot.pump_task.done():
        # Don't cancel if we're just updating intensity of the running task
        # We only cancel if intensity is 0.0 (manual off) or if intensity > 0 and task *wasn't* running (manual on)
        # The intensity command handles updating running tasks separately.
        # Let's simplify: Always cancel if intensity is 0.0 (explicit off)
        # If intensity > 0, the calling command should decide if a task needs starting.
        if intensity == 0.0:
             bot.pump_task.cancel()
             logger.info("Cancelled running pump task due to manual pump off.")
             # Wait briefly for cancellation to potentially process before sending new state
             await asyncio.sleep(0.1)
        # If intensity > 0, we assume a new timed/banked task will handle it, or it's an intensity update for a running task.

    if await api_request(bot, "setPumpState", method="POST", data={"pump": intensity}):
        bot.last_pump_time = time.time()
        # DO NOT update bot.pump_intensity here
        # DO NOT save_session_state here
        state_str = "OFF" if intensity == 0.0 else f"ON (Intensity: {intensity:.2f})"
        # Avoid sending response if interaction already responded (e.g., in pump_intensity)
        if interaction and not interaction.response.is_done():
            await interaction.response.send_message(f"Pump set to {state_str}.", ephemeral=True)
        elif not interaction:
             logger.info(f"Pump set to {state_str} (no interaction response).") # Log if no interaction
        await bot.request_status_update()
        return True # Indicate success
    else:
        if interaction and not interaction.response.is_done():
            await interaction.response.send_message("Failed to set pump state via API.", ephemeral=True)
        elif not interaction:
            logger.error("Failed to set pump state via API (no interaction response).") # Log if no interaction
        return False # Indicate failure

# --- Pump Command Group --- #

class PumpGroup(app_commands.Group):
    def __init__(self, bot: commands.Bot):
        super().__init__(name="pump", description="[Privileged Only] Manually control the pump.")
        self.bot = bot

    @app_commands.command(name="on", description="[Privileged Only] Manually turns the pump ON using the stored intensity.")
    @check_is_privileged()
    @dm_wearer_on_use("pump on")
    async def pump_on(self, interaction: discord.Interaction):
        # Use the stored intensity, call the API helper
        await _send_pump_state_api(self.bot, interaction, self.bot.pump_intensity)

    @app_commands.command(name="off", description="[Privileged Only] Manually turns the pump OFF.")
    @check_is_privileged()
    @dm_wearer_on_use("pump off")
    async def pump_off(self, interaction: discord.Interaction):
        # Send intensity 0.0 using the API helper
        await _send_pump_state_api(self.bot, interaction, 0.0)

    @app_commands.command(name="intensity", description="[Privileged Only] Sets the default pump intensity (0.0 to 1.0).")
    @check_is_privileged()
    @app_commands.describe(intensity="Pump intensity (0.0 = off, 1.0 = full power).")
    @dm_wearer_on_use("pump intensity")
    async def pump_intensity(self, interaction: discord.Interaction, intensity: float):
        if not 0.0 <= intensity <= 1.0:
            await interaction.response.send_message("Intensity must be between 0.0 and 1.0.", ephemeral=True)
            return

        # Update the default intensity state and save
        self.bot.pump_intensity = intensity
        save_session_state(self.bot)
        logger.info(f"Pump intensity set to {intensity:.2f} by {interaction.user}.")
        response_message = f"Default pump intensity set to {intensity:.2f}."
        api_success = True # Assume success unless API call fails

        # Check if a pump task is currently running *or* if the pump might be manually on
        # We should update the pump's current state if it's supposed to be running
        # A simple check is if the last API call wasn't to turn it off (intensity 0)
        # Or better: check if a timed/banked task is active.
        if self.bot.pump_task and not self.bot.pump_task.done():
            logger.info(f"Pump task is running. Updating current intensity to {intensity:.2f} via API.")
            # Call API helper to change the intensity of the currently running pump
            # Pass interaction=None to prevent double response
            if await _send_pump_state_api(self.bot, None, intensity):
                logger.info(f"Successfully updated running pump intensity to {intensity:.2f}.")
                response_message += f"\nApplied intensity {intensity:.2f} to the currently running pump."
            else:
                logger.error(f"Failed to update running pump intensity to {intensity:.2f} via API.")
                response_message += "\n⚠️ Failed to apply intensity to the currently running pump (API error)."
                api_success = False
        # If no task is running, the new intensity will just be used next time pump turns on.

        await interaction.response.send_message(response_message, ephemeral=True)
        # Request status update regardless of API success for the running pump,
        # as the default intensity definitely changed.
        if api_success: # Only request update if API call succeeded or wasn't needed
             await self.bot.request_status_update()

# --- Standalone Commands --- #

class PumpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Add the command groups to the cog
        bot.tree.add_command(PumpGroup(bot))
        bot.tree.add_command(LatchGroup(bot))

    async def cog_unload(self):
        if self.bot.pump_task and not self.bot.pump_task.done():
            self.bot.pump_task.cancel()
            logger.info("Cancelled running pump task on cog unload.")
        # Remove the commands when unloading
        self.bot.tree.remove_command("pump")
        self.bot.tree.remove_command("latch")
        self.bot.tree.remove_command("inflate")
        self.bot.tree.remove_command("inflate_debt")

    @app_commands.command(name="inflate", description="Runs the pump for a duration using session time.")
    @app_commands.describe(seconds="Number of seconds to run (uses default if omitted by non-privileged user).")
    @dm_wearer_on_use("inflate")
    async def inflate(self, interaction: discord.Interaction, seconds: int = None):
        is_privileged_user = interaction.user.id == self.bot.config.get('wearer_id')
        duration = seconds
        if seconds is None and not is_privileged_user:
            duration = self.bot.config.get('default_pump_duration', 30)
        elif seconds is None and is_privileged_user:
            await interaction.response.send_message("Please specify a duration in seconds.", ephemeral=True)
            return

        await _start_timed_pump(self.bot, interaction, duration)

    @app_commands.command(name="inflate_debt", description="[Wearer Only] Runs the pump using banked time.")
    @app_commands.describe(seconds="Number of seconds to run using banked time.")
    @check_is_wearer()
    @dm_wearer_on_use("inflate_debt")
    async def inflate_debt(self, interaction: discord.Interaction, seconds: int):
        await _start_banked_pump(self.bot, interaction, seconds)

async def setup(bot):
    await bot.add_cog(PumpCog(bot))
