import os
from langchain.chat_models import init_chat_model
from langchain.agents import create_agent
from dotenv import load_dotenv
from tools import search_jobs

load_dotenv()


def main():
    model = os.getenv("LLM_MODEL", "gemini-2.0-flash")
    provider = os.getenv("LLM_PROVIDER", "google_genai")
    llm = init_chat_model(model, model_provider=provider, temperature=0)
    tools = [search_jobs]
    graph = create_agent(
        model=llm,
        tools=tools,
        system_prompt="You are a job search assistant. Help users find and apply to jobs.",
        debug=True,
    )
    inputs = {"messages": [("user", "Find me software engineering jobs")]}
    for chunk in graph.stream(inputs, stream_mode="updates"):
        for node, update in chunk.items():
            print(f"--- {node} ---")
            print(update)


if __name__ == "__main__":
    main()
