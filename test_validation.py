#!/usr/bin/env python
"""Test semantic validation"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from profiles.services.semantic_validator import validate_answer_semantically
from profiles.services.llm_engine import get_llm_client

print("=" * 60)
print("SEMANTIC VALIDATION TEST")
print("=" * 60)

# Check API key
api_key = os.getenv("CEREBRAS_API_KEY")
if api_key:
    print(f"✓ CEREBRAS_API_KEY found: {api_key[:10]}...")
else:
    print("✗ CEREBRAS_API_KEY MISSING!")
    
# Check client
client = get_llm_client()
if client:
    print("✓ LLM client initialized")
else:
    print("✗ LLM client failed")

print("\n" + "=" * 60)
print("TEST CASES")
print("=" * 60)

test_cases = [
    ("Tell me about your Python experience", "asdfjkasldkfj", False),
    ("Tell me about your Python experience", "I have 5 years with Python", True),
    ("What's your AWS experience?", "I like pizza", False),
    ("What's your AWS experience?", "I don't have any", True),
]

for question, answer, expected_valid in test_cases:
    makes_sense, msg = validate_answer_semantically(question, answer, "test")
    status = "✓" if makes_sense == expected_valid else "✗"
    print(f"\n{status} Q: {question[:40]}...")
    print(f"  A: {answer}")
    print(f"  Valid: {makes_sense} (expected: {expected_valid})")
    if msg:
        print(f"  Msg: {msg[:60]}...")
