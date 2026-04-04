from openai import OpenAI
from decouple import config
import json

# Initialize client only if key is present to avoid errors on startup module load
# But here we do it in function or global.
client = None
try:
    key = config('OPENAI_API_KEY', default=None)
    if key:
        client = OpenAI(api_key=key)
except:
    pass

def chat_with_user(conversation_history):
    """
    Chatbot for guided profile creation
    conversation_history: list of {"role": "user/assistant", "content": "..."}
    """
    if not client:
        return "AI Service not configured (OPENAI_API_KEY missing)."

    system_prompt = """You are a helpful career assistant helping users create their professional profile.
Ask questions one at a time about:
1. Full name and contact information
2. Current/past work experience
3. Skills and proficiency levels
4. Education background
5. Certifications and projects

Be conversational and encouraging. Extract structured data from user responses."""

    messages = [{"role": "system", "content": system_prompt}] + conversation_history
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.7,
            max_tokens=200
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error communicating with AI: {str(e)}"

def extract_profile_from_conversation(conversation_history):
    """
    Extract structured profile data from chatbot conversation
    """
    if not client:
        return {}

    # Use GPT to extract structured data
    extraction_prompt = f"""
Based on this conversation, extract the user's profile information in JSON format:
{json.dumps(conversation_history)}

Return JSON with keys: full_name, email, phone, location, skills (list of objects), experiences (list of objects), education (list of objects).

=== STRICT ANTI-HALLUCINATION RULE (CRITICAL) ===
- Never invent, add, or imply skills, keywords, achievements, metrics, job titles, or any other content not explicitly stated by the user in the conversation.
"""
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": extraction_prompt}],
            temperature=0,
            response_format={ "type": "json_object" }
        )
        
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Extraction error: {e}")
        return {}
