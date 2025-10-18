from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from typing import List, Dict, Any
import asyncio
import json
import logging
import uuid
import time
from enum import Enum

from .services import OllamaService, LLMWrapper

logger = logging.getLogger(__name__)

class SessionState(Enum):
    """States for chat session lifecycle management"""
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"

router = APIRouter()

class ConnectionManager:
    """Manages WebSocket connections and chat sessions"""

    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.chat_sessions: Dict[str, ChatSession] = {}
        self.conversation_tasks: Dict[str, List[asyncio.Task]] = {}  # Track conversation tasks per connection
        self._cleanup_task: asyncio.Task = None
        self._start_cleanup_task()

    async def connect(self, websocket: WebSocket) -> str:
        await websocket.accept()
        connection_id = str(uuid.uuid4())
        self.active_connections[connection_id] = websocket
        return connection_id

    def disconnect(self, connection_id: str):
        """Enhanced disconnect with complete session cleanup"""
        logger.info(f"Disconnecting connection {connection_id}")

        # CRITICAL: Cancel ALL conversation tasks for this connection
        self.cancel_all_conversation_tasks(connection_id)

        # Clean up session when connection is closed
        if connection_id in self.chat_sessions:
            session = self.chat_sessions[connection_id]
            logger.info(f"Cleaning up session for connection {connection_id}")

            # Stop any running conversation
            session.is_running = False

            # Reset the session completely
            session.reset_session()

            # Remove the session from the manager
            del self.chat_sessions[connection_id]
            logger.info(f"Session removed from manager for connection {connection_id}")

        if connection_id in self.active_connections:
            del self.active_connections[connection_id]
            logger.info(f"Connection removed from active connections: {connection_id}")

    def add_conversation_task(self, connection_id: str, task: asyncio.Task):
        """Add a conversation task to track for cancellation"""
        if connection_id not in self.conversation_tasks:
            self.conversation_tasks[connection_id] = []
        self.conversation_tasks[connection_id].append(task)
        logger.info(f"Added conversation task for connection {connection_id}, total: {len(self.conversation_tasks[connection_id])}")

    def cancel_all_conversation_tasks(self, connection_id: str):
        """Cancel ALL conversation tasks for a connection"""
        if connection_id not in self.conversation_tasks:
            return

        tasks = self.conversation_tasks[connection_id]
        logger.info(f"Cancelling {len(tasks)} conversation tasks for connection {connection_id}")

        for task in tasks:
            if not task.done():
                task.cancel()
                logger.info(f"Cancelled conversation task for connection {connection_id}")

        # Clear the task list
        del self.conversation_tasks[connection_id]

    async def send_personal_message(self, message: Dict[str, Any], connection_id: str):
        # Validate connection exists
        if connection_id not in self.active_connections:
            logger.warning(f"Attempted to send message to non-existent connection: {connection_id}")
            return

        message_type = message.get("type")

        # Skip session validation for control messages and thinking messages
        # Thinking messages are status updates that should show immediately
        skip_session_validation = message_type in [
            "chat_started", "chat_stopped", "chat_cleared", "error", "thinking"
        ]

        if not skip_session_validation:
            # Validate session exists and is running for chat messages
            if connection_id not in self.chat_sessions:
                logger.warning(f"Attempted to send message to non-existent session: {connection_id}")
                return

            chat_session = self.chat_sessions[connection_id]
            if not chat_session.is_running:
                logger.warning(f"Attempted to send message to stopped session: {connection_id}")
                return

            # CRITICAL: Enhanced session validation to prevent orphaned task messages
            # Skip validation for thinking messages (they're in skip_session_validation list)
            if "session_id" in message and message.get("type") != "thinking":
                if connection_id not in self.chat_sessions:
                    logger.warning(f"Rejecting message for unknown session: session_id {message['session_id']}")
                    return

                current_session = self.chat_sessions[connection_id]
                if message["session_id"] != current_session.session_id:
                    # Reject ALL messages from stale sessions (including thinking)
                    logger.warning(f"Rejecting stale message: session_id {message['session_id']} != current {current_session.session_id}")
                    return

            if "generation" in message and connection_id in self.chat_sessions:
                current_session = self.chat_sessions[connection_id]
                if message["generation"] != current_session.generation:
                    # Reject ALL messages from stale generations (including thinking)
                    logger.warning(f"Rejecting stale message: generation {message['generation']} != current {current_session.generation}")
                    return

        # Send the message if all validations pass
        websocket = self.active_connections[connection_id]
        try:
            await websocket.send_text(json.dumps(message))
        except Exception as e:
            logger.error(f"Error sending message to {connection_id}: {e}")
            # Remove dead connection
            self.disconnect(connection_id)

    def _start_cleanup_task(self):
        """Start the background cleanup task"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self):
        """Background task to clean up timed-out sessions"""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                await self._cleanup_timeout_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

    async def _cleanup_timeout_sessions(self):
        """Remove sessions that have timed out"""
        timeout_sessions = []

        for connection_id, session in self.chat_sessions.items():
            if session.is_timeout():
                timeout_sessions.append(connection_id)
                logger.info(f"Session {connection_id} timed out, marking for cleanup")

        for connection_id in timeout_sessions:
            if connection_id in self.chat_sessions:
                session = self.chat_sessions[connection_id]
                logger.info(f"Cleaning up timed out session {connection_id}")

                # Cancel any running tasks
                session.is_running = False
                if session.llm1:
                    session.llm1.cancel_request()
                if session.llm2:
                    session.llm2.cancel_request()

                # Remove the session
                del self.chat_sessions[connection_id]
                logger.info(f"Removed timed out session {connection_id}")

    def get_session(self, connection_id: str) -> 'ChatSession':
        """Get or create chat session for connection"""
        if connection_id not in self.chat_sessions:
            self.chat_sessions[connection_id] = ChatSession()

        # Update activity when accessing session
        self.chat_sessions[connection_id].update_activity()
        return self.chat_sessions[connection_id]

manager = ConnectionManager()

class ChatSession:
    """Manages a chat session between two LLMs with enhanced state management"""

    def __init__(self):
        self.llm1: LLMWrapper = None
        self.llm2: LLMWrapper = None
        self.is_running = False
        self.current_turn = 1
        self.conversation_history: List[Dict[str, Any]] = []
        self.state = SessionState.IDLE
        self._state_lock = asyncio.Lock()

        # Add unique session ID and generation counter for request association
        self.session_id = str(uuid.uuid4())
        self.generation = 1  # Increment on each restart

        # Add timeout tracking for orphaned task cleanup
        self.last_activity = time.time()
        self.session_timeout = 300  # 5 minutes timeout for inactive sessions

        logger.info(f"Created new ChatSession with ID: {self.session_id}, generation: {self.generation}")

    def setup_models(self, model1: str, model2: str, system_prompt1: str, system_prompt2: str):
        """Setup the two LLM models"""
        self.llm1 = LLMWrapper(model1, system_prompt1)
        self.llm2 = LLMWrapper(model2, system_prompt2)

    def clear_conversation(self):
        """Clear conversation history"""
        if self.llm1:
            self.llm1.clear_history()
        if self.llm2:
            self.llm2.clear_history()
        self.conversation_history = []
        self.current_turn = 1

    async def set_state(self, new_state: SessionState):
        """Safely update session state with logging"""
        async with self._state_lock:
            old_state = self.state
            self.state = new_state
            logger.info(f"Session state changed: {old_state.value} -> {new_state.value}")

    def get_state(self) -> SessionState:
        """Get current session state"""
        return self.state

    def reset_session(self):
        """Completely reset the chat session state"""
        logger.info(f"Resetting chat session {self.session_id}, generation {self.generation}")

        # Stop any running conversation
        self.is_running = False

        # Cancel any active LLM requests
        if self.llm1:
            self.llm1.cancel_request()
        if self.llm2:
            self.llm2.cancel_request()

        # Clear conversation history
        self.clear_conversation()

        # Reset models to None to force fresh setup
        self.llm1 = None
        self.llm2 = None

        # Reset state to idle
        self.state = SessionState.IDLE

        logger.info(f"Chat session {self.session_id} reset complete")

    def increment_generation(self):
        """Increment generation counter for session restart"""
        self.generation += 1
        logger.info(f"Session {self.session_id} incremented to generation {self.generation}")

    def get_session_info(self) -> Dict[str, Any]:
        """Get session identifying information for message association"""
        return {
            "session_id": self.session_id,
            "generation": self.generation
        }

    def update_activity(self):
        """Update the last activity timestamp"""
        self.last_activity = time.time()

    def is_timeout(self) -> bool:
        """Check if session has timed out due to inactivity"""
        return time.time() - self.last_activity > self.session_timeout


@router.get("/models", response_model=List[str])
async def get_available_models():
    """Get list of available Ollama models"""
    async with OllamaService() as ollama:
        models = await ollama.get_available_models()
        return models

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time chat"""
    connection_id = await manager.connect(websocket)
    logger.info(f"WebSocket connected: {connection_id}")
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            await handle_websocket_message(message, connection_id)

    except WebSocketDisconnect:
        manager.disconnect(connection_id)
        logger.info(f"WebSocket disconnected: {connection_id}")
    except Exception as e:
        logger.error(f"WebSocket error for {connection_id}: {e}")
        manager.disconnect(connection_id)

async def handle_websocket_message(message: Dict[str, Any], connection_id: str):
    """Handle incoming WebSocket messages"""

    message_type = message.get("type")
    logger.info(f"[WEBSOCKET] Received message type '{message_type}' for connection {connection_id}")

    if message_type == "start_chat":
        logger.info(f"[WEBSOCKET] Handling start_chat for connection {connection_id}")
        await start_chat_session(message, connection_id)
    elif message_type == "stop_chat":
        logger.info(f"[WEBSOCKET] Handling stop_chat for connection {connection_id}")
        await stop_chat_session(connection_id)
    elif message_type == "clear_chat":
        logger.info(f"[WEBSOCKET] Handling clear_chat for connection {connection_id}")
        await clear_chat(connection_id)
    else:
        logger.warning(f"[WEBSOCKET] Unknown message type '{message_type}' for connection {connection_id}")
        await manager.send_personal_message({
            "type": "error",
            "message": "Unknown message type"
        }, connection_id)

async def start_chat_session(message: Dict[str, Any], connection_id: str):
    """Start a chat session between two LLMs with proper session management"""

    model1 = message.get("model1")
    model2 = message.get("model2")
    system_prompt1 = message.get("system_prompt1", "")
    system_prompt2 = message.get("system_prompt2", "")
    initial_message = message.get("initial_message", "Hello!")

    if not model1 or not model2:
        await manager.send_personal_message({
            "type": "error",
            "message": "Both models must be selected"
        }, connection_id)
        return

    if model1 == model2:
        await manager.send_personal_message({
            "type": "error",
            "message": "Please select different models for Model 1 and Model 2"
        }, connection_id)
        return

    logger.info(f"Starting chat session for connection {connection_id}")

    try:
        # CRITICAL FIX: Clean up existing session BEFORE creating new one
        # First, cancel ALL conversation tasks for this connection
        manager.cancel_all_conversation_tasks(connection_id)

        if connection_id in manager.chat_sessions:
            existing_session = manager.chat_sessions[connection_id]
            logger.info(f"Found existing session for connection {connection_id}, cleaning up first")

            # Stop any running conversation
            existing_session.is_running = False

            # Cancel any active LLM requests
            if existing_session.llm1:
                existing_session.llm1.cancel_request()
            if existing_session.llm2:
                existing_session.llm2.cancel_request()

            # Reset the session completely
            existing_session.reset_session()

            # Remove the old session
            del manager.chat_sessions[connection_id]
            logger.info(f"Removed old session for connection {connection_id}")

            # No artificial delay - cleanup should be immediate

        # NOW create a fresh session AFTER cleanup
        chat_session = ChatSession()  # Create directly, don't use get_session()
        manager.chat_sessions[connection_id] = chat_session
        logger.info(f"Created new session for connection {connection_id}")

        # Setup models and conversation
        chat_session.setup_models(model1, model2, system_prompt1, system_prompt2)
        chat_session.clear_conversation()

        # Explicitly reset turn counter to ensure we always start with model 1
        chat_session.current_turn = 1

        chat_session.is_running = True
        chat_session.state = SessionState.RUNNING  # Set state directly, no async needed
        chat_session.update_activity()  # Update activity for timeout tracking

        logger.info(f"Chat session setup complete for connection {connection_id}")

        # Send confirmation to client
        await manager.send_personal_message({
            "type": "chat_started",
            "message": "Chat session started"
        }, connection_id)
        logger.info(f"Sent chat_started message for connection {connection_id}")

        # Start the conversation with the initial message as a tracked task
        conversation_task = asyncio.create_task(run_conversation_turn(initial_message, connection_id))
        manager.add_conversation_task(connection_id, conversation_task)

    except Exception as e:
        logger.error(f"Error starting chat session for connection {connection_id}: {e}")
        await manager.send_personal_message({
            "type": "error",
            "message": f"Failed to start chat session: {str(e)}"
        }, connection_id)

        # Clean up the session on error
        if connection_id in manager.chat_sessions:
            session = manager.chat_sessions[connection_id]
            session.reset_session()
            del manager.chat_sessions[connection_id]

async def stop_chat_session(connection_id: str):
    """Stop the current chat session with enhanced cleanup"""

    # CRITICAL: Cancel ALL conversation tasks FIRST, before any session operations
    manager.cancel_all_conversation_tasks(connection_id)

    if connection_id not in manager.chat_sessions:
        logger.info(f"No active session to stop for connection {connection_id}")
        return

    chat_session = manager.chat_sessions[connection_id]  # Get directly, don't create
    logger.info(f"Stopping chat session for connection {connection_id}")

    # Set stopping state immediately (no async to avoid delays)
    chat_session.state = SessionState.STOPPING

    # Stop any running conversation immediately
    chat_session.is_running = False

    # Cancel any active LLM requests immediately
    if chat_session.llm1:
        chat_session.llm1.cancel_request()
    if chat_session.llm2:
        chat_session.llm2.cancel_request()

    # Force immediate session reset to prevent race conditions
    chat_session.reset_session()

    # CRITICAL: Remove session from manager immediately to prevent conflicts
    if connection_id in manager.chat_sessions:
        del manager.chat_sessions[connection_id]
        logger.info(f"Removed session from manager for connection {connection_id}")

    logger.info(f"Chat session stopped for connection {connection_id}")

    # Send confirmation immediately
    await manager.send_personal_message({
        "type": "chat_stopped",
        "message": "Chat session stopped"
    }, connection_id)

async def clear_chat(connection_id: str):
    """Clear chat history and stop any running conversation"""
    chat_session = manager.get_session(connection_id)

    # Stop the running conversation first
    chat_session.is_running = False

    # Cancel any active LLM requests
    if chat_session.llm1:
        chat_session.llm1.cancel_request()
    if chat_session.llm2:
        chat_session.llm2.cancel_request()

    # Clear conversation history
    chat_session.clear_conversation()

    await manager.send_personal_message({
        "type": "chat_cleared",
        "message": "Chat cleared"
    }, connection_id)

async def run_conversation_turn(message: str, connection_id: str):
    """Run a single turn in the conversation with proper turn management"""

    # Get session safely
    if connection_id not in manager.chat_sessions:
        logger.warning(f"No session found for connection {connection_id}")
        return

    chat_session = manager.chat_sessions[connection_id]

    # CRITICAL: Capture session ID at start to detect if session is replaced
    original_session_id = chat_session.session_id
    logger.info(f"Starting conversation turn for session {original_session_id}")

    # Check if still running
    if not chat_session.is_running:
        logger.info(f"Conversation turn skipped - session not running")
        return

    # Determine which LLM should respond
    current_llm = chat_session.llm1 if chat_session.current_turn == 1 else chat_session.llm2
    current_model = current_llm.model_name

    logger.info(f"Starting conversation turn {chat_session.current_turn} for model {current_model}")

    # Send thinking message immediately with session info
    session_info = chat_session.get_session_info()
    await manager.send_personal_message({
        "type": "thinking",
        "model": current_model,
        "turn": chat_session.current_turn,
        **session_info
    }, connection_id)
    logger.info(f"Sent thinking message for {current_model}, turn {chat_session.current_turn}, generation {session_info['generation']}")

    # Define a callback function to check cancellation during LLM calls
    def check_cancelled():
        # CRITICAL: Check if connection still has any tracked conversation tasks
        # If this task is not in the tracked list, it means it was cancelled
        if connection_id not in manager.conversation_tasks:
            raise asyncio.CancelledError("All conversation tasks were cancelled for connection")

        if connection_id not in manager.chat_sessions:
            raise asyncio.CancelledError("Session was removed")

        current_session = manager.chat_sessions[connection_id]

        # Check if session was replaced (different session_id)
        if current_session.session_id != original_session_id:
            raise asyncio.CancelledError("Session was replaced with new session")

        if not current_session.is_running:
            raise asyncio.CancelledError("Chat was stopped by user")

    try:
        logger.info(f"Getting response from {current_model}")
        # Get response from current LLM with cancellation support
        response = await current_llm.get_response(message, check_cancelled_callback=check_cancelled)

        # Check if session still exists and is running after getting response
        if connection_id not in manager.chat_sessions:
            logger.info("Session was removed during LLM response generation")
            return

        chat_session = manager.chat_sessions[connection_id]

        # CRITICAL: Check if session was replaced (new session has different ID)
        if chat_session.session_id != original_session_id:
            logger.info(f"Session was replaced (old: {original_session_id}, new: {chat_session.session_id}), aborting old task")
            return

        if not chat_session.is_running:
            logger.info("Chat was stopped during LLM response generation")
            return

        if response:
            logger.info(f"Got response from {current_model}, length: {len(response)}")

            # Store message in conversation history
            chat_message = {
                "turn": chat_session.current_turn,
                "model": current_model,
                "message": response,
                "timestamp": str(asyncio.get_event_loop().time())
            }
            chat_session.conversation_history.append(chat_message)

            # Send response to client with session info
            session_info = chat_session.get_session_info()
            await manager.send_personal_message({
                "type": "message",
                **chat_message,
                **session_info
            }, connection_id)
            logger.info(f"Sent message from {current_model}, generation {session_info['generation']}")

            # Check if still running before continuing
            if connection_id not in manager.chat_sessions:
                logger.info("Session was removed after sending message")
                return

            chat_session = manager.chat_sessions[connection_id]

            # CRITICAL: Check if session was replaced
            if chat_session.session_id != original_session_id:
                logger.info(f"Session was replaced after sending message (old: {original_session_id}, new: {chat_session.session_id}), aborting old task")
                return

            if not chat_session.is_running:
                logger.info("Chat was stopped after response was sent")
                return

            # Switch turns and continue with validation
            old_turn = chat_session.current_turn
            chat_session.current_turn = 2 if chat_session.current_turn == 1 else 1

            # Validate turn switching (should always alternate between 1 and 2)
            if chat_session.current_turn not in [1, 2]:
                logger.warning(f"Invalid turn detected: {chat_session.current_turn}, resetting to 1")
                chat_session.current_turn = 1

            logger.info(f"Switching turns: {old_turn} -> {chat_session.current_turn}")

            # Continue conversation with a delay (check cancellation during delay)
            try:
                if connection_id not in manager.chat_sessions:
                    return

                chat_session = manager.chat_sessions[connection_id]

                # CRITICAL: Check if session was replaced before sleeping
                if chat_session.session_id != original_session_id:
                    logger.info(f"Session was replaced before delay (old: {original_session_id}, new: {chat_session.session_id}), aborting old task")
                    return

                if chat_session.is_running:
                    logger.info("Waiting 2 seconds before next turn...")
                    await asyncio.sleep(2)

                    # Final check before starting next turn
                    if connection_id in manager.chat_sessions:
                        chat_session = manager.chat_sessions[connection_id]

                        # CRITICAL: Final check if session was replaced
                        if chat_session.session_id != original_session_id:
                            logger.info(f"Session was replaced during delay (old: {original_session_id}, new: {chat_session.session_id}), aborting old task")
                            return

                        if chat_session.is_running:
                            logger.info(f"Starting next turn with message from {current_model}")
                            next_task = asyncio.create_task(run_conversation_turn(response, connection_id))
                            manager.add_conversation_task(connection_id, next_task)
            except asyncio.CancelledError:
                logger.info("Chat was stopped during delay between responses")
                return
        else:
            # Handle case where LLM returned no response with retry logic
            chat_session = manager.get_session(connection_id)
            if chat_session.is_running:
                logger.warning(f"LLM {current_model} returned no response, but chat is still running")

                # Check if we should retry (for timeout cases)
                # Get the model timeout to determine if this was likely a timeout
                from .services import get_model_timeout
                model_timeout = get_model_timeout(current_model)

                # Send a more informative error message with timeout info
                error_message = f"Failed to get response from {current_model}. "
                if model_timeout >= 90.0:
                    error_message += "This is a large model that may need more time. "
                error_message += "The chat will stop. You can try starting a new chat."

                await manager.send_personal_message({
                    "type": "error",
                    "message": error_message
                }, connection_id)

                # Ensure proper state cleanup
                chat_session.is_running = False
                logger.info(f"Chat stopped due to model failure: {current_model}")
            else:
                logger.info(f"LLM {current_model} request was cancelled, not sending error")

    except asyncio.CancelledError:
        logger.info(f"Conversation turn cancelled for {current_model}")
        # Don't send any error message - the cancellation is intentional
        return
    except Exception as e:
        logger.error(f"Error in conversation turn: {e}")
        chat_session = manager.get_session(connection_id)
        if chat_session.is_running:
            await manager.send_personal_message({
                "type": "error",
                "message": f"Error in conversation: {str(e)}"
            }, connection_id)
            chat_session.is_running = False