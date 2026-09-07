# get_profile

**Purpose:** Assemble `profile_snapshot` for P3 (structured + resume + preferences).

**Input**
```json
{"user_id": 1}
```

**Output**
```json
{"profile_snapshot": {"skills": [], "work_history": [], "education": [], "resume_text": "", "preferences": {"desired_roles": [], "locations": [], "salary_band": "", "remote": null}}, "completeness": "full|partial"}
```
`resume_text` is first ~2k tokens from `resumes` blob; `partial` when resume missing.

**Errors**
- `401` if `user_id` not set via `set_current_user` / `current_setting('app.current_user_id')`

**Scoping:** Must filter by `current_setting('app.current_user_id')::int`. Never return other users.

**Version:** `v1` (reads `user_profile` + `resumes` + `user_preferences`).
