import json
import re
import logging
from typing import Optional
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _read_prompt() -> str:
    """Load scoring prompt template.

    Returns:
        Prompt template string.
    """
    import os
    from pathlib import Path
    prompt_path = Path(__file__).parent.parent.parent / "agent" / "prompts" / "scoring" / "v1.md"
    if prompt_path.exists():
        return prompt_path.read_text()
    return "Score experience relevance 0-50: {profile} vs {job}"


def _deterministic_score(job: dict, snapshot: dict) -> tuple[int, dict]:
    """Compute deterministic 0–50 score for skills/location/salary/type.

    Args:
        job: Job dict with title, description, location, salary.
        snapshot: Profile snapshot with skills, work_history, education, preferences.

    Returns:
        Tuple of (score, breakdown dict).
    """
    skills = [s.lower() for s in snapshot.get("skills", [])]
    prefs = snapshot.get("preferences", {}) or {}
    pref_locs = [str(x).lower() for x in prefs.get("locations", [])]
    pref_remote = prefs.get("remote")

    text = f"{job.get('title','')} {job.get('description','')}".lower()
    # Skills overlap 0–30
    matched = sum(1 for s in skills if s and s in text)
    skills_score = min(30, matched * 6) if skills else 10

    # Location 0–10
    job_loc = (job.get("location") or "").lower()
    loc_score = 0
    if pref_locs:
        if any(pl in job_loc for pl in pref_locs if pl):
            loc_score = 10
        elif "remote" in job_loc and pref_remote:
            loc_score = 10
        elif not job_loc or job_loc == "n/a":
            loc_score = 5
    else:
        loc_score = 7

    # Salary 0–5 (if both present assume match)
    salary_score = 3
    if job.get("salary") and job["salary"] != "N/A" and prefs.get("salary_band"):
        salary_score = 5

    type_score = 5

    breakdown = {"skills": skills_score, "location": loc_score, "salary": salary_score, "type": type_score}
    return sum(breakdown.values()), breakdown


_BATCH_CHUNK = 10
_FALLBACK_EXPERIENCE = 25


def _invoke_rubric(prompt: str) -> list[dict]:
    """Invoke the rubric LLM and parse the scores list.

    Args:
        prompt: Full prompt text.

    Returns:
        List of score dicts, or empty list on failure.
    """
    import os
    from langchain.chat_models import init_chat_model
    model_name = os.getenv("PLANNER_LLM_MODEL", "claude-haiku-4-5")
    llm = init_chat_model(model_name, model_provider="anthropic", temperature=0)
    result = llm.invoke(prompt)
    content = result.content if isinstance(result.content, str) else str(result.content)
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return []
    data = json.loads(m.group())
    scores = data.get("scores", [])
    return scores if isinstance(scores, list) else []


def _score_chunk(chunk: list[tuple[int, dict]], snapshot: dict) -> dict[int, int]:
    """Score one chunk of jobs via a single LLM call.

    Args:
        chunk: List of (global index, job) pairs.
        snapshot: Profile snapshot.

    Returns:
        Mapping of global index to experience score 0–50.
    """
    prompt_tmpl = _read_prompt()
    jobs_payload = [
        {"job_idx": idx, "title": job.get("title", ""), "description": (job.get("description", "") or "")[:1500],
         "location": job.get("location", ""), "salary": job.get("salary", "")}
        for idx, job in chunk
    ]
    prompt = (
        prompt_tmpl
        + f"\n\nProfile: {json.dumps(snapshot, indent=2)[:3000]}"
        + f"\nJobs: {json.dumps(jobs_payload)}"
    )
    valid_idx = {idx for idx, _ in chunk}
    try:
        out = {}
        for entry in _invoke_rubric(prompt):
            idx = entry.get("job_idx")
            if idx in valid_idx and idx not in out:
                try:
                    out[idx] = max(0, min(50, int(entry.get("experience", _FALLBACK_EXPERIENCE))))
                except (TypeError, ValueError):
                    out[idx] = _FALLBACK_EXPERIENCE
        for idx, _ in chunk:
            out.setdefault(idx, _FALLBACK_EXPERIENCE)
        return out
    except Exception as e:
        logger.warning("LLM experience score failed: %s", e)
        return {idx: _FALLBACK_EXPERIENCE for idx, _ in chunk}


def _llm_experience_scores(jobs: list, snapshot: dict) -> list[int]:
    """Score experience relevance 0–50 for jobs in chunks of 10.

    Args:
        jobs: List of job dicts.
        snapshot: Profile snapshot.

    Returns:
        Experience scores aligned with jobs order.
    """
    scores = [0] * len(jobs)
    for start in range(0, len(jobs), _BATCH_CHUNK):
        chunk = list(enumerate(jobs[start:start + _BATCH_CHUNK], start=start))
        for idx, exp in _score_chunk(chunk, snapshot).items():
            scores[idx] = exp
    return scores


def _llm_experience_score(job: dict, snapshot: dict) -> int:
    """Score experience relevance 0–50 for a single job (compat wrapper).

    Args:
        job: Job dict.
        snapshot: Profile snapshot.

    Returns:
        Experience score 0–50.
    """
    return _llm_experience_scores([job], snapshot)[0]


@tool
def score_jobs(jobs: list, profile_snapshot: dict, threshold: Optional[int] = None) -> str:
    """Score jobs against a profile snapshot using hybrid deterministic + LLM rubric.

    Args:
        jobs: List of job dicts with title, description, location, salary, link, source.
        profile_snapshot: Profile snapshot from get_profile.
        threshold: If set, only return jobs with score >= threshold.

    Returns:
        JSON string with scored_jobs sorted by score, each with score and breakdown.
    """
    exp_scores = _llm_experience_scores(jobs, profile_snapshot)
    scored = []
    for job, exp_score in zip(jobs, exp_scores):
        det_score, breakdown = _deterministic_score(job, profile_snapshot)
        total = det_score + exp_score
        breakdown["experience"] = exp_score
        scored.append({"job": job, "score": total, "breakdown": breakdown})

    scored.sort(key=lambda x: x["score"], reverse=True)
    if threshold is not None:
        scored = [s for s in scored if s["score"] >= int(threshold)]
    return json.dumps({"scored_jobs": scored}, indent=2)
