import asyncio
import json
import logging
import os
import platform
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

import aiofiles
import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.security import OAuth2PasswordRequestForm
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlmodel import Session, select

from auth import (
    create_access_token,
    get_current_admin,
    get_current_manager,
    get_current_user,
    hash_password,
    verify_password,
)
from database.session import get_session
from models.document import Document, DocumentUpdate
from models.user import User, UserCreate, UserResponse
from models.webhook import Webhook, WebhookCreate
from services.weather import get_weather

app = FastAPI(title="SendIt API", version="1.0.0")

# ============================================================
# CONFIGURATION
# ============================================================
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_FILE_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", 5 * 1024 * 1024))
ALLOWED_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png", ".docx"]
WEBHOOK_MAX_RETRIES = int(os.getenv("WEBHOOK_MAX_RETRIES", 3))

start_time = time.time()

# ============================================================
# RATE LIMITING
# ============================================================
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ============================================================
# LOGGING
# ============================================================
LOG_FILE = os.getenv("LOG_FILE", "app.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_start = time.time()
    response = await call_next(request)
    process_time = time.time() - request_start
    logger.info(
        f"{request.method} {request.url.path} - "
        f"Status: {response.status_code} - "
        f"Time: {process_time:.3f}s"
    )
    return response


# ============================================================
# AUTHENTICATION ENDPOINTS
# ============================================================
@app.post("/register", status_code=201)
@limiter.limit("5/minute")
def register_user(
    request: Request, user_data: UserCreate, session: Session = Depends(get_session)
):
    """Register a new user."""
    existing = session.exec(
        select(User).where(User.username == user_data.username)
    ).first()
    if existing:
        raise HTTPException(409, "Username already exists")

    existing = session.exec(select(User).where(User.email == user_data.email)).first()
    if existing:
        raise HTTPException(409, "Email already exists")

    hashed = hash_password(user_data.password)
    db_user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=hashed,
        full_name=user_data.full_name,
        role=user_data.role,
    )
    session.add(db_user)
    session.commit()
    session.refresh(db_user)

    return {
        "message": "User created successfully",
        "user": UserResponse.model_validate(db_user),
    }


@app.post("/login")
@limiter.limit("5/minute")
def login_user(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):
    """Login and receive an access token."""
    user = session.exec(select(User).where(User.username == form_data.username)).first()
    if not user:
        raise HTTPException(401, "Invalid credentials")

    if not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(401, "Invalid credentials")

    if not user.is_active:
        raise HTTPException(403, "User is inactive")

    user.last_login = datetime.utcnow()
    session.commit()

    token = create_access_token({"sub": user.username})

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 30 * 60,
        "username": user.username,
        "role": user.role,
    }


# ============================================================
# WEBHOOK DELIVERY HELPER
# ============================================================
async def fire_webhook(event_type: str, payload: dict, session: Session):
    webhooks = session.exec(
        select(Webhook).where(
            Webhook.event_type == event_type, Webhook.is_active == True
        )
    ).all()

    for webhook in webhooks:
        asyncio.create_task(_deliver_with_retry(webhook.url, event_type, payload))


async def _deliver_with_retry(url: str, event_type: str, payload: dict):
    body = {
        "event": event_type,
        "data": payload,
        "sent_at": datetime.utcnow().isoformat(),
    }
    delay = 1
    for attempt in range(1, WEBHOOK_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=body, timeout=5.0)
                if response.status_code < 300:
                    return
        except Exception as e:
            print(f"Webhook delivery attempt {attempt} to {url} failed: {e}")

        if attempt < WEBHOOK_MAX_RETRIES:
            await asyncio.sleep(delay)
            delay *= 2

    print(f"Webhook to {url} failed after {WEBHOOK_MAX_RETRIES} attempts, giving up.")


# ============================================================
# FILE UPLOAD ENDPOINTS
# ============================================================
@app.post("/documents/upload")
@limiter.limit("10/hour")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    city: str = Form(...),
    description: str | None = Form(None),
    country: str = Form("Kenya"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Upload a document with validation. Enriches with weather data. Handles versioning."""
    file_extension = os.path.splitext(file.filename)[1].lower()
    if file_extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400, f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    contents = await file.read()
    file_size = len(contents)
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            400, f"File too large. Max size: {MAX_FILE_SIZE // (1024*1024)} MB"
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_filename = f"{timestamp}_{current_user.id}_{file.filename.replace(' ', '_')}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)

    async with aiofiles.open(file_path, "wb") as out_file:
        await out_file.write(contents)

    existing_latest = session.exec(
        select(Document).where(
            Document.original_filename == file.filename,
            Document.uploader_id == current_user.id,
            Document.is_latest == True,
        )
    ).first()

    new_version = 1
    parent_id = None
    if existing_latest:
        new_version = existing_latest.version + 1
        parent_id = existing_latest.id
        existing_latest.is_latest = False
        session.add(existing_latest)

    document = Document(
        filename=safe_filename,
        original_filename=file.filename,
        file_size=file_size,
        file_type=file.content_type or "application/octet-stream",
        city=city,
        country=country,
        description=description,
        uploader_id=current_user.id,
        file_path=file_path,
        status="processing",
        version=new_version,
        parent_document_id=parent_id,
        is_latest=True,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    await fire_webhook(
        "document.uploaded",
        {
            "document_id": document.id,
            "filename": document.original_filename,
            "version": document.version,
            "uploader_id": current_user.id,
        },
        session,
    )

    try:
        weather_data = await get_weather(city, country)
        if weather_data and "error" not in weather_data:
            document.weather_data = json.dumps(weather_data)
            document.weather_fetched_at = datetime.utcnow()
            document.status = "enriched"
            session.commit()

            await fire_webhook(
                "document.enriched",
                {
                    "document_id": document.id,
                    "filename": document.original_filename,
                    "weather": weather_data,
                },
                session,
            )
        else:
            document.status = "uploaded"
            session.commit()
    except Exception as e:
        print(f"Weather API error: {e}")
        document.status = "uploaded"
        session.commit()

    return {
        "message": "Document uploaded successfully",
        "document_id": document.id,
        "filename": document.original_filename,
        "version": document.version,
        "status": document.status,
    }


@app.get("/documents")
@limiter.limit("30/minute")
def list_documents(
    request: Request,
    status: str | None = None,
    city: str | None = None,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """List all documents with optional filters. Only shows latest versions."""
    query = select(Document).where(Document.is_latest == True)

    if current_user.role not in ["admin", "manager"]:
        query = query.where(Document.uploader_id == current_user.id)

    if status:
        query = query.where(Document.status == status)
    if city:
        query = query.where(Document.city == city)

    return session.exec(query).all()


@app.get("/documents/search")
@limiter.limit("20/minute")
def search_documents(
    request: Request,
    q: str | None = None,
    city: str | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Search documents with multiple filters."""
    query = select(Document).where(Document.is_latest == True)

    if current_user.role not in ["admin", "manager"]:
        query = query.where(Document.uploader_id == current_user.id)

    if q:
        query = query.where(
            (Document.original_filename.ilike(f"%{q}%"))
            | (Document.description.ilike(f"%{q}%"))
        )
    if city:
        query = query.where(Document.city == city)
    if status:
        query = query.where(Document.status == status)
    if date_from:
        query = query.where(Document.uploaded_at >= date_from)
    if date_to:
        query = query.where(Document.uploaded_at <= date_to)

    return session.exec(query).all()


@app.get("/documents/{document_id}/versions")
@limiter.limit("30/minute")
def get_document_versions(
    request: Request,
    document_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Return the full version chain for a document."""
    document = session.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found")

    if (
        current_user.role not in ["admin", "manager"]
        and document.uploader_id != current_user.id
    ):
        raise HTTPException(403, "Access denied")

    root = document
    while root.parent_document_id is not None:
        root = session.get(Document, root.parent_document_id)

    chain = [root]
    current = root
    while True:
        child = session.exec(
            select(Document).where(Document.parent_document_id == current.id)
        ).first()
        if not child:
            break
        chain.append(child)
        current = child

    return chain


@app.get("/documents/{document_id}")
@limiter.limit("30/minute")
def get_document(
    request: Request,
    document_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Get a specific document."""
    document = session.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found")

    if (
        current_user.role not in ["admin", "manager"]
        and document.uploader_id != current_user.id
    ):
        raise HTTPException(403, "Access denied")

    return document


@app.patch("/documents/{document_id}")
def update_document(
    document_id: int,
    document_update: DocumentUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Update a document's metadata."""
    document = session.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found")

    if (
        current_user.role not in ["admin", "manager"]
        and document.uploader_id != current_user.id
    ):
        raise HTTPException(403, "Access denied")

    for key, value in document_update.dict(exclude_unset=True).items():
        setattr(document, key, value)

    document.updated_at = datetime.utcnow()
    session.commit()
    session.refresh(document)
    return document


@app.delete("/documents/{document_id}")
def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_manager),
    session: Session = Depends(get_session),
):
    """Delete a document (managers and admins only)."""
    document = session.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found")

    if os.path.exists(document.file_path):
        os.remove(document.file_path)

    session.delete(document)
    session.commit()
    return {"message": "Document deleted successfully"}


# ============================================================
# DOCUMENT ENRICHMENT
# ============================================================
@app.post("/documents/{document_id}/enrich")
@limiter.limit("5/minute")
async def enrich_document(
    request: Request,
    document_id: int,
    current_user: User = Depends(get_current_manager),
    session: Session = Depends(get_session),
):
    """Manually trigger weather enrichment for a document."""
    document = session.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found")

    if document.status == "enriched":
        return {"message": "Document already enriched"}

    weather_data = await get_weather(document.city, document.country)
    if weather_data and "error" not in weather_data:
        document.weather_data = json.dumps(weather_data)
        document.weather_fetched_at = datetime.utcnow()
        document.status = "enriched"
        session.commit()

        await fire_webhook(
            "document.enriched",
            {
                "document_id": document.id,
                "filename": document.original_filename,
                "weather": weather_data,
            },
            session,
        )

        return {"message": "Document enriched successfully", "weather": weather_data}
    else:
        document.status = "failed"
        session.commit()
        raise HTTPException(500, "Failed to enrich document with weather data")


@app.get("/documents/{document_id}/weather")
@limiter.limit("10/minute")
def get_document_weather(
    request: Request,
    document_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Get the weather data associated with a document."""
    document = session.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found")

    if (
        current_user.role not in ["admin", "manager"]
        and document.uploader_id != current_user.id
    ):
        raise HTTPException(403, "Access denied")

    if not document.weather_data:
        raise HTTPException(404, "No weather data available for this document")

    return {
        "document_id": document.id,
        "city": document.city,
        "country": document.country,
        "weather": json.loads(document.weather_data),
    }


# ============================================================
# ADMIN USER MANAGEMENT
# ============================================================
@app.get("/users", response_model=list[UserResponse])
def list_users(
    admin: User = Depends(get_current_admin), session: Session = Depends(get_session)
):
    """List all users (admin only)."""
    return session.exec(select(User)).all()


@app.get("/users/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int,
    admin: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
):
    """Get a specific user (admin only)."""
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return user


# ============================================================
# WEBHOOKS
# ============================================================
@app.post("/webhooks/register", status_code=201)
def register_webhook(
    webhook_data: WebhookCreate,
    current_user: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
):
    """Register a webhook URL for a document event type (admin only)."""
    valid_events = ["document.uploaded", "document.enriched", "document.failed"]
    if webhook_data.event_type not in valid_events:
        raise HTTPException(400, f"event_type must be one of {valid_events}")

    webhook = Webhook(
        url=webhook_data.url,
        event_type=webhook_data.event_type,
        secret=webhook_data.secret,
        created_by=current_user.id,
    )
    session.add(webhook)
    session.commit()
    session.refresh(webhook)
    return {"message": "Webhook registered", "webhook_id": webhook.id}


@app.get("/webhooks")
def list_webhooks(
    current_user: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
):
    """List all registered webhooks (admin only)."""
    return session.exec(select(Webhook)).all()


@app.delete("/webhooks/{webhook_id}")
def delete_webhook(
    webhook_id: int,
    current_user: User = Depends(get_current_admin),
    session: Session = Depends(get_session),
):
    """Deactivate a webhook (admin only)."""
    webhook = session.get(Webhook, webhook_id)
    if not webhook:
        raise HTTPException(404, "Webhook not found")
    webhook.is_active = False
    session.commit()
    return {"message": "Webhook deactivated"}


# ============================================================
# MONITORING
# ============================================================
@app.get("/health")
def health_check():
    """Health check endpoint for uptime monitoring (no auth required)."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "uptime_seconds": round(time.time() - start_time, 2),
        "system": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
    }


@app.get("/metrics")
def get_metrics(current_user: User = Depends(get_current_admin)):
    """Server resource metrics (admin only)."""
    import psutil

    return {
        "cpu_percent": psutil.cpu_percent(),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_usage_percent": psutil.disk_usage("/").percent,
    }
