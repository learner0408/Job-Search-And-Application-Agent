import os
import sys
import json
import logging
import shutil
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
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
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Resume storage directory (same volume as DB)
DATA_DIR = os.path.dirname(os.getenv("DB_PATH", os.path.join(BASE_DIR, "data", "history.db")))
RESUME_DIR = os.path.join(DATA_DIR, "resumes")
os.makedirs(RESUME_DIR, exist_ok=True)

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


class ApplicationResponse(BaseModel):
    id: int
    job_title: str
    company: str
    location: str
    salary: str
    link: str
    source: str
    status: str
    cover_letter: str
    notes: str
    applied_at: Optional[str] = None
    created_at: str


@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    logger.info("Chat request: '%s'", request.message[:120])
    session_id = request.session_id
    if not session_id:
        session_id = create_session()
    save_chat_message("user", request.message, session_id=session_id)
    result = run_agent(request.message, session_id=session_id)
    reply = result["reply"]
    jobs_data = result.get("jobs")
    job_listings = [JobListing(**j) for j in jobs_data] if jobs_data else None
    search_id = None
    if job_listings:
        search_id = save_search(
            query=request.message,
            location="",
            source_trigger="chat",
            jobs=[j.model_dump() for j in job_listings],
        )
    save_chat_message("agent", reply, search_id=search_id, session_id=session_id)
    update_session(session_id)
    return ChatResponse(reply=reply, session_id=session_id, jobs=job_listings)


@app.get("/api/sessions")
async def list_sessions_endpoint():
    return {"sessions": list_sessions()}


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages_endpoint(session_id: str):
    return {"messages": get_session_messages(session_id)}


@app.delete("/api/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    deleted = delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@app.post("/api/search", response_model=SearchResponse)
async def search_jobs_endpoint(request: SearchRequest):
    logger.info(
        "Search request: query='%s', location='%s', max_results=%d",
        request.query, request.location, request.max_results,
    )

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
    )
    return SearchResponse(jobs=job_listings)


@app.get("/api/history")
async def history_list(page: int = 1, per_page: int = 10):
    items, total = get_history(page=page, per_page=per_page)
    return {"searches": items, "total": total, "page": page, "per_page": per_page}


@app.get("/api/history/{search_id}")
async def history_detail(search_id: int):
    search = get_search(search_id)
    if not search:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Search not found")
    return search


@app.delete("/api/history/{search_id}")
async def history_delete(search_id: int):
    deleted = delete_search(search_id)
    if not deleted:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Search not found")
    return {"ok": True}


@app.delete("/api/history")
async def history_clear():
    count = clear_history()
    return {"deleted": count}


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


# ── Profile ────────────────────────────────────────────────

@app.get("/api/profile")
async def get_profile_endpoint():
    p = get_profile()
    if not p:
        return {
            "full_name": "", "email": "", "phone": "",
            "linkedin_url": "", "github_url": "", "website": "",
            "skills": [], "work_history": [], "education": [],
            "resume_name": "",
        }
    return p


@app.post("/api/profile")
async def save_profile_endpoint(data: ProfileData):
    save_profile(data.model_dump())
    return {"ok": True}


# ── Resume ──────────────────────────────────────────────────

RESUME_PATH = os.path.join(RESUME_DIR, "resume")


@app.post("/api/resume/upload")
async def upload_resume(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "resume.pdf")[1] or ".pdf"
    dest = RESUME_PATH + ext
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    # Update the profile with the resume filename
    p = get_profile() or {}
    p["resume_name"] = os.path.basename(dest)
    save_profile(p)
    return {"resume_name": os.path.basename(dest)}


@app.get("/api/resume")
async def download_resume():
    p = get_profile()
    if not p or not p.get("resume_name"):
        raise HTTPException(status_code=404, detail="No resume uploaded")
    path = os.path.join(RESUME_DIR, p["resume_name"])
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Resume file not found")
    return FileResponse(path, media_type="application/octet-stream", filename=p["resume_name"])


# ── Cover Letter & Apply Preparation ────────────────────────

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
async def prepare_apply(data: PrepareApplyRequest):
    profile = get_profile()
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
    })

    return {"app_id": app_id, "cover_letter": cover_letter}


# ── Applications ────────────────────────────────────────────

@app.get("/api/applications")
async def list_applications(status: Optional[str] = Query(None)):
    items = get_applications(status=status)
    return {"applications": items}


@app.get("/api/applications/{app_id}")
async def get_application_endpoint(app_id: int):
    app = get_application(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return app


@app.put("/api/applications/{app_id}")
async def update_application(app_id: int, status: str = Query(...)):
    now = datetime.utcnow().isoformat() if status == "applied" else None
    ok = update_application_status(app_id, status, applied_at=now)
    if not ok:
        raise HTTPException(status_code=404, detail="Application not found")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)