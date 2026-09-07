# search_jobs

**Purpose:** Search Indeed / LinkedIn / YC via `JobSource` interface (J2). Thin cache (24h by `title,location,source`).

**Input**
```json
{"title": "AI Engineer", "location": "Bangalore", "source": "all|indeed|linkedin|yc", "employment_type": "", "remote": false, "max_results": 10}
```

**Output**
```json
{"indeed": [{"title": "", "description": "", "location": "", "salary": "", "link": "", "source": "Indeed"}], "linkedin": [], "yc_startups": []}
```
`description` is full snippet (no 120-char truncation). `location`/`salary` normalized in source layer.

**Errors**
- `{"error": "Unknown source '...' "}` for invalid `source`
- `[]` per source on DDG failure after 2× retry → falls back to cache per R2

**Scoping:** No `user_id` needed. Called by planner before `get_profile`.

**Version:** `v1` (DDG only). Real APIs later are new `JobSource` impls, no contract change.
