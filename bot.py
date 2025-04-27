import os
import discord
from discord.ext import commands
import json
import asyncio
import logging  # Import logging
from discord import app_commands  # Added for error handling
import aiohttp # Added for WebSocket

# Local imports
import utils  # Import the utils module

# Setup logger for this module
logger = logging.getLogger(__name__)

# --- Configuration Loading ---
DEFAULT_CONFIG = {
    "discord_token": "changeme",
    "api_base_url": "http://localhost:80",
    "wearer_secret": "changeme",
    "wearer_id": None,
    "max_pump_duration": 60,
    "max_session_time": 1800,
    "max_session_extension": 3600
}

if not os.path.exists('bot.json'):
    with open('bot.json', 'w') as f:
        json.dump(DEFAULT_CONFIG, f, indent=4)
    print("Created default bot.json. Please configure it and restart the bot.")
    exit()  # Exit if config was just created

# Load configuration from JSON file
with open('bot.json', 'r') as config_file:
    config = json.load(config_file)

# Validate critical configurations
if config.get('discord_token') == 'changeme' or not config.get('discord_token'):
    raise ValueError("Discord token is not set in bot.json. Please add it and restart.")

if config.get('wearer_secret') == 'changeme':
    print(
        "\n\nSecurity Warning: Default wearer secret detected!\n"
        "Please edit bot.json and change 'wearer_secret' to a secure password.\n"
        "While the bot will run, setting the wearer will not work until this is changed.\n"
    )
    # Allow bot to run but warn user

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True  # Keep if needed for prefix commands, otherwise can be false for slash commands only
# Consider discord.Intents.none() if only slash commands and no message content needed


class lBISBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Attach config and API URL directly to bot instance for easy access in cogs
        self.config = config
        self.API_BASE_URL = config.get('api_base_url', 'http://localhost:80')
        self.OWNER_ID = config.get("wearer_id", None)  # Load initial Owner/Wearer ID

        # Initialize state variables on the bot object
        self.latch_active = False
        self.latch_timer = None
        self.latch_end_time = None
        self.latch_reason = None
        self.session_time_remaining = 0
        self.session_pump_start = None
        self.service_was_up = True  # Assume service is up initially
        self.ready_note = None  # Initialize ready_note
        self.banked_time: int = 0  # Added: Banked time in seconds
        self.pump_task: asyncio.Task | None = None  # Added: Reference to the running pump task
        self.pump_task_end_time: float | None = None  # Added: Target end time for the pump task
        self.pump_intensity: float = 1.0  # Added: Current pump intensity (0.0 to 1.0)
        self.current_pump_state: float | None = None # Added: Latest state received from WebSocket

        # WebSocket State (Renamed to avoid conflict with discord.py internal 'ws')
        self.lbis_ws: aiohttp.ClientWebSocketResponse | None = None
        self.lbis_ws_url = f"ws://{self.API_BASE_URL.split('//')[1]}/ws/pump" # Construct WS URL
        self.lbis_ws_lock = asyncio.Lock()
        self._lbis_ws_ready = asyncio.Event() # Event to signal WS readiness
        self._lbis_ws_manager_task: asyncio.Task | None = None # Task for managing WS connection

        # Load persistent state
        utils.load_session_state(self)  # Pass self (the bot instance)

    async def request_status_update(self):
        """Requests the MonitorCog to update the bot's presence."""
        monitor_cog = self.get_cog('MonitorCog')
        if monitor_cog:
            # Use create_task to avoid blocking if the update takes time
            asyncio.create_task(monitor_cog.update_bot_status())
        else:
            # Log if the cog isn't loaded for some reason
            logging.warning("Tried to request status update, but MonitorCog is not loaded.")

    # --- WebSocket Management ---

    async def _ws_manager(self):
        """Manages the WebSocket connection lifecycle."""
        while not self.is_closed():
            try:
                logger.info(f"Attempting to connect WebSocket to {self.lbis_ws_url}...")
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(self.lbis_ws_url, timeout=10) as ws:
                        logger.info("WebSocket connected successfully.")
                        self.lbis_ws = ws
                        self._lbis_ws_ready.set() # Signal that WS is ready

                        # Keep connection alive and handle potential incoming messages (optional)
                        async for msg in ws:
                            # Process incoming messages for state updates
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(msg.data)
                                    if isinstance(data, dict) and 'state' in data:
                                        try:
                                            new_state = float(data['state'])
                                            if self.current_pump_state != new_state:
                                                self.current_pump_state = new_state
                                                logger.info(f"WebSocket updated pump state to: {self.current_pump_state:.2f}")
                                                # Trigger status update in MonitorCog
                                                await self.request_status_update()
                                        except (ValueError, TypeError):
                                             logger.warning(f"WebSocket received invalid state value: {data['state']}")
                                    else:
                                         logger.debug(f"WebSocket received non-state JSON: {msg.data}")
                                except json.JSONDecodeError:
                                    logger.warning(f"WebSocket received non-JSON text message: {msg.data}")
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                logger.error(f"WebSocket error received: {ws.exception()}")
                                break # Exit inner loop on error
                            elif msg.type == aiohttp.WSMsgType.CLOSED:
                                logger.warning("WebSocket connection closed by server.")
                                break # Exit inner loop if closed

            except aiohttp.ClientConnectorError as e:
                logger.warning(f"WebSocket connection failed: {e}. Retrying in 15s...")
            except asyncio.TimeoutError:
                logger.warning("WebSocket connection timed out. Retrying in 15s...")
            except Exception as e:
                logger.error(f"Unexpected WebSocket error: {e}. Retrying in 15s...", exc_info=True)

            # Cleanup before retry
            async with self.lbis_ws_lock:
                self.lbis_ws = None
                self._lbis_ws_ready.clear()
            await asyncio.sleep(15) # Wait before retrying

    async def ensure_ws_connection(self, timeout=10):
        """Waits for the WebSocket connection to be ready."""
        try:
            await asyncio.wait_for(self._lbis_ws_ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning(f"Timed out waiting for WebSocket connection after {timeout}s.")
            return False

    async def send_ws_message(self, message: dict):
        """Sends a JSON message over the WebSocket connection."""
        if not await self.ensure_ws_connection():
             logger.error("Cannot send WebSocket message: Connection not ready.")
             return False

        async with self.lbis_ws_lock:
            if self.lbis_ws and not self.lbis_ws.closed:
                try:
                    await self.lbis_ws.send_json(message)
                    logger.debug(f"WebSocket message sent: {message}")
                    return True
                except Exception as e:
                    logger.error(f"Failed to send WebSocket message: {e}")
                    # Assume connection is broken, manager task will handle reconnect
                    self.lbis_ws = None
                    self._lbis_ws_ready.clear()
                    return False
            else:
                logger.error("Cannot send WebSocket message: Connection is closed or None.")
                return False

    # --- End WebSocket Management ---

    async def setup_hook(self):
        # Start the WebSocket manager task
        self._lbis_ws_manager_task = asyncio.create_task(self._ws_manager())

        # Load Cogs
        cogs_dir = "cogs"
        for filename in os.listdir(cogs_dir):
            # Skip core.py as its functionality has been moved
            if filename == "core.py":
                continue
            if filename.endswith(".py") and not filename.startswith("_"):
                try:
                    await self.load_extension(f"{cogs_dir}.{filename[:-3]}")
                    print(f"Loaded cog: {filename[:-3]}")
                except Exception as e:
                    print(f"Failed to load cog {filename[:-3]}: {e}")

        # Sync commands after loading cogs
        try:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} command(s)")
        except Exception as e:
            print(f"Failed to sync commands: {e}")

        # Add the global error handler AFTER loading cogs
        self.tree.on_error = self.on_app_command_error

    async def on_ready(self):
        print(f'{self.user} has connected to Discord!')
        # Initial status update is handled by the MonitorCog's loop starting

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Global error handler for application commands."""
        if isinstance(error, app_commands.errors.CheckFailure):
            # Handle permission errors centrally
            # Check if response already sent before sending
            if not interaction.response.is_done():
                await interaction.response.send_message("Only the device wearer can use this command.", ephemeral=True)
            else:
                # If deferred or already responded, use followup
                try:
                    await interaction.followup.send("Only the device wearer can use this command.", ephemeral=True)
                except discord.errors.NotFound:
                    logger.warning("Could not send followup error message: Interaction expired or not found.")

    async def close(self):
        """Gracefully close resources."""
        logger.info("Closing bot...")
        if self._lbis_ws_manager_task:
            self._lbis_ws_manager_task.cancel()
        async with self.lbis_ws_lock:
            # Check the renamed attribute and use the correct 'closed' property for aiohttp websockets
            if self.lbis_ws and not self.lbis_ws.closed:
                await self.lbis_ws.close()
                logger.info("lBIS WebSocket connection closed.")
        await super().close() # Call parent close method

# --- Startup ---
def start():
# Setup logging
    # logging.basicConfig(level=logging.INFO) # Basic logging
    # discord.utils.setup_logging(level=logging.INFO) # Discord specific logging

    bot = lBISBot(command_prefix='!', intents=intents)  # Prefix needed for commands.Bot, even if only using slash commands

    bot_token = config.get('discord_token')
    # Validation already happened above

    try:
        # Use asyncio.run for better async handling if possible,
        # but bot.run() handles its own loop.
        # Ensure graceful shutdown on KeyboardInterrupt or termination signals.
        # bot.run() blocks, so cleanup needs to be handled carefully,
        # potentially via signal handlers or ensuring bot.close() is called.
        bot.run(bot_token)
    except discord.errors.LoginFailure:
        print("Failed to log in. Please check your Discord token in bot.json.")
    except KeyboardInterrupt:
        print("Bot shutdown requested via KeyboardInterrupt.")
    except Exception as e:
        print(f"An error occurred while running the bot: {e}")
    finally:
        # Attempt graceful shutdown if bot object exists
        if 'bot' in locals() and bot is not None:
             # Running close() in a new event loop if the main one is stopped/closed
             try:
                 asyncio.run(bot.close())
             except RuntimeError as e:
                 print(f"Error during final cleanup: {e}")


# --- Main Execution ---

if __name__ == "__main__":
    start()