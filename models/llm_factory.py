import os
from typing import Any, Dict, Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def get_llm(model_name: str, provider: Optional[str] = None, **kwargs: Any) -> Any:
    """
    Returns a LangChain LLM or ChatModel instance depending on the model name and provider.

    Supported Providers:
    - openai (e.g., 'gpt-4o', 'gpt-4o-mini', 'gpt-3.5-turbo')
    - anthropic (e.g., 'claude-3-5-sonnet-latest', 'claude-3-opus')
    - ollama (e.g., 'llama3', 'mistral')
    - llama.cpp (can load local .gguf models or connect to a local llama.cpp server via base_url)

    Args:
        model_name (str): The name or path of the model.
        provider (str, optional): The provider name ('openai', 'anthropic', 'ollama', 'llama.cpp').
                                  If not provided, it will be automatically inferred from the model_name.
        **kwargs: Additional parameters passed to the model initializer (e.g., temperature, max_tokens).

    Returns:
        An instance of a LangChain BaseLanguageModel or BaseChatModel.
    """
    model_name_lower = model_name.lower()
    
    # Auto-detect provider if not explicitly provided
    if not provider:
        if any(prefix in model_name_lower for prefix in ["gpt-", "o1-", "o3-", "text-davinci"]):
            provider = "openai"
        elif "claude" in model_name_lower:
            provider = "anthropic"
        elif model_name_lower.startswith("ollama/"):
            provider = "ollama"
            model_name = model_name[7:]  # Strip the prefix
        elif model_name_lower.startswith("ollama:"):
            provider = "ollama"
            model_name = model_name[7:]  # Strip the prefix
        elif model_name_lower.startswith("llamacpp/") or model_name_lower.startswith("llama.cpp/"):
            provider = "llama.cpp"
            model_name = model_name[model_name.find("/") + 1:]  # Strip the prefix
        elif model_name_lower.startswith("llamacpp:") or model_name_lower.startswith("llama.cpp:"):
            provider = "llama.cpp"
            model_name = model_name[model_name.find(":") + 1:]  # Strip the prefix
        elif "ollama" in model_name_lower:
            provider = "ollama"
        elif "llama.cpp" in model_name_lower or model_name_lower.endswith(".gguf"):
            provider = "llama.cpp"
        else:
            # Default to openai as a fallback
            provider = "openai"

    provider_lower = provider.lower()

    if provider_lower == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError(
                "Could not import langchain_openai. Please install it using `pip install langchain-openai`."
            )
        # Ensure API key is present or passed
        api_key = kwargs.pop("api_key", kwargs.pop("openai_api_key", os.getenv("OPENAI_API_KEY")))
        return ChatOpenAI(model=model_name, api_key=api_key, **kwargs)

    elif provider_lower == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError(
                "Could not import langchain_anthropic. Please install it using `pip install langchain-anthropic`."
            )
        # Ensure API key is present or passed
        api_key = kwargs.pop("api_key", kwargs.pop("anthropic_api_key", os.getenv("ANTHROPIC_API_KEY")))
        return ChatAnthropic(model=model_name, api_key=api_key, **kwargs)

    elif provider_lower == "ollama":
        try:
            # Try newer langchain_ollama package first if available
            from langchain_ollama import ChatOllama
        except ImportError:
            try:
                from langchain_community.chat_models import ChatOllama
            except ImportError:
                raise ImportError(
                    "Could not import ChatOllama. Please make sure langchain-community or langchain-ollama is installed."
                )
        # Ollama typically runs on localhost:11434 by default
        base_url = kwargs.pop("base_url", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
        return ChatOllama(model=model_name, base_url=base_url, **kwargs)

    elif provider_lower in ["llama.cpp", "llamacpp"]:
        # There are two ways to use Llama.cpp:
        # 1. Local GGUF file via LlamaCpp (requires llama-cpp-python)
        # 2. Local Llama.cpp server (OpenAI-compatible) via ChatOpenAI
        
        # If 'model_path' is provided or if the model_name itself is a file path to .gguf, use LlamaCpp
        model_path = kwargs.pop("model_path", None)
        if not model_path and (model_name.endswith(".gguf") or os.path.exists(model_name)):
            model_path = model_name
            
        if model_path:
            try:
                from langchain_community.llms import LlamaCpp
            except ImportError:
                raise ImportError(
                    "Could not import langchain_community.llms.LlamaCpp. "
                    "Please install `llama-cpp-python` if you want to run local .gguf models directly."
                )
            return LlamaCpp(model_path=model_path, **kwargs)
        else:
            # Otherwise assume connection to a local llama.cpp server running an OpenAI compatible API
            try:
                from langchain_openai import ChatOpenAI
            except ImportError:
                raise ImportError(
                    "Could not import langchain_openai. Please install it using `pip install langchain-openai`."
                )
            # Default llama.cpp server URL is usually http://localhost:8080 or http://localhost:8080/v1
            base_url = kwargs.pop("base_url", kwargs.pop("openai_api_base", os.getenv("LLAMACPP_BASE_URL", "http://localhost:8080/v1")))
            api_key = kwargs.pop("api_key", "not-needed")
            return ChatOpenAI(
                model=model_name,
                base_url=base_url,
                api_key=api_key,
                **kwargs
            )

    else:
        raise ValueError(
            f"Unsupported provider: '{provider}'. "
            f"Supported providers are: 'openai', 'anthropic', 'ollama', 'llama.cpp'"
        )
