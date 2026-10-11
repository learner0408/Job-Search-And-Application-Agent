# apply_application

**Purpose:** Human-approved apply step. Takes a list of prepared application ids (single-item lists welcome). Currently a placeholder: validates each id belongs to the user but writes nothing.

**Input**
```json
{"app_ids": [12, 15]}
```

**Output**
```json
{"results": [{"app_id": 12, "message": "The setup for applying is currently in progress. Please come back later."}]}
```
Unknown ids are reported per-id; empty list returns a clarification message.

**Errors**
- No user context → `{"results": [], "message": "No user context available."}`
- Never raises; all failures are per-id messages.

**Scoping:** Each id verified via `get_application(app_id, user_id)` against the request context.

**Version:** `v1` (dummy, no writes). Real cloud submission implements inside this signature later.
