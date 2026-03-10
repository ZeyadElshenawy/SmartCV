import logging
import json
from profiles.services.llm_engine import get_llm_client, LLM_MODEL

logger = logging.getLogger(__name__)

def generate_learning_path(skills_list):
    """
    Generate a learning path for a list of missing skills.
    Returns JSON containing course recommendations and tutorials.
    """
    client = get_llm_client()
    
    if not client:
        logger.warning("LLM unavailable for learning path generation")
        return []

    if not skills_list:
        return []
        
    prompt = f"""You are an expert technical career coach. I have a user who is repeatedly missing the following skills in the jobs they are applying for:
    
{', '.join(skills_list)}

Please generate a concrete, actionable learning path to help them acquire these skills. 

For each skill, provide:
1. A brief explanation of why it is important in the current market.
2. 1-2 Recommended free/low-cost resources (e.g., specific YouTube channels, Coursera, official documentation).
3. A practical project idea they can build to put it on their resume.

Output MUST be strictly valid JSON in the following format:
[
  {{
    "skill": "Skill Name",
    "importance": "Why it matters",
    "resources": [
      {{"name": "Course/Resource Name", "type": "Video/Interactive/Text"}}
    ],
    "project_idea": "Build an X that does Y"
  }}
]

Return ONLY JSON. No markdown formatting around the output."""

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            timeout=45,
        )
        
        content = response.choices[0].message.content.strip()
        
        # Parse JSON response
        import re
        match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', content, re.DOTALL)
        if match:
             json_text = match.group(1)
        else:
            # Try finding array brackets if markdown parsing fails
            start = content.find('[')
            end = content.rfind(']')
            if start != -1 and end != -1:
                json_text = content[start:end+1]
            else:
                json_text = content
                
        learning_path = json.loads(json_text)
        logger.info(f"Generated learning path for {len(skills_list)} skills")
        return learning_path
        
    except Exception as e:
        logger.exception(f"Learning path generation failed: {e}")
        return []
