from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import uvicorn
import logging
from pathlib import Path
from .api import router as api_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:     %(message)s'
)

app = FastAPI(title="LLM Chat", description="A web app where two LLM models can chat with each other")

# Include API routes
app.include_router(api_router, prefix="/api")

# Get the project root directory
project_root = Path(__file__).parent.parent

# Mount static files
app.mount("/static", StaticFiles(directory=str(project_root / "static")), name="static")

# Setup templates
templates = Jinja2Templates(directory=str(project_root / "templates"))

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """
    Serve the main chat interface
    """
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/health")
async def health_check():
    """
    Health check endpoint
    """
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)