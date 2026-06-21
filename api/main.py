import os
import sys
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from dotenv import load_dotenv

# Add parent directory to path to import tools, Agent, and database
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
from tools import search_indeed, search_linkedin, search_yc
from Agent.agent import run_agent
from database import init_db, save_search, get_history, get_search, delete_search, clear_history, save_chat_message

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
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


class ChatResponse(BaseModel):
    reply: str
    jobs: Optional[List[JobListing]] = None


@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    logger.info("Chat request: '%s'", request.message[:120])
    save_chat_message("user", request.message)
    result = run_agent(request.message)
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
    save_chat_message("agent", reply, search_id=search_id)
    return ChatResponse(reply=reply, jobs=job_listings)


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)