import asyncio
import aiohttp
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class OllamaService:
    """Service for interacting with Ollama API with cancellation support"""

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None
        self._active_requests: List[asyncio.Task] = []
        self._cancelled = False

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        self._cancelled = False
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Cancel all active requests
        await self.cancel_all_requests()

        if self.session:
            await self.session.close()

    def cancel_request(self):
        """Cancel all active requests for this service instance"""
        self._cancelled = True
        logger.info("Cancelling OllamaService requests")

        # Cancel all active tasks
        for task in self._active_requests:
            if not task.done():
                task.cancel()

        # Clear the list
        self._active_requests.clear()

    async def cancel_all_requests(self):
        """Cancel all active requests and wait for them to complete"""
        self.cancel_request()

        # Wait for all tasks to complete (with timeout)
        if self._active_requests:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._active_requests, return_exceptions=True),
                    timeout=2.0
                )
            except asyncio.TimeoutError:
                logger.warning("Some requests did not cancel gracefully")

            self._active_requests.clear()

    def _track_request(self, task: asyncio.Task):
        """Track a new request task"""
        self._active_requests.append(task)

        # Clean up completed tasks
        self._active_requests = [t for t in self._active_requests if not t.done()]

    def _check_cancelled(self):
        """Check if the service has been cancelled"""
        if self._cancelled:
            raise asyncio.CancelledError("OllamaService request was cancelled")

    async def get_available_models(self) -> List[str]:
        """Get list of available Ollama models (chat models only)"""
        try:
            if not self.session:
                self.session = aiohttp.ClientSession()

            async with self.session.get(f"{self.base_url}/api/tags") as response:
                if response.status == 200:
                    data = await response.json()
                    all_models = [model["name"] for model in data.get("models", [])]

                    # Filter out embedding models and non-chat models
                    chat_models = []
                    for model in all_models:
                        # Skip embedding models
                        if "embed" in model.lower():
                            logger.info(f"Skipping embedding model: {model}")
                            continue

                        # Skip known embedding models
                        if model in ["mxbai-embed-large:latest"]:
                            logger.info(f"Skipping known embedding model: {model}")
                            continue

                        # Include the model (chat models)
                        chat_models.append(model)

                    logger.info(f"Available chat models: {chat_models}")
                    return chat_models
                else:
                    logger.error(f"Failed to fetch models: HTTP {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching models: {e}")
            return []

    async def chat(self, model: str, messages: List[Dict[str, str]], timeout: float = 30.0) -> Optional[str]:
        """Send chat message to Ollama model with cancellation support"""
        try:
            # Check if cancelled before starting
            self._check_cancelled()

            if not self.session:
                self.session = aiohttp.ClientSession()

            payload = {
                "model": model,
                "messages": messages,
                "stream": False
            }

            # Create the HTTP request task with better cancellation handling
            async def make_request():
                self._check_cancelled()  # Check before making request

                try:
                    async with self.session.post(
                        f"{self.base_url}/api/chat",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=timeout)
                    ) as response:
                        self._check_cancelled()  # Check after getting response

                        if response.status == 200:
                            data = await response.json()
                            self._check_cancelled()  # Check before processing
                            return data.get("message", {}).get("content", "")
                        else:
                            error_text = await response.text() if response.content_length else "No error details"
                            logger.error(f"Chat request failed: HTTP {response.status} - {error_text}")
                            return None
                except asyncio.CancelledError:
                    logger.info("HTTP request cancelled during model call")
                    raise  # Re-raise to be caught by outer handler
                except Exception as e:
                    logger.error(f"HTTP request error for {model}: {e}")
                    raise  # Re-raise to be caught by outer handler

            # Create and track the request task
            request_task = asyncio.create_task(make_request())
            self._track_request(request_task)

            try:
                # Wait for the request with timeout
                result = await asyncio.wait_for(request_task, timeout=timeout)
                return result
            except asyncio.TimeoutError:
                logger.warning(f"Chat request timed out after {timeout} seconds for model {model}")
                return None
            except asyncio.CancelledError:
                logger.info(f"Chat request was cancelled for model {model}")
                # The task cancellation is handled by the cancel_request method
                return None

        except asyncio.CancelledError:
            logger.info(f"Chat request cancelled at service level for model {model}")
            return None
        except Exception as e:
            logger.error(f"Error in chat for model {model}: {e}")
            return None


def get_model_timeout(model_name: str) -> float:
    """Get appropriate timeout based on model name and characteristics"""
    # Default timeout for most models
    default_timeout = 60.0

    # Extended timeouts for larger/slower models
    large_model_patterns = [
        "deepseek-r1",
        "llama3:70b",
        "llama3.2",  # llama3.2 can be slow, needs extended timeout
        "codellama:70b",
        "mixtral",
        "qwen:72b",
        "yi:34b"
    ]

    # Medium timeout for mid-size models
    medium_model_patterns = [
        "llama3:8b",
        "mistral:7b",
        "qwen:7b",
        "phi",
        "gemma"
    ]

    model_lower = model_name.lower()

    # Check for large models that need more time
    for pattern in large_model_patterns:
        if pattern in model_lower:
            return 180.0  # 180 seconds (3 minutes) for large models

    # Check for medium models
    for pattern in medium_model_patterns:
        if pattern in model_lower:
            return 45.0  # 45 seconds for medium models

    # Default timeout for other models
    return default_timeout


class LLMWrapper:
    """Wrapper class for LLM model communication with cancellation support"""

    def __init__(self, model_name: str, system_prompt: str = ""):
        self.model_name = model_name
        self.system_prompt = system_prompt
        self.conversation_history: List[Dict[str, str]] = []
        self._ollama_service: Optional[OllamaService] = None
        self._cancelled = False  # Track cancellation state

        if system_prompt:
            self.conversation_history.append({
                "role": "system",
                "content": system_prompt
            })

    async def get_response(self, message: str, check_cancelled_callback=None) -> Optional[str]:
        """Get response from LLM model with cancellation support"""

        # Reset cancellation state for new request
        self._cancelled = False

        self.conversation_history.append({
            "role": "user",
            "content": message
        })

        # Check cancellation before starting
        if check_cancelled_callback:
            check_cancelled_callback()

        # Check internal cancellation state
        if self._cancelled:
            logger.info(f"LLM {self.model_name} is cancelled, not starting request")
            # Remove the user message
            if self.conversation_history and self.conversation_history[-1]["role"] == "user":
                self.conversation_history.pop()
            return None

        ollama_service = None
        try:
            # Create and track service instance for cancellation
            ollama_service = OllamaService()
            await ollama_service.__aenter__()
            self._ollama_service = ollama_service

            # Check cancellation again before making the request
            if check_cancelled_callback:
                check_cancelled_callback()

            # Check internal cancellation state again
            if self._cancelled:
                logger.info(f"LLM {self.model_name} was cancelled before request")
                # Remove the user message
                if self.conversation_history and self.conversation_history[-1]["role"] == "user":
                    self.conversation_history.pop()
                return None

            # Use model-specific timeout for better responsiveness
            model_timeout = get_model_timeout(self.model_name)
            logger.info(f"Using {model_timeout}s timeout for model {self.model_name}")

            response = await ollama_service.chat(
                self.model_name,
                self.conversation_history,
                timeout=model_timeout
            )

            # Final cancellation check after getting response
            if check_cancelled_callback:
                check_cancelled_callback()

            # Check internal cancellation state after response
            if self._cancelled:
                logger.info(f"LLM {self.model_name} was cancelled after getting response")
                # Remove the user message
                if self.conversation_history and self.conversation_history[-1]["role"] == "user":
                    self.conversation_history.pop()
                return None

            if response:
                self.conversation_history.append({
                    "role": "assistant",
                    "content": response
                })
                return response
            else:
                # Remove the user message if the request failed
                if self.conversation_history and self.conversation_history[-1]["role"] == "user":
                    self.conversation_history.pop()
                logger.info(f"LLM {self.model_name} returned no response")
                return None

        except asyncio.CancelledError:
            logger.info(f"LLM request cancelled for model {self.model_name}")
            # Remove the user message if cancelled
            if self.conversation_history and self.conversation_history[-1]["role"] == "user":
                self.conversation_history.pop()
            return None
        except Exception as e:
            logger.error(f"Error in LLM get_response for {self.model_name}: {e}")
            # Remove the user message if the request failed
            if self.conversation_history and self.conversation_history[-1]["role"] == "user":
                self.conversation_history.pop()
            return None
        finally:
            # Clean up service reference
            self._ollama_service = None
            # Always clean up the service instance
            if ollama_service:
                await ollama_service.__aexit__(None, None, None)

    def cancel_request(self):
        """Cancel the current LLM request with proper cancellation support"""
        # Set cancellation flag
        self._cancelled = True
        logger.info(f"Cancelling LLM request for {self.model_name}")

        # Cancel the OllamaService request if it exists
        if self._ollama_service:
            self._ollama_service.cancel_request()
            logger.info(f"Cancelled OllamaService request for {self.model_name}")

    async def cleanup(self):
        """Clean up resources - no-op with temporary service instances"""
        # Since we now use temporary service instances per request,
        # there's no persistent resource to clean up
        self._ollama_service = None

    def clear_history(self):
        """Clear conversation history, keeping system prompt"""
        system_prompt = self.system_prompt
        self.conversation_history = []
        if system_prompt:
            self.conversation_history.append({
                "role": "system",
                "content": system_prompt
            })

    def update_system_prompt(self, system_prompt: str):
        """Update system prompt and reset conversation"""
        self.system_prompt = system_prompt
        self.clear_history()