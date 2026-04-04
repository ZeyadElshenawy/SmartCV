"""Quick smoke test for the LangChain + Groq migration."""
import os, sys, time
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')

# Test 1: Raw LLM (plain text)
print("=" * 50)
print("Test 1: get_llm() — plain text generation")
print("=" * 50)
from profiles.services.llm_engine import get_llm, get_structured_llm
from langchain_core.messages import HumanMessage

start = time.time()
llm = get_llm()
result = llm.invoke([HumanMessage(content="Say hello in one sentence.")])
elapsed = time.time() - start
print(f"✓ Response: {result.content}")
print(f"  Time: {elapsed:.2f}s")

# Test 2: Structured LLM (JSON with Pydantic)
print()
print("=" * 50)
print("Test 2: get_structured_llm() — Pydantic output")
print("=" * 50)
from profiles.services.schemas import SkillListResult

start = time.time()
structured_llm = get_structured_llm(SkillListResult, temperature=0.0)
result = structured_llm.invoke("Extract skills from: 'Looking for a Python developer with experience in Django, PostgreSQL, and Docker'")
elapsed = time.time() - start
print(f"✓ Result type: {type(result).__name__}")
print(f"  Skills: {result.skills}")
print(f"  Time: {elapsed:.2f}s")

print()
print("All tests passed! 🎉")
