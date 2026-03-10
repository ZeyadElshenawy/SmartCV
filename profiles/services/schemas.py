from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Any, Dict
from pydantic import ConfigDict

class Skill(BaseModel):
    name: str
    proficiency: Optional[str] = Field(None, description="Beginner, Intermediate, Advanced, Expert")
    years: Optional[float] = None

class Experience(BaseModel):
    title: str
    company: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    description: Optional[str] = None
    model_config = ConfigDict(extra='allow')

class Education(BaseModel):
    degree: str
    institution: str
    graduation_year: Optional[str] = None
    field: Optional[str] = None
    model_config = ConfigDict(extra='allow')

class Project(BaseModel):
    name: str
    description: Optional[str] = None
    technologies: List[str] = Field(default_factory=list)
    url: Optional[str] = None
    model_config = ConfigDict(extra='allow')

class Certification(BaseModel):
    name: str
    issuer: Optional[str] = None
    date: Optional[str] = None

class ResumeSchema(BaseModel):
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    
    summary: Optional[str] = Field(None, description="Professional summary extracted from CV")
    normalized_summary: Optional[str] = Field(None, description="Standardized summary including years of exp, top skills, and key roles")
    
    skills: List[Skill] = Field(default_factory=list)
    experiences: List[Experience] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)
    projects: List[Project] = Field(default_factory=list)
    certifications: List[Certification] = Field(default_factory=list)
    
    # Catch-all for extra sections is handled by extra='allow' in Config
    model_config = ConfigDict(extra='allow')
