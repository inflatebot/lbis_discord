import asyncio
import json
import logging
import aiohttp
from typing import TYPE_CHECKING, Optional

# Added TYPE_CHECKING block for Bot hint
if TYPE_CHECKING:
    from bot import lBISBot

logger = logging.getLogger(__name__)

# --- API Interaction ---

async def api_request(bot: 'lBISBot', endpoint: str, method: str = "GET", data: dict = None, timeout: int = 5) -> dict | None:
    """
    Make a request to the lBIS API.
    NOTE: Pump control endpoints (setPumpState, getPumpState) should now use WebSockets.

    Args:
        bot: The bot instance containing API_BASE_URL
        endpoint: API endpoint (without leading slash)
        method: HTTP method (GET/POST)
        data: Optional data to send with request
        timeout: Request timeout in seconds

    Returns:
        Response data as dict if successful and response has data
        None if request failed or had no data
    """
    # Add a check or warning if trying to use this for pump endpoints
    if endpoint in ["setPumpState", "getPumpState"]:
         logger.warning(f"Attempted to use api_request for deprecated endpoint: {endpoint}. Use WebSocket instead.")
         # Optionally return None or raise an error
         # return None

    url = f"{bot.API_BASE_URL}/api/{endpoint}"
    try:
        async with aiohttp.ClientSession() as session:
            kwargs = {
                'timeout': timeout
            }
            if data:
                kwargs['json'] = data

            async with getattr(session, method.lower())(url, **kwargs) as resp:
                if resp.status != 200:
                    logger.warning(f"API request to {endpoint} failed with status {resp.status}")
                    return None

                try:
                    return await resp.json()
                except (json.JSONDecodeError, aiohttp.ContentTypeError):
                    # For endpoints that return plain text
                    text = await resp.text()
                    try:
                        # Try to handle numeric responses
                        return {"value": int(text)}
                    except ValueError:
                        return {"message": text}

    except asyncio.TimeoutError:
        logger.warning(f"API request to {endpoint} timed out after {timeout}s")
    except Exception as e:
        logger.error(f"API request to {endpoint} failed with error: {e}")

    return None
