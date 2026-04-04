import logging
import json
import os
from typing import Dict, Any, Optional
from .schemas import ResumeSchema
from .llm_engine import get_structured_llm, get_llm

logger = logging.getLogger(__name__)

# Stage 2: Flexible Extraction System Prompt (ENHANCED)
VALIDATION_SYSTEM_PROMPT = """You are a CV/Resume Data Extraction Expert.
Your task is to extract a complete structured profile from the provided CV text and parsed data.

CRITICAL INSTRUCTIONS:
1. Extract standard fields: full_name, email, phone, location, linkedin_url, github_url, skills, experiences, education, projects, certifications.
2. **DYNAMIC FIELDS (The Core Logic)**: If you encounter sections that do NOT fit standard categories, create NEW top-level keys for them in snake_case format.
3. Normalize all data (dates in YYYY-MM format, full URLs).
4. Infer proficiency for skills if context is available.
5. **URL EXTRACTION**: Pay close attention to `[Embedded Link: ...]` tags in the text. Map these URLs into the `url` fields of the corresponding `projects` and `certifications` objects!
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

=== STRICT ANTI-HALLUCINATION RULE (CRITICAL) ===
- Never invent, add, or imply skills, keywords, achievements, metrics, job titles, or any other content not present in the original raw text.
- Only extract and restructure what actually exists.

=== SCHEMA MAPPING RULES (CRITICAL) ===
- For Work Experience and Projects: If the text contains bullet points or lists of achievements, you MUST put them in the `highlights` array field.
- Do NOT put arrays or bullet points into the `description` field. The `description` field should be used ONLY for a single short paragraph summarizing the role. If there is no summary paragraph, leave `description` null and put everything in `highlights`.

=== REMOVE FROM EXTRACTED DATA ===
- Street/home address (extract city and country only)
- Objective statements (exclude them entirely)
- Graduation year if graduation date is more than 10 years ago
- Work experience older than 15 years (20 years max for executive roles)
- High school education
- GPA or university grades
- Headshot or photo references
- Salary expectations
- All soft skills (e.g., "teamwork", "detail-oriented"). Extract ONLY hard technical skills.
"""

def perform_reasonableness_check(data: Dict[str, Any]) -> None:
    if data.get('experiences') and not data.get('full_name'):
         pass 
    if not data.get('normalized_summary') and data.get('experiences'):
        logger.warning("LLM failed to generate normalized_summary")
    if len(data.get('skills', [])) > 50:
         logger.warning("Suspiciously high skill count detected.")

def validate_and_map_cv_data(parsed_data: Dict[str, Any], raw_cv_text: str) -> Dict[str, Any]:
    """
    Uses LangChain + Groq with structured output to extract and validate CV data.
    Output is guaranteed to match ResumeSchema via Pydantic.
    """
    try:
        logger.info("Preparing LLM validation. Raw text length: %d", len(raw_cv_text))
        
        schema_definition = json.dumps(ResumeSchema.model_json_schema(), indent=2)
        
        prompt = f"""
{VALIDATION_SYSTEM_PROMPT}

Please extract the full profile from this CV information.

TARGET JSON SCHEMA:
{schema_definition}

PARSED DATA (Use as hint/baseline):
{json.dumps(parsed_data, indent=2)}

RAW CV TEXT (Primary Source):
{raw_cv_text[:100000]}

Return the structured profile matching the TARGET JSON SCHEMA precisely.
"""
        
        structured_llm = get_structured_llm(ResumeSchema, temperature=0.1, max_tokens=8000)
        result = structured_llm.invoke(prompt)
        
        # result is already a validated ResumeSchema instance
        final_data = result.model_dump()
        
        # Reasonableness check
        perform_reasonableness_check(final_data)
        
        logger.info(f"✓ LLM extraction successful. Extracted {len(final_data.get('skills', []))} skills")
        return final_data
            
    except Exception as e:
        logger.error(f"LLM extraction failed: {e}")
        return parsed_data

def get_missing_fields(data: Dict[str, Any]) -> list:
    """Identify key fields missing from the extracted data."""
    required_fields = ['full_name', 'email', 'phone', 'skills', 'experiences', 'education']
    missing = []
    
    for field in required_fields:
        value = data.get(field)
        if not value:
            missing.append(field)
            
    return missing
