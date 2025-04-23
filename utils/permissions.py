import discord
import asyncio
import functools
import logging
from discord.ext import commands

logger = logging.getLogger(__name__)

# --- Permissions & Notifications ---

# Placeholder for future, more complex permission logic
# For now, "privileged" is the same as being the wearer.
def is_privileged():
    async def predicate(interaction: discord.Interaction) -> bool:
        # Ensure bot and config are accessible
        bot = interaction.client
        if not hasattr(bot, 'config') or 'wearer_id' not in bot.config:
            # Handle missing config gracefully (log error, deny permission)
            print("Error: Bot config or wearer_id not found for permission check.") # Replace with proper logging
            return False
        return interaction.user.id == bot.config.get('wearer_id')
    return commands.check(predicate)

def is_wearer():
    async def predicate(interaction: discord.Interaction) -> bool:
        # Ensure bot and config are accessible
        bot = interaction.client
        if not hasattr(bot, 'config') or 'wearer_id' not in bot.config:
             # Handle missing config gracefully (log error, deny permission)
            print("Error: Bot config or wearer_id not found for permission check.") # Replace with proper logging
            return False
        return interaction.user.id == bot.config.get('wearer_id')
    return commands.check(predicate)

# Decorator to check if the user is the wearer
def check_is_wearer():
     return is_wearer()

# Decorator to check if the user is privileged
def check_is_privileged():
    return is_privileged()

async def notify_wearer(bot, interaction: discord.Interaction, command_name: str):
    if not hasattr(bot, 'config') or 'wearer_id' not in bot.config or interaction.user.id == bot.config.get('wearer_id'):
        return  # Don't notify if no wearer set or if wearer uses command

    try:
        wearer = await bot.fetch_user(bot.config.get('wearer_id'))
        if wearer:
            location = "Direct Messages" if interaction.guild is None else f"{interaction.guild.name} / #{interaction.channel.name}"
            user_info = f"{interaction.user} ({interaction.user.id})"

            # Get command parameters
            params = []
            if interaction.data and "options" in interaction.data:
                for option in interaction.data.get("options", []):
                    params.append(f"{option['name']}:{option['value']}")
            param_str = " " + " ".join(params) if params else ""

            await wearer.send(
                f"Command `{command_name}{param_str}` used by {user_info} in {location}."
            )
    except Exception as e:
        logger.error(f"Failed to notify wearer for command '{command_name}': {e}")

def dm_wearer_on_use(command_name):
    """Decorator to notify the wearer when a command is used.

    Works for both regular cog commands and commands within an app_commands.Group.
    """
    def decorator(func):
        if not asyncio.iscoroutinefunction(func):
            raise TypeError("Decorated function must be a coroutine.")

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # The interaction object is usually the last positional argument
            # or the first if it's a method of a Group/Cog without other args.
            # Find the interaction object in the arguments.
            interaction: discord.Interaction = None
            instance = None # Could be Cog or Group instance
            if args:
                instance = args[0]
                # Check common patterns: (self, interaction, ...), (interaction, ...)
                if len(args) > 1 and isinstance(args[1], discord.Interaction):
                    interaction = args[1]
                elif isinstance(args[0], discord.Interaction):
                    interaction = args[0]
                    instance = None # No instance if interaction is first arg

            if interaction is None:
                logger.error(f"Could not find discord.Interaction object for command '{command_name}' in dm_wearer_on_use decorator.")
                # Decide how to handle this: maybe call func anyway or raise error?
                # For now, let's call the original function but log the error.
                return await func(*args, **kwargs)

            # Get the bot instance from the interaction
            bot = interaction.client

            # Notify the wearer
            await notify_wearer(bot, interaction, command_name)

            # Call the original command function
            return await func(*args, **kwargs)
        return wrapper
    return decorator
