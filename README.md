# LLM Chat Arena

A web application where two LLM models can chat with each other in real-time. Built with FastAPI, WebSockets, and Ollama.

## Features

- **Real-time LLM-to-LLM Chat**: Watch two AI models converse with each other
- **Model Selection**: Choose from available Ollama models
- **Custom System Prompts**: Set different personas for each model
- **Live Status Updates**: See which model is currently "thinking"
- **Conversation Control**: Start, stop, and clear chat sessions
- **Responsive Design**: Works on desktop and mobile devices

## Prerequisites

### Required Software

1. **Python 3.12+**: Make sure you have Python installed
2. **Ollama**: Download and install Ollama from [ollama.ai](https://ollama.ai)

### Install Ollama Models

You need at least two chat models installed. Here are some recommended models:

```bash
# Install Llama 3.2 (recommended)
ollama pull llama3.2

# Install other chat models
ollama pull deepseek-r1:1.5b
ollama pull llama3.1:8b
ollama pull mistral
ollama pull codellama
```

**Note**: Some models like `mxbai-embed-large` are embedding models and cannot be used for chat conversations.

## Installation

1. **Clone or download** this project to your local machine

2. **Install dependencies**:
   ```bash
   cd agentchat
   pip install -e .
   ```

3. **Start Ollama** (if not already running):
   ```bash
   ollama serve
   ```

4. **Run the application**:
   ```bash
   python -m app.main
   ```

5. **Open your browser** and navigate to:
   ```
   http://localhost:8000
   ```

## Usage

1. **Select Models**: Choose two different chat models from the dropdown menus
2. **Set System Prompts** (optional): Add custom instructions for each model
3. **Configure Initial Message**: Set the starting message for the conversation
4. **Start Chat**: Click the "Start Chat" button to begin the conversation
5. **Watch the Conversation**: The models will automatically alternate responses
6. **Control the Chat**: Use Stop/Clear buttons as needed

### Example System Prompts

- **Model 1**: "You are an enthusiastic AI assistant who loves to ask questions."
- **Model 2**: "You are a thoughtful AI assistant who provides detailed, philosophical answers."

## API Endpoints

### REST API

- `GET /api/models` - List available Ollama models
- `GET /health` - Health check endpoint
- `GET /` - Main web interface

### WebSocket

- `WS /api/ws` - Real-time chat communication

#### WebSocket Message Types

- `start_chat`: Start a new chat session
- `stop_chat`: Stop the current chat session
- `clear_chat`: Clear conversation history
- `message`: Chat message from a model
- `thinking`: Model is generating response
- `error`: Error message

## Project Structure

```
agentchat/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI application entry point
│   ├── api.py           # API routes and WebSocket handling
│   └── services.py      # Ollama service and LLM wrapper
├── templates/
│   └── index.html       # Main HTML interface
├── static/
│   ├── style.css        # CSS styling
│   └── script.js        # JavaScript WebSocket client
├── pyproject.toml       # Project dependencies
├── .gitignore           # Git ignore rules
└── README.md           # This file
```

## Troubleshooting

### Common Issues

1. **"Failed to fetch models" error**
   - Ensure Ollama is running (`ollama serve`)
   - Check that Ollama is accessible at `http://localhost:11434`

2. **"Failed to get response from model" error**
   - Verify that the selected models are chat models (not embedding models)
   - Check if models are properly installed (`ollama list`)

3. **WebSocket connection issues**
   - Refresh the page and try again
   - Check browser console for error messages

4. **Models not responding**
   - Ensure you have sufficient RAM for the selected models
   - Try smaller models if you're having resource issues

### Model Recommendations

For best performance, consider these combinations:

- **Lightweight**: `deepseek-r1:1.5b` + `llama3.2:latest`
- **Balanced**: `llama3.2:latest` + `mistral`
- **High Performance**: `llama3.1:8b` + `codellama`

## Development

### Running in Development Mode

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run with auto-reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Note**: You can also run the application using `python -m app.main` as shown in the installation steps.

### Code Style

The project follows standard Python conventions with:
- Type hints where appropriate
- Clear function and variable names
- Minimal dependencies
- Simple, modular design

## License

This project is open source and available under the MIT License.

## Contributing

Contributions are welcome! Please keep the design simple and focused on core functionality.