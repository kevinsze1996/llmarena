class LLMChat {
    constructor() {
        this.ws = null;
        this.isConnected = false;
        this.isChatting = false;
        this.models = [];
        this.stopRequested = false;
        this.reconnectTimeout = null;
        this.reconnectAttempts = 0;

        // Status update queue and timing control
        this.statusUpdateQueue = [];
        this.statusUpdateTimer = null;
        this.currentStatusTimeout = null;

        this.initializeElements();
        this.bindEvents();
        this.loadModels();
    }

    initializeElements() {
        // Model selection
        this.model1Select = document.getElementById('model1');
        this.model2Select = document.getElementById('model2');

        // System prompts
        this.system1Textarea = document.getElementById('system1');
        this.system2Textarea = document.getElementById('system2');

        // Initial message
        this.initialMessageInput = document.getElementById('initialMessage');

        // Buttons
        this.startBtn = document.getElementById('startBtn');
        this.stopBtn = document.getElementById('stopBtn');
        this.clearBtn = document.getElementById('clearBtn');

        // Chat panels
        this.model1Name = document.getElementById('model1Name');
        this.model2Name = document.getElementById('model2Name');
        this.messages1 = document.getElementById('messages1');
        this.messages2 = document.getElementById('messages2');
        this.status1 = document.getElementById('status1');
        this.status2 = document.getElementById('status2');

        // Error container
        this.errorContainer = document.getElementById('errorContainer');
        this.errorMessage = document.getElementById('errorMessage');
    }

    bindEvents() {
        this.startBtn.addEventListener('click', () => {
            // Fallback: If start button is disabled but shouldn't be, force reset state
            if (this.startBtn.disabled && !this.isChatting) {
                console.log('[Fallback] Start button was incorrectly disabled, forcing reset');
                this.isChatting = false;
                this.stopRequested = false;
                this.updateButtonStates();
            }
            this.startChat();
        });
        this.stopBtn.addEventListener('click', () => this.stopChat());
        this.clearBtn.addEventListener('click', () => this.clearChat());

        // Update model names when selection changes
        this.model1Select.addEventListener('change', () => this.updateModelNames());
        this.model2Select.addEventListener('change', () => this.updateModelNames());
    }

    async loadModels() {
        try {
            const response = await fetch('/api/models');
            if (response.ok) {
                this.models = await response.json();
                this.populateModelSelects();
            } else {
                this.showError('Failed to load available models');
            }
        } catch (error) {
            this.showError('Error loading models: ' + error.message);
        }
    }

    populateModelSelects() {
        // Clear existing options except the first one
        this.model1Select.innerHTML = '<option value="">Select a model...</option>';
        this.model2Select.innerHTML = '<option value="">Select a model...</option>';

        this.models.forEach(model => {
            const option1 = document.createElement('option');
            option1.value = model;
            option1.textContent = model;
            this.model1Select.appendChild(option1);

            const option2 = document.createElement('option');
            option2.value = model;
            option2.textContent = model;
            this.model2Select.appendChild(option2);
        });
    }

    updateModelNames() {
        this.model1Name.textContent = this.model1Select.value || 'Model 1';
        this.model2Name.textContent = this.model2Select.value || 'Model 2';
    }

    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/ws`;

        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            this.isConnected = true;
            console.log('WebSocket connected');
            // Clear any reconnection timeout
            if (this.reconnectTimeout) {
                clearTimeout(this.reconnectTimeout);
                this.reconnectTimeout = null;
            }

            // Reset reconnection attempts on successful connection
            this.reconnectAttempts = 0;

            // If we had a chat session that was interrupted, reset UI state
            if (this.isChatting && !this.stopRequested) {
                console.log('Connection restored, but previous chat session was interrupted');
                // Reset UI state to indicate chat needs to be restarted
                this.isChatting = false;
                this.updateButtonStates();
                this.updateStatuses('Ready', 'Ready');
                this.showError('Previous chat session was interrupted. Please start a new chat.');
            }
        };

        this.ws.onmessage = (event) => {
            this.handleMessage(JSON.parse(event.data));
        };

        this.ws.onclose = (event) => {
            this.isConnected = false;
            console.log('WebSocket disconnected:', event.code, event.reason);

            // Only attempt reconnection if not a normal closure and we haven't stopped intentionally
            if (event.code !== 1000 && !this.stopRequested) {
                this.scheduleReconnection();
            } else {
                // Normal closure or intentional stop - update button states
                this.isChatting = false;
                this.updateButtonStates();
            }
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.showError('WebSocket connection error');
        };
    }

    scheduleReconnection() {
        if (this.reconnectTimeout) {
            clearTimeout(this.reconnectTimeout);
        }

        const reconnectDelay = Math.min(1000, Math.pow(2, this.reconnectAttempts || 0));
        this.reconnectAttempts = (this.reconnectAttempts || 0) + 1;

        console.log(`Attempting reconnection in ${reconnectDelay}ms (attempt ${this.reconnectAttempts})`);

        this.reconnectTimeout = setTimeout(() => {
            if (!this.isConnected && !this.stopRequested) {
                console.log('Attempting to reconnect WebSocket...');
                this.connectWebSocket();
            }
        }, reconnectDelay);
    }

    handleMessage(message) {
        console.log('[WebSocket] Received message:', message);
        const { type } = message;

        switch (type) {
            case 'chat_started':
                console.log('[WebSocket] Processing chat_started message');
                this.handleChatStarted(message);
                break;
            case 'chat_stopped':
                console.log('[WebSocket] Processing chat_stopped message');
                this.handleChatStopped(message);
                break;
            case 'chat_cleared':
                console.log('[WebSocket] Processing chat_cleared message');
                this.handleChatCleared(message);
                break;
            case 'thinking':
                console.log('[WebSocket] Processing thinking message');
                this.handleThinking(message);
                break;
            case 'message':
                console.log('[WebSocket] Processing chat message');
                this.handleChatMessage(message);
                break;
            case 'error':
                console.log('[WebSocket] Processing error message:', message.message);
                this.showError(message.message);

                // If this is a model failure error, ensure UI state is reset
                if (message.message.includes('Failed to get response') ||
                    message.message.includes('timeout') ||
                    message.message.includes('The chat will stop') ||
                    message.message.includes('Failed to start chat session')) {
                    console.log('[Error] Model failure detected, resetting UI state');
                    this.isChatting = false;
                    this.stopRequested = false;
                    this.updateButtonStates();
                    this.updateStatuses('Ready', 'Ready'); // Reset from Starting...
                }
                break;
            default:
                console.log('[WebSocket] Unknown message type:', type);
        }
    }

    handleChatStarted(message) {
        // Clear status queue and timers to ensure fresh start
        if (this.currentStatusTimeout) {
            clearTimeout(this.currentStatusTimeout);
            this.currentStatusTimeout = null;
        }
        if (this.statusUpdateTimer) {
            clearTimeout(this.statusUpdateTimer);
            this.statusUpdateTimer = null;
        }
        this.statusUpdateQueue = [];

        this.isChatting = true;
        this.stopRequested = false; // Reset flag when chat starts
        this.reconnectAttempts = 0; // Reset reconnection counter

        // If we were showing "Starting...", the backend has confirmed startup
        // Transition to appropriate state - we'll get "thinking" message soon
        // For now, keep the current status and let the thinking message update it

        this.updateButtonStates();
        this.hideError();
    }

    handleChatStopped(message) {
        console.log('[ChatStopped] Received chat_stopped message:', message);
        console.log('[ChatStopped] Before state reset - isChatting:', this.isChatting, 'stopRequested:', this.stopRequested);

        // CRITICAL: Force reset all chat-related state immediately
        this.isChatting = false;
        this.stopRequested = false;
        this.reconnectAttempts = 0;

        console.log('[ChatStopped] After state reset - isChatting:', this.isChatting);

        // Clear status update queue and timers immediately
        if (this.currentStatusTimeout) {
            clearTimeout(this.currentStatusTimeout);
            this.currentStatusTimeout = null;
        }
        if (this.statusUpdateTimer) {
            clearTimeout(this.statusUpdateTimer);
            this.statusUpdateTimer = null;
        }
        this.statusUpdateQueue = [];

        // Clear any pending stop fallback timers
        if (this._stopFallbacks) {
            this._stopFallbacks.forEach(fallbackId => clearTimeout(fallbackId));
            this._stopFallbacks = null;
        }

        // Force button states to correct configuration
        this.startBtn.disabled = false;
        this.stopBtn.disabled = false;
        this.stopBtn.textContent = 'Stop Chat';

        // Update button states
        this.updateButtonStates();

        // Additional fallback to ensure buttons are correct
        setTimeout(() => {
            console.log('[ChatStopped] Fallback button state check');
            this.startBtn.disabled = false;
            this.stopBtn.disabled = false;
            this.stopBtn.textContent = 'Stop Chat';
            this.updateButtonStates();
        }, 50);

        // Clear any status immediately
        this.updateStatusesImmediate('Ready', 'Ready');

        console.log('[ChatStopped] Chat stopped successfully');
    }

    handleChatCleared(message) {
        this.messages1.innerHTML = '';
        this.messages2.innerHTML = '';
        this.updateStatuses('Ready', 'Ready');
        this.hideError();
    }

    handleThinking(message) {
        const { model, turn } = message;
        if (turn === 1) {
            this.updateStatuses('Thinking...', 'Ready');
        } else {
            this.updateStatuses('Ready', 'Thinking...');
        }
    }

    handleChatMessage(message) {
        const { turn, model, message: content } = message;

        const messageElement = document.createElement('div');
        messageElement.className = `message model${turn}`;

        const headerElement = document.createElement('div');
        headerElement.className = 'message-header';
        headerElement.textContent = model;

        const contentElement = document.createElement('div');
        contentElement.className = 'message-content';
        contentElement.textContent = content;

        messageElement.appendChild(headerElement);
        messageElement.appendChild(contentElement);

        if (turn === 1) {
            this.messages1.appendChild(messageElement);
            this.updateStatuses('Ready', 'Chatting...');
        } else {
            this.messages2.appendChild(messageElement);
            this.updateStatuses('Chatting...', 'Ready');
        }

        // Scroll to bottom
        this.messages1.scrollTop = this.messages1.scrollHeight;
        this.messages2.scrollTop = this.messages2.scrollHeight;
    }

    startChat() {
        const model1 = this.model1Select.value;
        const model2 = this.model2Select.value;

        if (!model1 || !model2) {
            this.showError('Please select both models');
            this.updateStatuses('Ready', 'Ready'); // Reset from Starting...
            return;
        }

        if (model1 === model2) {
            this.showError('Please select different models');
            this.updateStatuses('Ready', 'Ready'); // Reset from Starting...
            return;
        }

        // Reset state flags
        this.stopRequested = false;
        this.reconnectAttempts = 0;

        // Provide immediate visual feedback that startup is beginning
        this.updateStatuses('Starting...', 'Ready');

        if (!this.isConnected) {
            this.connectWebSocket();
        }

        const startMessage = {
            type: 'start_chat',
            model1: model1,
            model2: model2,
            system_prompt1: this.system1Textarea.value,
            system_prompt2: this.system2Textarea.value,
            initial_message: this.initialMessageInput.value
        };

        // Wait for WebSocket to be ready with timeout
        let attempts = 0;
        const maxAttempts = 50; // 5 seconds max wait

        const sendStartMessage = () => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify(startMessage));
            } else if (attempts < maxAttempts) {
                attempts++;
                setTimeout(sendStartMessage, 100);
            } else {
                this.showError('Failed to connect to server. Please refresh the page and try again.');
                this.updateStatuses('Ready', 'Ready'); // Reset from Starting...
                this.updateButtonStates();
            }
        };

        sendStartMessage();
    }

    stopChat() {
        this.stopRequested = true; // Set flag to prevent reconnection

        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            // Provide immediate visual feedback
            this.updateStatusesImmediate('Stopping...', 'Stopping...');

            // Disable stop button to prevent multiple clicks
            this.stopBtn.disabled = true;
            this.stopBtn.textContent = 'Stopping...';

            // Send stop request
            this.ws.send(JSON.stringify({ type: 'stop_chat' }));

            // Multiple fallback mechanisms with shorter delays for better responsiveness
            const fallback1 = setTimeout(() => {
                if (this.stopBtn.textContent === 'Stopping...') {
                    console.log('[StopTimeout] First fallback - forcing reset');
                    this.isChatting = false;
                    this.stopRequested = false;
                    this.stopBtn.disabled = false;
                    this.stopBtn.textContent = 'Stop Chat';
                    this.updateStatusesImmediate('Ready', 'Ready');
                    this.updateButtonStates();
                }
            }, 1500);

            const fallback2 = setTimeout(() => {
                if (this.stopBtn.textContent === 'Stopping...') {
                    console.log('[StopTimeout] Second fallback - forcing reset');
                    this.isChatting = false;
                    this.stopRequested = false;
                    this.stopBtn.disabled = false;
                    this.stopBtn.textContent = 'Stop Chat';
                    this.updateStatusesImmediate('Ready', 'Ready');
                    this.updateButtonStates();
                }
            }, 3000);

            // Store fallback IDs so we can clear them if we get confirmation
            this._stopFallbacks = [fallback1, fallback2];
        } else {
            // If WebSocket is not connected, still update UI state
            this.isChatting = false;
            this.stopRequested = false;
            this.updateButtonStates();
            this.updateStatusesImmediate('Ready', 'Ready');
        }
    }

    clearChat() {
        // Always clear locally first for immediate feedback
        this.messages1.innerHTML = '';
        this.messages2.innerHTML = '';
        this.updateStatuses('Ready', 'Ready');
        this.hideError();

        // If WebSocket is connected, also clear server-side session
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'clear_chat' }));
        } else {
            // If WebSocket is not connected, try to connect and then clear
            this.connectWebSocket();

            // Wait for connection to send clear command
            const sendClearMessage = () => {
                if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({ type: 'clear_chat' }));
                } else {
                    // If connection fails after several attempts, that's okay - local clear is done
                    setTimeout(sendClearMessage, 500);
                }
            };

            // Try for a limited time
            setTimeout(() => {
                if (this.ws && this.ws.readyState !== WebSocket.OPEN) {
                    console.log('WebSocket connection failed, but local chat is cleared');
                }
            }, 3000);

            sendClearMessage();
        }
    }

    updateButtonStates() {
        console.log(`[ButtonState] Updating buttons. isChatting: ${this.isChatting}, isConnected: ${this.isConnected}`);
        console.log(`[ButtonState] Before: start.disabled=${this.startBtn.disabled}, stop.disabled=${this.stopBtn.disabled}`);

        // Calculate correct button states
        const shouldDisableStart = this.isChatting;
        const shouldDisableStop = !this.isChatting;

        // Apply button states with forced correction
        this.startBtn.disabled = shouldDisableStart;
        this.stopBtn.disabled = shouldDisableStop;
        this.clearBtn.disabled = false;

        // Force ensure stop button text is correct
        if (!this.isChatting && this.stopBtn.textContent !== 'Stop Chat') {
            this.stopBtn.textContent = 'Stop Chat';
        }

        console.log(`[ButtonState] After: start.disabled=${this.startBtn.disabled}, stop.disabled=${this.stopBtn.disabled}`);

        // Additional validation to ensure buttons are in correct state
        // This helps resolve edge cases where state gets out of sync
        if (this.isChatting && this.startBtn.disabled === false) {
            console.warn('[ButtonState] Correcting inconsistent state - forcing start button disabled');
            this.startBtn.disabled = true;
        }

        if (!this.isChatting && this.stopBtn.disabled === true) {
            console.warn('[ButtonState] Correcting inconsistent state - forcing stop button enabled');
            this.stopBtn.disabled = false;
        }
    }

    updateStatuses(status1, status2) {
        console.log(`[Status] Updating to: status1="${status1}", status2="${status2}"`);

        // Queue the status update to handle minimum display times
        this.queueStatusUpdate(status1, status2);
    }

    // Add method for immediate status updates (bypassing queue)
    updateStatusesImmediate(status1, status2) {
        console.log(`[Status] Immediate update to: status1="${status1}", status2="${status2}"`);

        // Clear any existing queue and timeouts
        if (this.currentStatusTimeout) {
            clearTimeout(this.currentStatusTimeout);
            this.currentStatusTimeout = null;
        }
        if (this.statusUpdateTimer) {
            clearTimeout(this.statusUpdateTimer);
            this.statusUpdateTimer = null;
        }

        // Clear the queue
        this.statusUpdateQueue = [];

        // Apply the status update immediately
        this.applyStatusUpdate(status1, status2);
    }

    queueStatusUpdate(status1, status2) {
        // Clear any existing timeout
        if (this.currentStatusTimeout) {
            clearTimeout(this.currentStatusTimeout);
        }

        // Add to queue with timestamp
        const statusUpdate = {
            status1,
            status2,
            timestamp: Date.now()
        };

        console.log(`[Queue] Adding status update: status1="${status1}", status2="${status2}"`);

        // If this is a thinking status, ensure minimum display time (reduced for better responsiveness)
        const minDisplayTime = 200; // 0.2 seconds minimum for thinking (reduced from 1.5s)

        // If there's already a pending update, replace it
        if (this.statusUpdateQueue.length > 0) {
            const lastUpdate = this.statusUpdateQueue[this.statusUpdateQueue.length - 1];
            // Only replace if the new update is different
            if (lastUpdate.status1 === status1 && lastUpdate.status2 === status2) {
                console.log(`[Queue] Skipping duplicate status update`);
                return; // Same status, no need to update
            }
        }

        this.statusUpdateQueue.push(statusUpdate);

        // Process the queue
        this.processStatusQueue(minDisplayTime);
    }

    processStatusQueue(minDisplayTime) {
        if (this.statusUpdateTimer) {
            clearTimeout(this.statusUpdateTimer);
        }

        const processNext = () => {
            if (this.statusUpdateQueue.length === 0) {
                console.log(`[Queue] Status queue is empty, stopping processing`);
                return;
            }

            const update = this.statusUpdateQueue.shift();
            const { status1, status2 } = update;

            console.log(`[Queue] Processing status update: status1="${status1}", status2="${status2}"`);

            // Apply the status update
            this.applyStatusUpdate(status1, status2);

            // If this was a thinking status, enforce minimum display time
            const isThinking1 = status1 === 'Thinking...';
            const isThinking2 = status2 === 'Thinking...';
            const hasThinking = isThinking1 || isThinking2;

            if (hasThinking) {
                const elapsedTime = Date.now() - update.timestamp;
                const remainingTime = Math.max(0, minDisplayTime - elapsedTime);

                console.log(`[Queue] Thinking status detected. Elapsed: ${elapsedTime}ms, Remaining: ${remainingTime}ms`);

                if (remainingTime > 0) {
                    console.log(`[Queue] Enforcing minimum thinking time: ${remainingTime}ms`);
                    this.currentStatusTimeout = setTimeout(() => {
                        console.log(`[Queue] Minimum thinking time elapsed, processing next update`);
                        this.processStatusQueue(minDisplayTime);
                    }, remainingTime);
                    return;
                }
            }

            // Process next update immediately
            this.currentStatusTimeout = setTimeout(() => {
                this.processStatusQueue(minDisplayTime);
            }, 50); // Small delay for smooth transitions
        };

        this.currentStatusTimeout = setTimeout(processNext, 50);
    }

    applyStatusUpdate(status1, status2) {
        console.log(`[Status] Applying: status1="${status1}", status2="${status2}"`);

        this.status1.textContent = status1;
        this.status2.textContent = status2;

        // Update status classes
        this.status1.className = 'status';
        this.status2.className = 'status';

        if (status1 === 'Starting...') {
            this.status1.classList.add('starting');
        } else if (status1 === 'Thinking...') {
            this.status1.classList.add('thinking');
        } else if (status1 === 'Chatting...') {
            this.status1.classList.add('chatting');
        }

        if (status2 === 'Starting...') {
            this.status2.classList.add('starting');
        } else if (status2 === 'Thinking...') {
            this.status2.classList.add('thinking');
        } else if (status2 === 'Chatting...') {
            this.status2.classList.add('chatting');
        }
    }

    showError(message) {
        console.log('[Error] Showing error message:', message);
        this.errorMessage.textContent = message;
        this.errorContainer.style.display = 'block';

        // For timeout-related errors, show longer and add helpful hint
        if (message.includes('timeout') || message.includes('large model') || message.includes('Failed to get response')) {
            // Auto-hide after 8 seconds for timeout errors (longer to read)
            setTimeout(() => {
                this.hideError();
            }, 8000);
        } else {
            // Auto-hide after 5 seconds for other errors
            setTimeout(() => {
                this.hideError();
            }, 5000);
        }
    }

    hideError() {
        this.errorContainer.style.display = 'none';
    }
}

// Initialize the chat application when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new LLMChat();
});