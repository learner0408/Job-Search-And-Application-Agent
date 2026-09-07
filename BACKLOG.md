# Backlog

## P0 — Keep-alive observability
- [ ] Web heartbeat: log `GET /ping` and `GET /wake` hits and `warmApi` success with duration and `inJstWindow` result; log `API_URL` host on boot (`web/server.js:25-32`, `54-61`, `85`)
- [ ] API heartbeat: log `GET /api/health` (`api/main.py:400`) and `GET /api/auth/google` with computed `redirect_uri` and `GOOGLE_CLIENT_ID` presence (`api/main.py:195`, `api/auth.py:318`)
- [ ] Verify: `curl -i https://jobsearch-web.onrender.com/wake` and `curl -i https://jobsearch-api-1bbx.onrender.com/api/health`, then 20-min heartbeat watch in Render logs for both services

## P1 — Auth & platform
- [ ] Decide `GET /wake` semantics: keep fire-and-forget (`warmApi(); res.send('ok')` at `web/server.js:58-61`) vs `?wait=1` await variant that makes cron 200 mean API was reached (adds up to ~55s on cold start)
- [ ] Rate-limit visibility: admin-only debug for per-email limiter (`api/auth.py:35-38` on `POST /api/auth/login` at `api/main.py:158`) without exposing PII

## P2 — Product roadmap
- [ ] Email verification / password reset flow
- [ ] Resume object storage (move from BYTEA to external store)
- [ ] Cloud auto-apply hardening
- [ ] R3 re-plan: planner re-routes on node failure (e.g., DDG `all` → retry `linkedin` only), behind flag (deferred from R2)

## P3 — Agent improvements (R3 follow-up)
- [ ] Implement orchestrator re-plan edge (`route_after_execute` → planner) with validator flag
