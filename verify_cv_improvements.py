
import sys
import os
import django
from pathlib import Path

# Setup Django environment
sys.path.append('g:/New folder/SmartCV')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartcv.settings')
django.setup()

from profiles.services.cv_parser import CVExtractor

RAW_TEXT = """Mohamed Bahgat
Teaching Assistant / AI Eng ineer
0106319533 Cairo
bahgatm226@gmail.com
Mohammed Bahgat | LinkedIn
MBAHGAT2000
Summary
Teaching Assistant in the Artificial Intelligence Engineering Department at El Alamein
International University, with a focus on deep learning, machine learning, reinforcement
learning, and NLP. Currently leading AI Research and Development at VRtualize Srl,
contributing to impactful projects in healthcare and finance. Passionate about advancing
knowledge in AI through both academic and practical applications, and eager to deepen
expertise in the field through the Master's program in Artificial Intelligence at Nile University.
Professional Experience
AI Research and Development Team Lead
2024/08–present
VRtualize Srl (Italy)
Milan, Italy
Remote • Lead a multidisciplinary team of 12 AI and software researchers to develop cutting-
edge AI solutions.
• Led AI R&D initiatives, driving innovation in healthcare and finance through cutting-
edge machine learning solutions.
• Coordinated cross-functional teams to develop AI-driven applications, improving
operational efficiency and decision-making.
• Published research findings and contributed to the adoption of AI solutions
in diverse fields.
• Collaborate with European corporations and Italian research laboratories to design
innovative solutions for complex challenges in healthcare and beyond.
Teaching Assistant – Artificial Intelligence Engineering Dept.
2024/09–present
El Alamein International University
Alamein,
Matrouh • Taught advanced AI topics, including deep learning, machine learning, reinforcement
learning, and NLP, to undergraduate students.
• Designed and delivered interactive course materials to enhance student engagement
and understanding of complex AI concepts.
2024/07 – 2024/10 AI Engineer at Horeca smart company
• Working on the analysis and forecasting for the sales team using Microsoft
Power BI and Amazon Sage .
• Working on developing chatbots using long-chain,botpress
Internships
2024/05–2024/11 Digital Egypt Pioneers Initiative - DEPI (Generative AI Track)
Cairo This training is under the guidance Ministry of Communications and
Information Technology (MCIT), The main courses: ML , DL, Mlops Tools,
Hugging Face, Variant Types of GANS, NLP With Attention, Gen AI

Education
Bachelor of Computer Science and Artificial Intelligence
Faculty of Computer Science and Artificial Intelligence, Banha University
Graduation Year: [2024] CGPA: 3.30 / 4.00 (Very Good with Honor)
Graduation Project: Automatic Grading for Essay Questions Using Deep Learning Models
• Developed an AI-driven system to automate essay grading, reducing manual correction time,
minimizing human error, and optimizing evaluation costs.
• Utilized advanced language models (LLAMA, MISTRAL, and FLAN T5) to understand semantic
meaning, achieving 97% accuracy on an English dataset and 92% accuracy on an Arabic dataset.
Technical Skills
• Data Analysis (Pandas, Matplotlib, SciPy, Plotly, PowerPI, Signal and Text analysis).
• Software Development (C++, Python, OOP, SQL, Flask, Data Structures, Algorithm, Git, GitHub,
Databases, Linux).
• Applied Mathematics (Calculus, Linear Algebra, Probability, Statistics).
• Machine and deep learning (Sikit-Learn, Pytorch, TensorFlow, Spacy, Cuda, Data Modeling, Model
Optimization, and Evaluation, Generative Modelling, Transformers, Sequence Models,
Reinforcement Learning).
Soft Skills
• Problem-solving & Analytical Thinking: Adept at translating complex data into actionable insights, with
experience in model optimization and evaluation.
• Project Management: Demonstrated ability to balance multiple roles, managing AI research projects,
academic responsibilities, and client relationships.
• Team Leadership & Collaboration: Led a cross-functional team of 12 researchers, driving innovative AI
solutions while fostering a collaborative environment.
"""

def test_improvements():
    extractor = CVExtractor(use_spacy=False, use_ollama=False) # Test regex logic first
    
    # 1. Test Skills Splitting
    print("Testing Skills Extraction...")
    section_positions = extractor.find_section_headers(RAW_TEXT)
    skills = extractor.extract_skills(RAW_TEXT, section_positions)
    
    technical_skills = skills.get('technical_skills', [])
    print(f"Extracted Technical Skills: {technical_skills[:5]}...") # Show first 5
    
    has_pandas = "Pandas" in technical_skills
    has_grouped_item = any("Pandas, Matplotlib" in s for s in technical_skills)
    
    if has_pandas and not has_grouped_item:
        print("✅ Skills split correctly.")
    else:
        print(f"❌ Skills failed. 'Pandas' found: {has_pandas}. Grouped item found: {has_grouped_item}")

    # 2. Test Personal Info (Location & LinkedIn)
    print("\nTesting Personal Info...")
    info = extractor.extract_personal_info(RAW_TEXT)
    
    print(f"Extracted Location: {info.get('address')}")
    if info.get('address') in ["Cairo", "Milan, Italy", "Alamein, Matrouh"]:
        print("✅ Location found.")
    else:
        print("❌ Location missing or incorrect.")
        
    print(f"Extracted LinkedIn: {info.get('linkedin')}")
    if info.get('linkedin') and "linkedin.com" in info.get('linkedin'):
        print("✅ LinkedIn URL found.")
    else:
        print("❌ LinkedIn URL missing or incorrect.")

    # 3. Test Education
    print("\nTesting Education...")
    education = extractor.extract_education(RAW_TEXT, section_positions)
    if education:
        grad_date = education[0].get('graduation_date')
        print(f"Extracted Graduation Date: {grad_date}")
        if grad_date == "2024":
             print("✅ Graduation date found.")
        else:
             print("❌ Graduation date incorrect.")
    else:
        print("❌ No education extracted.")

    # 4. Test Work Experience (Artifacts & Company Name)
    print("\nTesting Work Experience...")
    experiences = extractor.extract_experience(RAW_TEXT, section_positions)
    
    found_depi = False
    clean_role = True
    
    for exp in experiences:
        print(f"Role: {exp.get('position')}, Company: {exp.get('company')}")
        if "Digital Egypt Pioneers Initiative" in (exp.get('company') or "") or "Digital Egypt Pioneers Initiative" in (exp.get('position') or ""):
             # Just checking if it parsed somewhat resonably
             if exp.get('company') == "Digital Egypt Pioneers Initiative" or "DEPI" in str(exp.get('company')):
                  found_depi = True
        
        if exp.get('responsibilities'):
            first_resp = exp['responsibilities'][0]
            if first_resp.startswith("Remote"):
                print(f"❌ Artifact 'Remote' found in: {first_resp[:20]}...")
                clean_role = False
    
    if clean_role:
        print("✅ Artifacts cleaned.")
    
    if found_depi:
        print("✅ DEPI Company found.")
    else:
        print("❌ DEPI Company not correctly parsed.")

if __name__ == "__main__":
    test_improvements()
