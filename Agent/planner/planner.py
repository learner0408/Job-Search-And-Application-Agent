import json
import logging
import re
import os
from pathlib import Path
from langchain.chat_models import init_chat_model

logger = logging.getLogger(__name__)


def _load_prompt() -> str:
    """Load planner prompt template.

    Returns:
        Prompt template string.
    """
    p = Path(__file__).parent.parent / "prompts" / "planner" / "v1.md"
    if p.exists():
        return p.read_text()
    return "Generate JSON plan with steps [{id,tool,args,depends_on}]"


def plan_query(query: str, user_id: int = 0) -> dict:
    """Generate a validated DAG plan for a user query.

    Args:
        query: Raw user query.
        user_id: User id for context.

    Returns:
        Validated plan dict.
    """
    from Agent.planner.validator import validate_plan

    model_name = os.getenv("PLANNER_LLM_MODEL", "claude-haiku-4-5")
    llm = init_chat_model(model_name, model_provider="anthropic", temperature=0)
    prompt = _load_prompt() + f"\n\nUser_id: {user_id}\nQuery: {query}\nReturn ONLY JSON."
    result = llm.invoke(prompt)
    content = result.content if isinstance(result.content, str) else str(result.content)
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        raise ValueError(f"No JSON plan found: {content[:500]}")
    plan = json.loads(m.group())
    ok, err = validate_plan(plan, raw_query=query)
    logger.info(
        "plan valid=%s steps=%s user=%s",
        ok, [s.get("tool") for s in plan.get("steps", [])], user_id,
    )
    if not ok:
        # Retry once with error appended
        retry_prompt = prompt + f"\n\nPrevious attempt was invalid: {err}\nFix the JSON and return ONLY the corrected plan."
        try:
            from tests.conftest import add_tokens
            add_tokens(1000)
        except Exception:
            pass
        result2 = llm.invoke(retry_prompt)
        content2 = result2.content if isinstance(result2.content, str) else str(result2.content)
        m2 = re.search(r"\{.*\}", content2, re.DOTALL)
        if m2:
            plan = json.loads(m2.group())
            ok2, err2 = validate_plan(plan, raw_query=query)
            if ok2:
                return plan
            err = err2
        raise ValueError(f"Invalid plan: {err} — {plan}")
    return plan
