import os
import json
from langchain.chat_models import init_chat_model
from langchain.agents import create_agent
from dotenv import load_dotenv
from tools import search_jobs

load_dotenv()


def main(user_ques:str):
    model = os.getenv("LLM_MODEL", "gemini-2.0-flash")
    provider = os.getenv("LLM_PROVIDER", "google_genai")
    llm = init_chat_model(model, model_provider=provider, temperature=0)
    tools = [search_jobs]
    graph = create_agent(
        model=llm,
        tools=tools,
        system_prompt=(
            "You are a job search assistant. "
            "For each job found, output a line with format:\n"
            "---\nTitle: <title>\nDescription: <description>\n"
            "Location: <location>\nSalary: <salary or N/A>\nLink: <url>\n---"
        ),
    )
    inputs = {"messages": [("user", user_ques)]}
    for chunk in graph.stream(inputs, stream_mode="updates"):
        for node, update in chunk.items():
            if node == "tools":
                for msg in update.get("messages", []):
                    if msg.type == "tool" and msg.content:
                        data = json.loads(msg.content)
                        for source in ("indeed", "linkedin"):
                            for job in data.get(source, []):
                                print(f"Title: {job['title']}")
                                print(f"Description: {job.get('description', '')}")
                                print(f"Location: {job.get('location', '') or 'N/A'}")
                                print(f"Salary: {job.get('salary', '') or 'N/A'}")
                                print(f"Link: {job['link']}")
                                print()


if __name__ == "__main__":
    job_ques = "Find me a job for Machine Learning engineer in Bangalore"
    main(job_ques)
