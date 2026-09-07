# score_jobs

**Purpose:** S3 hybrid scoring. Deterministic 0–50 + LLM rubric 0–50; filter `score >= threshold` in code with breakdown.

**Input**
```json
{"jobs": [{"title": "", "description": "", "location": "", "salary": "", "link": "", "source": ""}], "profile_snapshot": {}, "threshold": 70}
```

**Output**
```json
{"scored_jobs": [{"job": {}, "score": 75, "breakdown": {"skills": 30, "location": 10, "salary": 5, "type": 5, "experience": 25}}]}
```
Sorted descending by `score`. Only jobs with `score >= threshold` returned when threshold set.

**Deterministic (0–50):** skills overlap (embedding/keyword), location/remote match, salary band, employment_type.
**LLM rubric (0–50):** experience relevance via `PLANNER_LLM_MODEL=claude-haiku-4-5` (`Agent/prompts/scoring/v1.md`).

**Errors**
- Returns `[]` when `jobs` empty
- On LLM failure, retries per R2 (1s,7s,70s); final fallback returns deterministic-only score

**Version:** `v1` — weight changes are minor version bumps (e.g., `v1.1`).
