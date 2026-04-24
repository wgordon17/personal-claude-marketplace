## Feature Request

Add rate limiting to the API to prevent abuse. We've been seeing occasional bursts of requests from scrapers.

## Codebase Context

```python
# requirements.txt (relevant excerpt)
fastapi==0.115.0
slowapi==0.1.9
redis==5.0.1
uvicorn==0.30.0
sqlalchemy==2.0.30
```

```python
# src/config.py
import os
import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

DATABASE_URL = os.environ.get("DATABASE_URL")
RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "false").lower() == "true"
```

```python
# src/main.py
from fastapi import FastAPI, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

app = FastAPI()
limiter = Limiter(key_func=get_remote_address, storage_uri=REDIS_URL)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})


# --- Auth endpoints (ALREADY rate-limited) ---

@app.post("/auth/login")
@limiter.limit("5/minute")
async def login(request: Request, credentials: LoginRequest):
    ...

@app.post("/auth/register")
@limiter.limit("3/minute")
async def register(request: Request, user: CreateUserRequest):
    ...


# --- Task endpoints (NO rate limiting) ---

@app.get("/api/tasks")
async def list_tasks(request: Request):
    tasks = await TaskService.list_for_user(request.state.user_id)
    return tasks

@app.post("/api/tasks")
async def create_task(request: Request, task: CreateTaskRequest):
    return await TaskService.create(task, owner_id=request.state.user_id)

@app.get("/api/tasks/{task_id}")
async def get_task(request: Request, task_id: int):
    return await TaskService.get(task_id, request.state.user_id)

@app.put("/api/tasks/{task_id}")
async def update_task(request: Request, task_id: int, task: UpdateTaskRequest):
    return await TaskService.update(task_id, task, request.state.user_id)


# --- Project endpoints (NO rate limiting) ---

@app.get("/api/projects")
async def list_projects(request: Request):
    return await ProjectService.list_for_user(request.state.user_id)

@app.post("/api/projects")
async def create_project(request: Request, project: CreateProjectRequest):
    return await ProjectService.create(project, owner_id=request.state.user_id)


# --- Report endpoint (NO rate limiting, CPU-intensive) ---

@app.post("/api/reports/generate")
async def generate_report(request: Request, params: ReportParams):
    """Generate analytical report. Joins 5 tables with aggregation — CPU-intensive."""
    return await ReportService.generate(params, request.state.user_id)


# --- Health check (should NOT be rate-limited) ---

@app.get("/health")
async def health():
    return {"status": "ok"}
```

```python
# src/middleware/auth.py
from src.config import redis_client

async def verify_token(token: str) -> dict | None:
    """Verify JWT and check revocation cache in Redis."""
    cached = redis_client.get(f"token:{token}")
    if cached:
        return json.loads(cached)
    # ... JWT verification logic ...
```
