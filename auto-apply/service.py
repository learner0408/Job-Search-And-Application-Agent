import os
import sys
import json
import asyncio
import tempfile
import logging
import re
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("auto-apply")

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8765"))


def _resolve_api_base(override: str | None = None) -> str:
    if override:
        return override
    env = os.environ.get("API_BASE")
    if env:
        return env
    return "http://127.0.0.1:8000"


API_BASE = _resolve_api_base()

app = FastAPI(title="Auto-Apply Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

server_params = StdioServerParameters(
    command="npx",
    args=["--yes", "@playwright/mcp"],
)


def _find_matching_element(snapshot: dict, patterns: list[str], role: str | None = None) -> str | None:
    text = snapshot.get("text", "")
    for line in text.split("\n"):
        line_lower = line.lower()
        if any(p in line_lower for p in patterns):
            return line.strip()
    nodes = snapshot.get("nodes", snapshot.get("elements", []))
    for node in nodes:
        for key in ("label", "placeholder", "aria-label", "name", "id"):
            val = (node.get(key) or "").lower()
            if any(p in val for p in patterns):
                if role is None or node.get("role") == role:
                    sel = node.get("selector") or node.get("locator") or ""
                    if sel:
                        return sel
    return None


async def _mcp_fill_field(session: ClientSession, field_name: str, value: str, snapshot: dict) -> bool:
    patterns = {
        "full_name": ["full name", "your name", "name*", "first name", "last name"],
        "email": ["email"],
        "phone": ["phone", "mobile", "telephone"],
    }.get(field_name, [field_name.lower().replace("_", " ")])

    sel = _find_matching_element(snapshot, patterns, role="textbox")
    if not sel:
        sel = _find_matching_element(snapshot, patterns)
    if sel and not sel.startswith("http"):
        try:
            await session.call_tool("browser_fill", {"selector": sel, "value": value})
            logger.info("  Filled %s", field_name)
            return True
        except Exception as e:
            logger.warning("  Failed to fill %s: %s", field_name, e)
    return False


async def _run_apply(app_id: int, api_base: str):
    async with httpx.AsyncClient(timeout=30) as client:
        app_resp = await client.get(f"{api_base}/api/applications/{app_id}")
        if app_resp.status_code != 200:
            raise HTTPException(status_code=404, detail="Application not found")
        app_data = app_resp.json()

        profile_resp = await client.get(f"{api_base}/api/profile")
        profile = profile_resp.json() if profile_resp.status_code == 200 else {}

        link = app_data.get("link", "")
        if not link:
            raise HTTPException(status_code=400, detail="No job link in application")
        cover_letter = app_data.get("cover_letter", "")

        resume_path = None
        if profile.get("resume_name"):
            try:
                resume_resp = await client.get(f"{api_base}/api/resume")
                if resume_resp.status_code == 200:
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=profile["resume_name"])
                    tmp.write(resume_resp.content)
                    tmp.close()
                    resume_path = tmp.name
                    logger.info("Resume saved to %s", resume_path)
            except Exception as e:
                logger.warning("Could not download resume: %s", e)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            logger.info("MCP tools available: %s", tool_names)

            await session.call_tool("browser_navigate", {"url": link})
            logger.info("Navigated to %s", link)
            await asyncio.sleep(5)

            # Dismiss overlays (sign-in prompts, cookie notices, etc.)
            for dismiss_sel in ["Escape", "button:has-text('Skip')", "button:has-text('Close')", "[aria-label='Close']", ".modal-close"]:
                try:
                    if dismiss_sel == "Escape":
                        await session.call_tool("browser_press_key", {"key": "Escape"})
                    else:
                        await session.call_tool("browser_click", {"selector": dismiss_sel})
                    await asyncio.sleep(1)
                except Exception:
                    pass

            # Click the primary apply button
            for apply_sel in ["button:has-text('Apply Now')", "button:has-text('Easy Apply')", "button:has-text('Apply')", "a:has-text('Apply Now')", "a:has-text('Apply')"]:
                try:
                    await session.call_tool("browser_click", {"selector": apply_sel})
                    logger.info("Clicked apply button: %s", apply_sel)
                    await asyncio.sleep(4)
                    # Check if we hit a sign-in page instead of the form
                    check_snap = await session.call_tool("browser_snapshot", {})
                    check_content = check_snap.content[0] if check_snap.content else {}
                    check_text = str(getattr(check_content, 'text', '')) if hasattr(check_content, 'text') else str(check_content)
                    if "sign in" in check_text.lower() or "create account" in check_text.lower():
                        logger.warning("Sign-in page detected — sign in manually in the browser window, then go back to the form")
                    break
                except Exception:
                    continue

            # Take a fresh snapshot of the form page
            snap = await session.call_tool("browser_snapshot", {})
            content = snap.content[0] if snap.content else {}
            try:
                snapshot_data = json.loads(content.text) if hasattr(content, 'text') else content
            except (json.JSONDecodeError, AttributeError, TypeError):
                snapshot_data = {"text": str(content)}

            # Debug: log visible text snippet
            page_text = snapshot_data.get("text", "")
            logger.info("Page text snippet: %s", page_text[:300].replace("\n", " | "))

            profile_fields = {
                "full_name": profile.get("full_name", ""),
                "email": profile.get("email", ""),
                "phone": profile.get("phone", ""),
            }

            for field_name, value in profile_fields.items():
                if value:
                    await _mcp_fill_field(session, field_name, value, snapshot_data)

            if cover_letter:
                try:
                    await session.call_tool("browser_type", {
                        "selector": "textarea",
                        "text": cover_letter,
                    })
                    logger.info("Past cover letter into textarea")
                except Exception as e:
                    logger.warning("Could not fill cover letter: %s", e)

            if resume_path:
                try:
                    file_input = _find_matching_element(snapshot_data, ["resume", "cv", "upload"], role="file")
                    if file_input:
                        await session.call_tool("browser_fill", {"selector": file_input, "value": resume_path})
                        logger.info("Uploaded resume")
                    else:
                        logger.info("No file input detected for resume")
                except Exception as e:
                    logger.warning("Could not upload resume: %s", e)

            logger.info("Form fields pre-filled (if any were found). Review the browser and submit manually.")
            logger.info("(Auto-detecting submission via URL change)")

            current_url = link
            submitted = False
            for _ in range(60):
                await asyncio.sleep(5)
                try:
                    snap = await session.call_tool("browser_snapshot", {})
                    c = snap.content[0] if snap.content else {}
                    new_url = ""
                    if hasattr(c, 'text'):
                        try:
                            d = json.loads(c.text)
                            new_url = d.get("url", "")
                        except (json.JSONDecodeError, AttributeError):
                            pass
                    if new_url and new_url != current_url and "apply" not in new_url.lower():
                        logger.info("URL changed to %s — submission detected", new_url)
                        submitted = True
                        break
                except Exception:
                    pass

            if submitted:
                await client.put(f"{api_base}/api/applications/{app_id}?status=applied")
                logger.info("Application %d marked as applied", app_id)
            else:
                logger.warning("No submission detected for application %d; leaving status as preparing", app_id)

            if resume_path:
                try:
                    os.unlink(resume_path)
                except OSError:
                    pass


@app.post("/apply/{app_id}")
async def trigger_apply(app_id: int, api_base: str | None = None):
    resolved = _resolve_api_base(api_base)
    logger.info("Triggering apply for app %d using API base: %s", app_id, resolved)

    async def _done(task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            logger.warning("Apply task for app %d cancelled", app_id)
        except Exception as e:
            logger.error("Apply task for app %d failed: %s", app_id, e)

    task = asyncio.create_task(_run_apply(app_id, resolved))
    task.add_done_callback(lambda t: asyncio.create_task(_done(t)))
    return {"status": "started", "app_id": app_id, "api_base": resolved}


@app.get("/status/{app_id}")
async def get_apply_status(app_id: int, api_base: str | None = None):
    resolved = _resolve_api_base(api_base)
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{resolved}/api/applications/{app_id}")
        if resp.status_code != 200:
            raise HTTPException(status_code=404)
        return resp.json()


if __name__ == "__main__":
    logger.info("Auto-Apply Agent Service")
    logger.info("API base: %s", API_BASE)
    logger.info("Listening on http://%s:%d", HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT)
