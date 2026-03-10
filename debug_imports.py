
import sys
import traceback

print("=== IMPORT TEST ===")

# Test 1: Import Cerebras
print("\n[1] Testing Cerebras import...")
try:
    from cerebras.cloud.sdk import Cerebras
    print("    ✓ Cerebras imported successfully")
except Exception as e:
    print(f"    ✗ ERROR: {e}")
    traceback.print_exc()
    sys.exit(1)

# Test 2: Import Django and setup
print("\n[2] Setting up Django...")
try:
    import os
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
    django.setup()
    print("    ✓ Django setup complete")
except Exception as e:
    print(f"    ✗ ERROR: {e}")
    traceback.print_exc()
    sys.exit(1)

# Test 3: Import llm_engine
print("\n[3] Importing llm_engine...")
try:
    from profiles.services.llm_engine import get_llm_client
    print("    ✓ llm_engine imported successfully")
except Exception as e:
    print(f"    ✗ ERROR: {e}")
    traceback.print_exc()
    sys.exit(1)

# Test 4: Get client
print("\n[4] Getting LLM client...")
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
    client = get_llm_client()
    if client:
        print("    ✓ Client obtained")
    else:
        print("    ✗ Client returned None")
except Exception as e:
    print(f"    ✗ ERROR: {e}")
    traceback.print_exc()
    sys.exit(1)

# Test 5: Import interviewer
print("\n[5] Importing interviewer...")
try:
    from profiles.services.interviewer import get_next_question, process_user_reply
    print("    ✓ interviewer imported successfully")
except Exception as e:
    print(f"    ✗ ERROR: {e}")
    traceback.print_exc()
    sys.exit(1)

# Test 6: Test get_next_question with real user
print("\n[6] Testing get_next_question...")
try:
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.first()
    if user:
        print(f"    Using user: {user.username}")
        field, question = get_next_question(user.id)
        print(f"    ✓ Field: {field}")
        print(f"    ✓ Question: {question[:50]}...")
    else:
        print("    ⚠ No user found to test")
except Exception as e:
    print(f"    ✗ ERROR: {e}")
    traceback.print_exc()

print("\n=== ALL TESTS COMPLETE ===")
