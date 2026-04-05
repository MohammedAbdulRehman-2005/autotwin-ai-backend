import os

structure = {
    "backend/main.py": """from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import router
from core.config import settings

app = FastAPI(title=settings.PROJECT_NAME, version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix=settings.API_V1_STR)

@app.get("/")
async def root():
    return {"message": "Welcome to AutoTwin AI Backend"}

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": settings.PROJECT_NAME}
""",
    "backend/api/__init__.py": "",
    "backend/api/routes.py": """from fastapi import APIRouter, Depends
from models.schemas import AnalysisRequest, AnalysisResponse
from services.orchestrator import Orchestrator

router = APIRouter()
orchestrator = Orchestrator()

@router.post("/analyze", response_model=AnalysisResponse)
async def analyze_endpoint(request: AnalysisRequest):
    return await orchestrator.process(request)
""",
    "backend/api/dependencies.py": """from fastapi import Header, HTTPException

async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != "development_secret":
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return x_api_key
""",
    "backend/core/__init__.py": "",
    "backend/core/config.py": """from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "AutoTwin AI"
    API_V1_STR: str = "/api"
    
    class Config:
        env_file = ".env"

settings = Settings()
""",
    "backend/core/security.py": """def hash_password(password: str) -> str:
    # Placeholder for password hashing
    return password + "hash"
""",
    "backend/models/__init__.py": "",
    "backend/models/schemas.py": """from pydantic import BaseModel
from typing import Any, Dict, Optional

class AnalysisRequest(BaseModel):
    query: str
    metadata: Optional[Dict[str, Any]] = None

class AnalysisResponse(BaseModel):
    status: str
    confidence: float
    result: Any
""",
    "backend/models/database.py": """# Placeholder for SQLAlchemy base and session
def get_db():
    yield None
""",
    "backend/services/__init__.py": "",
    "backend/services/orchestrator.py": """from models.schemas import AnalysisRequest, AnalysisResponse
from services.confidence import ConfidenceManager
from services.decision import DecisionEngine

class Orchestrator:
    def __init__(self):
        self.confidence_manager = ConfidenceManager()
        self.decision_engine = DecisionEngine()

    async def process(self, request: AnalysisRequest) -> AnalysisResponse:
        # Mock processing logic
        confidence = self.confidence_manager.calculate_confidence(request.query)
        decision = self.decision_engine.make_decision(confidence)
        
        return AnalysisResponse(
            status="success",
            confidence=confidence,
            result={"decision": decision, "query": request.query}
        )
""",
    "backend/services/confidence.py": """class ConfidenceManager:
    def calculate_confidence(self, data: str) -> float:
        # Placeholder confidence calculation
        return 0.95
""",
    "backend/services/decision.py": """class DecisionEngine:
    def make_decision(self, confidence: float) -> str:
        if confidence > 0.8:
            return "Action executed"
        return "Manual review required"
""",
    "backend/services/memory.py": """class MemoryStore:
    def __init__(self):
        self.store = {}
        
    def save_context(self, key: str, value: any):
        self.store[key] = value
""",
    "backend/services/logger.py": """import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("autotwin_ai")

def get_logger():
    return logger
""",
    "backend/services/agents/__init__.py": "",
    "backend/services/agents/vision_agent.py": """class VisionAgent:
    async def analyze_image(self, image_data):
        pass
""",
    "backend/services/agents/analytics_agent.py": """class AnalyticsAgent:
    async def perform_analysis(self, data):
        pass
""",
    "backend/services/agents/browser_agent.py": """class BrowserAgent:
    async def navigate_and_extract(self, url: str):
        pass
""",
    "backend/services/agents/reflection_agent.py": """class ReflectionAgent:
    async def self_heal_workflow(self, logs):
        pass
""",
    "backend/utils/__init__.py": "",
    "backend/utils/helpers.py": """def format_output(data):
    return f"Formatted: {data}"
""",
    "backend/utils/constants.py": """DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
""",
    "backend/tests/__init__.py": "",
    "backend/tests/test_confidence.py": """from services.confidence import ConfidenceManager

def test_calculate_confidence():
    manager = ConfidenceManager()
    assert manager.calculate_confidence("test") == 0.95
""",
    "backend/tests/test_api.py": """from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
""",
    "backend/Dockerfile": """FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
""",
    "backend/docker-compose.yml": """version: '3.8'

services:
  backend:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - .:/app
    environment:
      - PORT=8000
""",
    "backend/requirements.txt": """fastapi==0.103.1
uvicorn==0.23.2
pydantic==2.3.0
pydantic-settings==2.0.3
pytest==7.4.2
httpx==0.25.0
""",
    "backend/.env.example": """API_V1_STR=/api
PROJECT_NAME=AutoTwin AI
"""
}

for path, content in structure.items():
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)

print("Scaffold generated successfully.")
