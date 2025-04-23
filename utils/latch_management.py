import asyncio
import aiohttp  # Added aiohttp
import logging  # Added logging
from .state_persistence import save_session_state

logger = logging.getLogger(__name__)  # Added logger

# --- Latch Management ---

async def toggle_latch(bot, new_state: bool, reason: str = None, duration: int = None) -> tuple[bool, str]:
    """Toggles or sets the latch state, handling timers and API calls."""
    # Cancel existing timer if any
    if bot.latch_timer:
        bot.latch_timer.cancel()
        bot.latch_timer = None
        bot.latch_end_time = None

    bot.latch_active = new_state
    bot.latch_reason = reason if new_state else None  # Set reason only if latching
    status_message = "latched" if new_state else "unlatched"
    warning_message = ""

    # If latching, ensure pump is off
    if new_state:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{bot.API_BASE_URL}/api/setPumpState",
                    json={"pump": 0},
                    timeout=10
                ) as response:
                    if response.status != 200:
                        warning_message = f"Warning: Failed to turn pump off while latching (Server status: {response.status}). Latch applied anyway."
                        logger.warning(f"Failed to turn pump off via API during latch ON: Status {response.status}")
                    # else: Pump turned off successfully
            except Exception as e:
                warning_message = f"Warning: Error contacting server to turn pump off while latching: {e}. Latch applied anyway."
                logger.error(f"Error turning pump off via API during latch ON: {e}")
                # Continue latching

        # Set up timed unlatch if duration specified
        if duration is not None and duration > 0:
            bot.latch_end_time = asyncio.get_event_loop().time() + duration
            bot.latch_timer = asyncio.create_task(auto_unlatch(bot, duration))
            status_message = f"{status_message} for {duration // 60} minutes"
            if reason:
                status_message += f" (Reason: {reason})"
        elif reason:  # Latching indefinitely with reason
            status_message += f" (Reason: {reason})"

    else:  # Unlatching
        bot.latch_end_time = None  # Clear end time when unlatching manually
        bot.latch_reason = None  # Clear reason when unlatching

    save_session_state(bot)
    final_message = f"Pump is now {status_message}."
    if warning_message:
        final_message = f"{warning_message} {final_message}"

    return True, final_message  # Return success and the message

async def set_latch_reason(bot, reason: str = None) -> tuple[bool, str]:
    """Sets or clears the latch reason if the latch is active."""
    if not bot.latch_active:
        return False, "Cannot set reason: Latch is not currently active."

    bot.latch_reason = reason
    save_session_state(bot)
    if reason:
        return True, f"Latch reason set to: {reason}"
    else:
        return True, "Latch reason cleared."

async def auto_unlatch(bot, delay):
    """Automatically unlatch after specified delay"""
    await asyncio.sleep(delay)
    if bot.latch_timer:  # Check if it wasn't cancelled
        bot.latch_active = False
        bot.latch_timer = None
        bot.latch_end_time = None
        bot.latch_reason = None  # Clear reason on auto-unlatch
        save_session_state(bot)  # Use the imported function
        logger.info("Timed latch expired.")  # Use logger
        if bot.config.get('wearer_id'):  # Check config for wearer_id
            try:
                wearer = await bot.fetch_user(bot.config['wearer_id'])
                await wearer.send("Timed latch has expired - pump is now unlatched.")
                # Trigger status update after state change using the bot method
                await bot.request_status_update()
            except Exception as e:
                logger.error(f"Failed to notify wearer of auto-unlatch: {e}")  # Use logger
