from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.errors import AppException, app_exception_handler, general_exception_handler
from app.core.logging import RequestIDMiddleware, setup_logging
from app.routers import appointments, chat, health, properties

# Setup logging
setup_logging()

app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    version="1.0.0",
    description="AI-powered Real Estate Agent API",
)

# Middleware
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Exception handlers
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# Routers
app.include_router(health.router)
app.include_router(properties.router)
app.include_router(chat.router)  # Phase 4: Chat API
app.include_router(appointments.router)  # Phase 5: Appointments API

# Phase 6: Email worker task
email_worker_task = None


# Startup event
@app.on_event("startup")
async def startup_event():
    """Run on application startup."""
    global email_worker_task

    # Register all agent tools
    from app.agent.registry import register_all_tools
    register_all_tools()

    print("✅ AI Agent tools registered")

    # Phase 6: Start email worker if enabled
    if settings.EMAIL_WORKER_ENABLED:
        import asyncio

        from app.services.notifications import run_email_worker

        email_worker_task = asyncio.create_task(run_email_worker())
        print(f"✅ Email worker started (interval: {settings.EMAIL_WORKER_INTERVAL_SECONDS}s)")
    else:
        print("⚠️  Email worker disabled (EMAIL_WORKER_ENABLED=false)")

    print(f"✅ {settings.APP_NAME} started successfully")


# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """Run on application shutdown."""
    global email_worker_task

    # Phase 6: Stop email worker
    if email_worker_task:
        email_worker_task.cancel()
        try:
            await email_worker_task
        except Exception:
            pass  # Task cancelled
        print("✅ Email worker stopped")

    print(f"✅ {settings.APP_NAME} shutdown complete")


# Root endpoint
@app.get("/")
def read_root():
    """API root endpoint."""
    return {
        "name": settings.APP_NAME,
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "phase": "6 - Email Outbox + Summary + Handoff",
    }
