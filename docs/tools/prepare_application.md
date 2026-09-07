# prepare_application

**Purpose:** A2 draft + preview. No mandatory cover letter. Uses `profile_snapshot`; asks user if required apply fields missing.

**Input**
```json
{"job": {"title": "", "company": "", "location": "", "link": "", "description": ""}, "profile_snapshot": {}, "user_id": 1}
```

**Output**
```json
{"app_id": 1, "status": "preparing", "missing_fields": []}
```
Persists `applications` with `status: preparing`. If `missing_fields` non-empty (e.g., `["phone"]`), agent asks user instead of hallucinating.

**Side effects:** Writes DB only. Never auto-submits to Playwright; actual `apply` requires explicit user turn `"apply to #N"` / `"apply to all ≥70%"`.

**Errors**
- `404` if job missing required `title`/`link`

**Version:** `v1` (no cover letter). Cover letter remains separate manual path.
