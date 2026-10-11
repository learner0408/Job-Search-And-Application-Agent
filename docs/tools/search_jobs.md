# search_jobs

**Purpose:** Search Adzuna / YC (primaries) + Indeed / LinkedIn (fallbacks) via `JobSource` interface (J2). Thin cache (24h by `title,location,source`, v2 key).

**Input**
```json
{"title": "AI Engineer", "location": "Bangalore", "source": "all|adzuna|indeed|linkedin|yc", "employment_type": "", "remote": false, "max_results": 10}
```

**Output**
```json
{"adzuna": [{"title": "", "company": "", "description": "", "location": "", "salary": "", "link": "", "source": "Adzuna"}], "indeed": [], "linkedin": [], "yc_startups": []}
```
`description` is full snippet (no 120-char truncation). `location`/`salary`/`company` normalized in source layer via `_extract_company`. Adzuna fields are structured from the official API (`company.display_name`, `location.display_name`, `salary_min/max`, `redirect_url`); note Adzuna returns a description snippet, not the full posting.

**Cascade (`source="all"`):** primaries (`adzuna`, `yc`) always run; fallbacks (`linkedin`, `indeed`) run only when primaries return fewer than `max_results` total. Explicit `source` selects one source only.

**Errors**
- `{"error": "Unknown source '...' "}` for invalid `source`
- `[]` per source on DDG failure after 2× retry → falls back to cache per R2
- Adzuna returns `[]` when `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` are missing or on HTTP error (never raises)

**Scoping:** No `user_id` needed. Called by planner before `get_profile`.

**Version:** `v2` (Adzuna official API + cascade; cache key v2). YC direct page fetch and LinkedIn/Indeed official APIs still open (no accessible official path as of 2026).
