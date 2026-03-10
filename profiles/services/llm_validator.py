import logging
import json
import os
from typing import Dict, Any, Optional
from .schemas import ResumeSchema
from .llm_engine import get_llm_client, LLM_MODEL

logger = logging.getLogger(__name__)

# Stage 2: Flexible Extraction System Prompt (ENHANCED)
VALIDATION_SYSTEM_PROMPT = """You are a CV/Resume Data Extraction Expert.
Your task is to extract a complete structured profile from the provided CV text and parsed data.

CRITICAL INSTRUCTIONS:
1. Output MUST be a valid JSON object.
2. Extract standard fields: full_name, email, phone, location, linkedin_url, github_url, skills, experiences, education, projects, certifications.
3. **DYNAMIC FIELDS (The Core Logic)**: If you encounter sections that do NOT fit standard categories, create NEW top-level keys for them in snake_case format.
4. Normalize all data (dates in YYYY-MM format, full URLs).
5. Infer proficiency for skills if context is available.
6. Do NOT omit any information found in the CV.
7. Generate a 'normalized_summary' field: A concise paragraph combining Years of Experience, Top 3 Skills, and Most Recent Role Title.

STRICT NORMALIZATION RULES (APPLY THESE FIRST):
- "Community Service" → "volunteer_experience" (list of objects with: organization, role, description, dates)
- "Volunteer Work" → "volunteer_experience"
- "Volunteering" → "volunteer_experience"
- "Honors & Awards" → "awards" (list of objects with: title, issuer, date, description)
- "Awards" → "awards"
- "Achievements" → "awards"
- "Speaking Engagements" → "speaking_engagements" (list of objects with: event, title, date, description)
- "Talks" → "speaking_engagements"
- "Presentations" → "speaking_engagements"
- "Research Publications" → "publications" (list of objects with: title, authors, journal, year, url)
- "Publications" → "publications"
- "Papers" → "publications"
- "Languages Spoken" → "languages" (list of strings: e.g., ["English (Native)", "Spanish (Fluent)"])
- "Languages" → "languages"
- "Military Service" → "military_experience" (list of objects with: branch, rank, dates, description)
- "Patents" → "patents" (list of objects with: title, patent_number, date, description)

GENERAL RULES:
- ALL dynamic keys MUST be in snake_case (lowercase with underscores).
- If a section contains structured data (like awards with dates), use a list of objects.
- If a section is simple (like languages), use a list of strings.
- Dates should be in YYYY-MM format or YYYY if only year is available.
"""

def perform_reasonableness_check(data: Dict[str, Any]) -> None:
    # ... logic same as before ...
    # 1. Experience Check
    if data.get('experiences') and not data.get('full_name'):
         pass 
    if not data.get('normalized_summary') and data.get('experiences'):
        logger.warning("LLM failed to generate normalized_summary")
    if len(data.get('skills', [])) > 50:
         logger.warning("Suspiciously high skill count detected.")

def validate_and_map_cv_data(parsed_data: Dict[str, Any], raw_cv_text: str) -> Dict[str, Any]:
    """
    Uses the configured LLM (via HuggingFace Inference API) to extract and
    validate CV data. Enforces strict JSON output and validates via Pydantic.
    """
    client = get_llm_client()
    if not client:
        logger.warning("LLM client unavailable, falling back to parsed data")
        return parsed_data

    try:
        logger.info("Preparing LLM validation. Raw text length: %d", len(raw_cv_text))
        
        prompt = f"""
        Please extract the full profile from this CV information.
        
        PARSED DATA (Use as hint/baseline):
        {json.dumps(parsed_data, indent=2)}
        
        RAW CV TEXT (Primary Source):
        {raw_cv_text[:100000]}
        
        Return ONLY the JSON object, no markdown formatting.
        """
        
        # LLM API Call
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": VALIDATION_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=8000
        )
        
        json_content = response.choices[0].message.content
        
        # Parse JSON
        try:
            data_dict = json.loads(json_content)
        except json.JSONDecodeError:
            # Try to clean markdown
            if "```json" in json_content:
                json_content = json_content.split("```json")[1].split("```")[0].strip()
            elif "```" in json_content:
                json_content = json_content.split("```")[1].split("```")[0].strip()
            
            data_dict = json.loads(json_content)
        
        # Stage 4: Pydantic Validation with extra='allow'
        try:
            validated = ResumeSchema(**data_dict)
            final_data = validated.model_dump()
            
            # Preserve extra fields
            for key, value in data_dict.items():
                if key not in final_data:
                    final_data[key] = value
            
            # Reasonableness check
            perform_reasonableness_check(final_data)
            
            logger.info(f"✓ Cerebras extraction successful. Extracted {len(final_data.get('skills', []))} skills")
            return final_data
            
        except Exception as e:
            logger.error(f"Pydantic validation failed: {e}")
            return data_dict
            
    except Exception as e:
        logger.error(f"Cerebras LLM extraction failed: {e}")
        return parsed_data

def get_missing_fields(data: Dict[str, Any]) -> list:
    """Identify key fields missing from the extracted data."""
    required_fields = ['full_name', 'email', 'phone', 'skills', 'experiences', 'education']
    missing = []
    
    for field in required_fields:
        value = data.get(field)
        # Check for None, empty string, or empty list
        if not value:
            missing.append(field)
            
    return missing
