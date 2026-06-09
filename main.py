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
    )
    inputs = {"messages": [("user", "Find me AI Engineer job in Bangalore")]}
    for chunk in graph.stream(inputs, stream_mode="updates"):
        for node, update in chunk.items():
            if node == "tools":
                for msg in update.get("messages", []):
                    if msg.type == "tool" and msg.content:
                        print(msg.content)
                continue
            messages = update.get("messages", [])
            for msg in messages:
                if msg.type == "ai" and msg.content:
                    print(msg.content)


if __name__ == "__main__":
    main()
