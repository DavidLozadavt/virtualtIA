import json
import re
import logging
from typing import Optional, Dict, Any
from openai import OpenAI, AsyncOpenAI
from core.config import settings

logger = logging.getLogger("lyra.core.llm_utils")

_client: Optional[OpenAI] = None
_async_client: Optional[AsyncOpenAI] = None


def get_openai_client() -> Optional[OpenAI]:
    """Get or create the synchronous OpenAI/OpenRouter client."""
    global _client
    if _client is not None:
        return _client

    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not found in settings.")
        return None

    try:
        if settings.LLM_PROVIDER == "openrouter":
            _client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.OPENAI_API_KEY,
            )
        else:
            _client = OpenAI(api_key=settings.OPENAI_API_KEY)
        return _client
    except Exception as e:
        logger.error(f"Error creating OpenAI client: {e}")
        return None


def get_async_openai_client() -> Optional[AsyncOpenAI]:
    """Get or create the async OpenAI/OpenRouter client.
    Uses AsyncOpenAI so that `await client.chat.completions.create()`
    does NOT block Uvicorn's event loop."""
    global _async_client
    if _async_client is not None:
        return _async_client

    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not found in settings.")
        return None

    try:
        if settings.LLM_PROVIDER == "openrouter":
            _async_client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.OPENAI_API_KEY,
            )
        else:
            _async_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        return _async_client
    except Exception as e:
        logger.error(f"Error creating AsyncOpenAI client: {e}")
        return None


def get_model() -> str:
    """Get the model name from settings."""
    return settings.OPENAI_MODEL or "openai/gpt-4o-mini"

def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Robustly extract a JSON object from text, even if wrapped in markdown or having trailing junk."""
    if not text:
        return None
        
    # Clean markdown code blocks
    text = re.sub(r'```(?:json)?\s*([\s\S]*?)\s*```', r'\1', text).strip()
    
    # Try to find the first '{' and last '}'
    start = text.find('{')
    end = text.rfind('}')
    
    if start != -1 and end != -1 and end > start:
        candidate = text[start:end+1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # Try some manual cleaning if it fails (e.g. trailing commas)
            try:
                # Remove trailing commas before closing braces/brackets
                candidate = re.sub(r',\s*([\]}])', r'\1', candidate)
                return json.loads(candidate)
            except:
                pass
    
    # Fallback: simple text match if no braces
    return None

async def call_llm(prompt: str, system_message: str = "You are a helpful assistant.", timeout: float = 10.0) -> Optional[str]:
    """Call the LLM with a prompt and return the content string.
    Uses the sync client — exists for backward compatibility.
    Prefer call_llm_async() in async contexts."""
    client = get_openai_client()
    if not client:
        return None
        
    try:
        model = get_model()
        # Note: completions.create is synchronous in standard OpenAI client, 
        # but we wrap it in async for consistent API elsewhere if needed.
        # However, for simplicity here we just use it directly.
        # In a real async environment, one might use an async client or run_in_executor.
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt}
            ],
            timeout=timeout
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"LLM call error: {e}")
        return None


async def call_llm_async(prompt: str, system_message: str = "You are a helpful assistant.", timeout: float = 10.0) -> Optional[str]:
    """Non-blocking LLM call using AsyncOpenAI. Use in Twilio/voice paths."""
    client = get_async_openai_client()
    if not client:
        return None

    try:
        model = get_model()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            timeout=timeout,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Async LLM call error: {e}")
        return None
