import sys
sys.path.insert(0, "src")
from llm_client import get_llm_client

client = get_llm_client()
print("Endpoints:", [e["name"] for e in client._endpoints])

result = client.chat(
    [{"role": "user", "content": "Reply with exactly one word: HELLO"}],
    max_tokens=20,
    timeout=60,
)
print("Result:", repr(result))
