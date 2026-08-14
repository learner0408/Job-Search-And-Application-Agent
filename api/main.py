import os
import sys
import json
import logging
import shutil
from datetime import datetime, timezone
from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

# Add parent directory to path to import tools, Agent, and database
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
from tools import search_indeed, search_linkedin, search_yc
from Agent.agent import run_agent
from database import (
    init_db, save_search, get_history, get_search, delete_search, clear_history,
    save_chat_message, get_recent_chat_messages,
    create_session, update_session, list_sessions, get_session_messages, delete_session,
    get_profile, save_profile,
    save_application, get_application, get_applications, update_application_status,
    save_resume, get_resume,
    create_user, get_user_by_email, get_user_by_provider, get_user_by_id,
    count_active_users, admit_user, link_oauth_identity, set_current_user,
)
from api.auth import (
    hash_password, verify_password, set_session_cookie, clear_session_cookie,
    get_current_user, get_service_or_user, get_owner_user,
    google_authorize_url, github_authorize_url,
    exchange_google_code, exchange_github_code,
    _register_user, _check_login_rate, _record_login_failure, _reset_login_attempts,
    _is_owner, MAX_USERS as AUTH_MAX_USERS,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Job Search Agent API")


@app.on_event("startup")
async def startup():
    init_db()

LOCAL_AGENT_PORT = os.getenv("LOCAL_AGENT_PORT", "8765")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        f"http://127.0.0.1:{LOCAL_AGENT_PORT}",
        f"http://localhost:{LOCAL_AGENT_PORT}",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SearchRequest(BaseModel):
    query: str
    location: Optional[str] = ""
    employment_type: Optional[str] = ""
    remote: Optional[bool] = False
    max_results: Optional[int] = 10


class JobListing(BaseModel):
    title: str
    description: str
    location: str
    salary: str
    link: str
    source: str


class SearchResponse(BaseModel):
    jobs: List[JobListing]


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    jobs: Optional[List[JobListing]] = None


class ProfileData(BaseModel):
    full_name: str = ""
    email: str = ""
    phone: str = ""
    linkedin_url: str = ""
    github_url: str = ""
    website: str = ""
    skills: list[str] = []
    work_history: list[dict] = []
    education: list[dict] = []


class PrepareApplyRequest(BaseModel):
    job_title: str
    company: str = ""
    location: str = ""
    salary: str = ""
    link: str = ""
    source: str = ""
    description: str = ""


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ── Auth endpoints ────────────────────────────────────────────

@app.post("/api/auth/register")
async def register(request: Request, data: RegisterRequest):
    email = data.email.lower()
    if len(data.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if get_user_by_email(email):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    result = _register_user(email, password_hash=hash_password(data.password))
    user = result["user"]
    response = JSONResponse(content={
        "user_id": user["id"],
        "email": user["email"],
        "is_admin": user["is_admin"],
        "status": user["status"],
    })
    if user["status"] == "active":
        set_session_cookie(response, user, request)
    return response


@app.post("/api/auth/login")
async def login(request: Request, data: LoginRequest):
    email = data.email.lower()
    if not _check_login_rate(email):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")

    user = get_user_by_email(email)
    if not user or not user.get("password_hash") or not verify_password(data.password, user["password_hash"]):
        _record_login_failure(email)
        logger.info("Failed login attempt for %s", email)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user["status"] == "pending":
        if count_active_users() < AUTH_MAX_USERS:
            user = admit_user(user["id"])
        else:
            raise HTTPException(status_code=403, detail="Account is on the waitlist. Signups are currently at capacity.")

    _reset_login_attempts(email)
    response = JSONResponse(content={
        "user_id": user["id"],
        "email": user["email"],
        "is_admin": user["is_admin"],
    })
    set_session_cookie(response, user, request)
    return response


@app.post("/api/auth/logout")
async def logout():
    response = JSONResponse(content={"ok": True})
    clear_session_cookie(response)
    return response


@app.get("/api/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return user


@app.get("/api/auth/google")
async def google_auth(request: Request):
    return RedirectResponse(google_authorize_url(request))


@app.get("/api/auth/google/callback")
async def google_callback(request: Request, code: str = Query(...)):
    info = await exchange_google_code(request, code)
    email = info["email"]
    user = get_user_by_provider("google", info["provider_id"])
    if not user:
        existing = get_user_by_email(email)
        if existing:
            user = link_oauth_identity(existing["id"], "google", info["provider_id"])
        else:
            result = _register_user(email, provider="google", provider_id=info["provider_id"])
            user = result["user"]
    if user["status"] == "pending":
        if count_active_users() < AUTH_MAX_USERS:
            user = admit_user(user["id"])
        else:
            return RedirectResponse("/login?waitlist=1")
    response = RedirectResponse("/")
    set_session_cookie(response, user, request)
    return response


@app.get("/api/auth/github")
async def github_auth(request: Request):
    return RedirectResponse(github_authorize_url(request))


@app.get("/api/auth/github/callback")
async def github_callback(request: Request, code: str = Query(...)):
    info = await exchange_github_code(request, code)
    email = info["email"]
    user = get_user_by_provider("github", info["provider_id"])
    if not user:
        existing = get_user_by_email(email)
        if existing:
            user = link_oauth_identity(existing["id"], "github", info["provider_id"])
        else:
            result = _register_user(email, provider="github", provider_id=info["provider_id"])
            user = result["user"]
    if user["status"] == "pending":
        if count_active_users() < AUTH_MAX_USERS:
            user = admit_user(user["id"])
        else:
            return RedirectResponse("/login?waitlist=1")
    response = RedirectResponse("/")
    set_session_cookie(response, user, request)
    return response


# ── Chat & sessions ───────────────────────────────────────────

@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(request: Request, chat_req: ChatRequest, user: dict = Depends(get_current_user)):
    logger.info("Chat request from user %s: '%s'", user["user_id"], chat_req.message[:120])
    set_current_user(user["user_id"])
    session_id = chat_req.session_id
    if not session_id:
        session_id = create_session(user_id=user["user_id"])
    save_chat_message("user", chat_req.message, session_id=session_id, user_id=user["user_id"])
    result = run_agent(chat_req.message, session_id=session_id, user_id=user["user_id"], email=user["email"])
    reply = result["reply"]
    jobs_data = result.get("jobs")
    job_listings = [JobListing(**j) for j in jobs_data] if jobs_data else None
    search_id = None
    if job_listings:
        search_id = save_search(
            query=chat_req.message,
            location="",
            source_trigger="chat",
            jobs=[j.model_dump() for j in job_listings],
            user_id=user["user_id"],
        )
    save_chat_message("agent", reply, search_id=search_id, session_id=session_id, user_id=user["user_id"])
    update_session(session_id, user_id=user["user_id"])
    return ChatResponse(reply=reply, session_id=session_id, jobs=job_listings)


@app.get("/api/sessions")
async def list_sessions_endpoint(user: dict = Depends(get_current_user)):
    set_current_user(user["user_id"])
    return {"sessions": list_sessions(user_id=user["user_id"])}


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages_endpoint(session_id: str, user: dict = Depends(get_current_user)):
    set_current_user(user["user_id"])
    return {"messages": get_session_messages(session_id, user_id=user["user_id"])}


@app.delete("/api/sessions/{session_id}")
async def delete_session_endpoint(session_id: str, user: dict = Depends(get_current_user)):
    set_current_user(user["user_id"])
    deleted = delete_session(session_id, user_id=user["user_id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


# ── Search ────────────────────────────────────────────────────

@app.post("/api/search", response_model=SearchResponse)
async def search_jobs_endpoint(request: SearchRequest, user: dict = Depends(get_current_user)):
    logger.info(
        "Search request from user %s: query='%s', location='%s', max_results=%d",
        user["user_id"], request.query, request.location, request.max_results,
    )
    set_current_user(user["user_id"])

    indeed_jobs = search_indeed(
        request.query,
        request.location,
        request.employment_type,
        request.remote,
        request.max_results
    )
    linkedin_jobs = search_linkedin(
        request.query,
        request.location,
        request.employment_type,
        request.remote,
        request.max_results
    )
    yc_jobs = search_yc(
        request.query,
        request.location,
        request.employment_type,
        request.remote,
        request.max_results,
    )

    logger.info(
        "Results: %d Indeed + %d LinkedIn + %d YC = %d total",
        len(indeed_jobs), len(linkedin_jobs), len(yc_jobs),
        len(indeed_jobs) + len(linkedin_jobs) + len(yc_jobs),
    )

    job_listings = []
    for job in indeed_jobs:
        job_listings.append(JobListing(
            title=job["title"], description=job["description"],
            location=job["location"] or "N/A", salary=job["salary"] or "N/A",
            link=job["link"], source="Indeed",
        ))
    for job in linkedin_jobs:
        job_listings.append(JobListing(
            title=job["title"], description=job["description"],
            location=job["location"] or "N/A", salary=job["salary"] or "N/A",
            link=job["link"], source="LinkedIn",
        ))
    for job in yc_jobs:
        job_listings.append(JobListing(
            title=job["title"], description=job["description"],
            location=job["location"] or "N/A", salary=job["salary"] or "N/A",
            link=job["link"], source="YC Startup",
        ))

    save_search(
        query=request.query,
        location=request.location or "",
        source_trigger="search-form",
        jobs=[j.model_dump() for j in job_listings],
        user_id=user["user_id"],
    )
    return SearchResponse(jobs=job_listings)


# ── History ───────────────────────────────────────────────────

@app.get("/api/history")
async def history_list(page: int = 1, per_page: int = 10, user: dict = Depends(get_current_user)):
    set_current_user(user["user_id"])
    items, total = get_history(page=page, per_page=per_page, user_id=user["user_id"])
    return {"searches": items, "total": total, "page": page, "per_page": per_page}


@app.get("/api/history/{search_id}")
async def history_detail(search_id: int, user: dict = Depends(get_current_user)):
    set_current_user(user["user_id"])
    search = get_search(search_id, user_id=user["user_id"])
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")
    return search


@app.delete("/api/history/{search_id}")
async def history_delete(search_id: int, user: dict = Depends(get_current_user)):
    set_current_user(user["user_id"])
    deleted = delete_search(search_id, user_id=user["user_id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Search not found")
    return {"ok": True}


@app.delete("/api/history")
async def history_clear(user: dict = Depends(get_current_user)):
    set_current_user(user["user_id"])
    count = clear_history(user_id=user["user_id"])
    return {"deleted": count}


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


# ── Profile ───────────────────────────────────────────────────

@app.get("/api/profile")
async def get_profile_endpoint(user: dict = Depends(get_service_or_user)):
    set_current_user(user["user_id"])
    p = get_profile(user_id=user["user_id"])
    if not p:
        return {
            "full_name": "", "email": "", "phone": "",
            "linkedin_url": "", "github_url": "", "website": "",
            "skills": [], "work_history": [], "education": [],
            "resume_name": "",
        }
    return p


@app.post("/api/profile")
async def save_profile_endpoint(data: ProfileData, user: dict = Depends(get_service_or_user)):
    set_current_user(user["user_id"])
    save_profile(data.model_dump(), user_id=user["user_id"])
    return {"ok": True}


# ── Resume ────────────────────────────────────────────────────

@app.post("/api/resume/upload")
async def upload_resume(file: UploadFile = File(...), user: dict = Depends(get_service_or_user)):
    set_current_user(user["user_id"])
    ext = os.path.splitext(file.filename or "resume")[1] or ".pdf"
    filename = f"resume{ext}"
    content = await file.read()
    save_resume(content, filename, file.content_type or "application/pdf", user_id=user["user_id"])
    p = get_profile(user_id=user["user_id"]) or {}
    p["resume_name"] = filename
    save_profile(p, user_id=user["user_id"])
    return {"resume_name": filename}


@app.get("/api/resume")
async def download_resume(user: dict = Depends(get_service_or_user)):
    set_current_user(user["user_id"])
    resume = get_resume(user_id=user["user_id"])
    if not resume:
        raise HTTPException(status_code=404, detail="No resume uploaded")
    return Response(
        content=resume["content"],
        media_type=resume["mime_type"],
        headers={"Content-Disposition": f'attachment; filename="{resume["filename"]}"'},
    )


# ── Cover Letter & Apply Preparation ──────────────────────────

_COVER_LETTER_PROMPT = (
    "You are writing a professional cover letter. "
    "Write a 3-paragraph cover letter tailored to the specific job and applicant below. "
    "Be concise, specific, and professional. Do not use placeholders in brackets.\n\n"
    "Job Title: {job_title}\n"
    "Company: {company}\n"
    "Job Description: {description}\n\n"
    "Applicant Skills: {skills}\n"
    "Work History: {work_history}\n"
    "Education: {education}\n\n"
    "Cover Letter:"
)


def _generate_cover_letter(job_title: str, company: str, description: str, profile: dict) -> str:
    try:
        model_name = os.getenv("CHAT_LLM_MODEL", "llama-3.3-70b-versatile")
        provider = os.getenv("CHAT_LLM_PROVIDER", "groq")
        llm = init_chat_model(model_name, model_provider=provider, temperature=0.7)
        prompt = _COVER_LETTER_PROMPT.format(
            job_title=job_title,
            company=company,
            description=description,
            skills=", ".join(profile.get("skills", [])),
            work_history=json.dumps(profile.get("work_history", []), indent=2),
            education=json.dumps(profile.get("education", []), indent=2),
        )
        result = llm.invoke(prompt)
        return result.content.strip()
    except Exception as e:
        logger.error("Cover letter generation failed: %s", e)
        return ""


@app.post("/api/apply/prepare")
async def prepare_apply(data: PrepareApplyRequest, user: dict = Depends(get_current_user)):
    set_current_user(user["user_id"])
    profile = get_profile(user_id=user["user_id"])
    skills = profile.get("skills", []) if profile else []
    work_history = profile.get("work_history", []) if profile else []
    education = profile.get("education", []) if profile else []

    cover_letter = _generate_cover_letter(
        data.job_title, data.company, data.description,
        {"skills": skills, "work_history": work_history, "education": education},
    )
    logger.info("Generated cover letter for '%s' at %s (%d chars)", data.job_title, data.company, len(cover_letter))

    app_id = save_application({
        "job_title": data.job_title,
        "company": data.company,
        "location": data.location,
        "salary": data.salary,
        "link": data.link,
        "source": data.source,
        "status": "preparing",
        "cover_letter": cover_letter,
    }, user_id=user["user_id"])

    return {"app_id": app_id, "cover_letter": cover_letter}


# ── Applications ──────────────────────────────────────────────

@app.get("/api/applications")
async def list_applications(status: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    set_current_user(user["user_id"])
    items = get_applications(status=status, user_id=user["user_id"])
    return {"applications": items}


@app.get("/api/applications/{app_id}")
async def get_application_endpoint(app_id: int, user: dict = Depends(get_service_or_user)):
    set_current_user(user["user_id"])
    app = get_application(app_id, user_id=user["user_id"])
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


_VALID_STATUSES = {"applied", "rejected", "interview", "offer"}


@app.put("/api/applications/{app_id}")
async def update_application(app_id: int, status: str = Query(...), user: dict = Depends(get_service_or_user)):
    set_current_user(user["user_id"])
    if status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status '{status}'. Must be one of: {', '.join(sorted(_VALID_STATUSES))}")
    now = datetime.now(timezone.utc).isoformat() if status == "applied" else None
    ok = update_application_status(app_id, status, applied_at=now, user_id=user["user_id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Application not found")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)