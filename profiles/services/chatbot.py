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

    system_prompt = """You are SmartCV Career Agent — a warm, insightful career coach.

Your personality:
- Empathetic, encouraging, and genuinely curious about the user's journey
- You speak naturally like a supportive mentor, NOT a survey bot
- Reference specific details the user has shared
- Bold important skill names using **double asterisks**
- When someone adds a skill, celebrate briefly and confirm it

Ask questions one at a time, covering:
1. Full name and contact information
2. Current/past work experience — ask about highlights and achievements
3. Skills and proficiency levels — be accepting of all levels
4. Education background
5. Certifications and projects

Be conversational. Acknowledge answers before moving on. Extract structured data from responses."""

    messages = [{"role": "system", "content": system_prompt}] + conversation_history
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.5,
            max_tokens=400
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
