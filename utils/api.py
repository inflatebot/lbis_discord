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

async def get_api_pump_state(bot: 'lBISBot') -> Optional[bool]:
    """Queries the API for the current pump state (PWM value).

    Args:
        bot: The bot instance.

    Returns:
        True if the pump duty cycle > 0, False if 0, None if state is unknown or API fails.
    """
    if not bot.device_base_url:
        logger.error("Device API base URL not configured. Cannot get pump status.")
        return None

    try:
        # Use the existing api_request helper
        response_data = await api_request(bot, "getPumpState", method="GET")

        if response_data is None:
            logger.warning("get_api_pump_state: API request returned None")
            return None

        # Handle potential text response first (API returns float string like "0.00", "0.50")
        if 'message' in response_data:
            text_value = response_data['message']
            try:
                # Parse as float and check if > 0
                return float(text_value) > 0.0
            except (ValueError, TypeError):
                logger.error(f"get_api_pump_state: Could not parse text response '{text_value}' as float.")
                return None
        # Handle potential numeric response (from api_request parsing - less likely now)
        elif 'value' in response_data:
             try:
                 # Check if numeric value > 0
                return float(response_data['value']) > 0.0
             except (ValueError, TypeError):
                logger.error(f"get_api_pump_state: Could not parse numeric value '{response_data['value']}' as float.")
                return None
        # Handle potential JSON response as fallback (unlikely for this endpoint now)
        elif 'is_on' in response_data: # Keep for potential future API changes
            return bool(response_data.get('is_on'))
        else:
            logger.error(f"get_api_pump_state: Unexpected API response format: {response_data}")
            return None

    except Exception as e:
        logger.error(f"get_api_pump_state: Failed to check pump status: {e}")
        return None
