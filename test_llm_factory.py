import sys
from models import get_llm

def test_factory():
    print("Testing LLM Factory...")

    # 1. Test OpenAI
    try:
        print("\n--- Testing OpenAI (gpt-4o-mini) ---")
        llm = get_llm("gpt-4o-mini", api_key="mock-key-for-testing")
        print(f"Success! Object type: {type(llm)}")
        print(f"Model name: {llm.model_name}")
        print(f"API key loaded: {'Yes' if llm.openai_api_key.get_secret_value() == 'mock-key-for-testing' else 'No'}")
    except Exception as e:
        print(f"OpenAI test failed: {e}")

    # 2. Test Anthropic
    try:
        print("\n--- Testing Anthropic (claude-3-5-sonnet-latest) ---")
        llm = get_llm("claude-3-5-sonnet-latest", api_key="mock-key-for-testing")
        print(f"Success! Object type: {type(llm)}")
        print(f"Model name: {llm.model}")
        print(f"API key loaded: {'Yes' if llm.anthropic_api_key.get_secret_value() == 'mock-key-for-testing' else 'No'}")
    except Exception as e:
        print(f"Anthropic test failed: {e}")

    # 3. Test Ollama
    try:
        print("\n--- Testing Ollama (ollama/llama3) ---")
        llm = get_llm("ollama/llama3")
        print(f"Success! Object type: {type(llm)}")
        print(f"Model name: {llm.model}")
        print(f"Base URL: {llm.base_url}")
    except Exception as e:
        print(f"Ollama test failed: {e}")

    # 4. Test Llama.cpp Server (default base_url)
    try:
        print("\n--- Testing Llama.cpp Server ---")
        llm = get_llm("llamacpp/mistral-7b")
        print(f"Success! Object type: {type(llm)}")
        print(f"Model name: {llm.model_name}")
        print(f"Base URL: {llm.openai_api_base}")
    except Exception as e:
        print(f"Llama.cpp Server test failed: {e}")

if __name__ == "__main__":
    test_factory()
