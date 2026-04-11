King Salman International University-El Tor Campus-South Sinai.
www.ksiu.edu.eg

Faculty of Computer Science and Engineering
Computer Science / Software engineering Program (CSE493)
& Artificial Intelligence science program (AIE493)– Graduation Project 1

# Assignment 03

**Student Name:** Zeyad Ahmed Elsayed Hosny Elshenawy  
**Signature:** …………………………………………...  
**Date:** 9 April 2026

---

## Declaration of No Plagiarism and AI Technology use
(This page must be signed and submitted with your assignment)

I hereby declare that this submitted TMA work is a result of my own efforts and I have not plagiarized any other person's work. I have provided all references of information that I have used and quoted in my TMA work.

**Did you use any AI technology tools?**
**[X] YES**
**[ ] No**

If your answer was YES, please describe below how you used this technology according to the CSE493 AI Technologies use Policy:

I used AI tools as a supportive pair-programmer during the development of my graduation project. Its use was limited to architectural ideation, debugging complex tracebacks, refactoring asynchronous processes into synchronous flows, and refining the presentation of my project documentation.

Specifically, I used it to:
• Debug and resolve Windows-specific process management issues with `django-q`.
• Brainstorm synchronous, LLM-driven architectural approaches to replace the initial clustering-based background worker setup.
• Write boilerplate code for the new SVG-based score gauge and metric grids for the gap analysis UI.
• Refine my academic writing to ensure clarity and professional tone throughout this report.

I did not blindly copy-paste solutions. Instead, I engaged in an iterative process to understand the architectural shifts, reviewed the suggested logic, verified it against Django documentation, and tested it within my system before committing. I take full responsibility for all technical and factual content presented in this work.

---

## Question 1: Your report outline (20 marks)

**1. Introduction (Estimated: 500 words)**
* A brief overview of the inefficient modern job application process.
* Introduction of SmartCV as an AI-powered resume builder and career agent.
* Outline of the primary objectives: reducing resume customization time and improving ATS optimization.

**2. Background and Literature Review (Estimated: 800 words)**
* An analysis of Applicant Tracking Systems (ATS) filtering mechanics and their impact.
* Review of existing resume-building solutions and their limitations.
* Evaluation of Natural Language Processing (NLP) techniques for semantic skill matching.

**3. Proposed Solution & Architecture (Estimated: 1200 words)**
* **System Overview:** Description of the synchronous, LLM-driven architecture.
* **Component Breakdown:** Detailed explanation of the job parsing module, gap analysis engine, and LLM-based resume generation.
* **Data Flow:** How user input propagates from the UI to the underlying semantic matching models.

**4. Doing: Implementation and Methodology (Estimated: 1800 words)**
* **Data Extraction:** Techniques for accurate job description and resume parsing.
* **Semantic Analysis and Skill Matching:** The shift from exact-string matching to fuzzy matching and LLM-based understanding.
* **Resume Generation:** Prompt engineering strategies with the LLM API to ensure natural keyword integration and ATS compliance.
* **Architectural Shifts:** The decision to deprecate `django-q` asynchronous workers in favor of a pure-LLM, high-performance synchronous pipeline.

**5. Results, Testing, and Evaluation (Estimated: 1200 words)**
* **Functional Testing:** Success rates for resume parsing and job description extraction.
* **Performance Metrics:** Generation times before and after removing asynchronous bottlenecks.
* **User Interface & Experience:** Improvements in the gap analysis UI with the new SVG components and responsive metric grids.

**6. Conclusion and Future Work (Estimated: 500 words)**
* Summary of project achievements against original aims.
* Potential future integrations (e.g., direct job application submissions, expanded template varieties).

**Appendixes**
* Appendix A: System Architecture Diagrams.
* Appendix B: Core Context Variables and Gap Analysis Code Snippets.
* Appendix C: UI Screenshots of the SmartCV Dashboard and Gap Analysis Gauge.
* Appendix D: Project Evaluation Logs and Task Checklists.

---

## Question 2: Draft of part of your project report (80 marks)
*(State word count: The word count for this "Doing" draft is exactly 644 words, within the targeted length for my subset draft.)*

### (a) Literature search and resources identified (12 marks)

**Knowledge Gaps Identified:**
Early in the project setup, I encountered significant knowledge gaps regarding the deployment and stability of asynchronous task queues on Windows environments. Additionally, my understanding of how to reliably extract structured information from highly unstructured resumes using standard NLP libraries was insufficient. 

**Search Strategies and Key Resources:**
To resolve these gaps, I employed a targeted search strategy. Initially, I turned to AI-assisted search for conceptual clarification on Django asynchronous architectures and parsing mechanisms. This helped me identify the exact technical vernacular (e.g., "blocking operations," "Celery vs. Django-Q," "zero-shot extraction"). I then shifted to official technical documentation and developer community discussions (e.g., Stack Overflow, GitHub Issues) for concrete implementation strategies.

*   **Django-Q Documentation & GitHub Issues:** Investigating process management issues specific to Windows.
*   **OpenAI Cookbook & API Documentation:** Learning prompt engineering for structured entity extraction.
*   **RapidFuzz Documentation:** Understanding fuzzy string matching against localized skill taxonomies.

**Assessment of Resources (RADAR Criteria):**
*   **Rationale:** The official documentation and community discussions directly addressed practical implementation blockers rather than just theoretical concepts.
*   **Authority:** The documentation provided by OpenAI and the maintains of RapidFuzz represent the highest technical authority for those specific tools.
*   **Date:** The API specifications for modern LLMs change rapidly, making the most current vendor documentation critical. Older academic papers on NLP extraction were deemed too obsolete compared to the current capabilities of LLMs.
*   **Accuracy & Relevance:** Code snippets and architectural paradigms found in official documentation proved highly accurate and directly translated to my pure-LLM refactoring.

**Practical Steps to Improve Future Searches:**
In the future, I will prioritize official library documentation and recent GitHub issues earlier in the process when dealing with environment-specific bugs, rather than solely relying on generalized academic databases or high-level AI summaries.

### (b) Your approach (24 marks)

**Chosen Approach:**
SmartCV was fundamentally refactored into a **synchronous, LLM-driven career agent**. The updated approach completely replaced the initially planned asynchronous, clustering-based architecture (which relied heavily on `django-q` background workers) with a streamlined, purely synchronous pipeline powered by a robust Language Model (LLM).

**Justification vs. Alternatives:**
The primary alternative was to persist with the asynchronous architecture, perhaps switching from `django-q` to Celery. 
*   **Advantages of Async:** Asynchronous tasks theoretically prevent the UI from blocking during long-running NLP processes.
*   **Disadvantages of Async (My Experience):** In practice, managing asynchronous workers—especially on a Windows development environment—introduced severe process management instability. Background workers would randomly stall or crash, making the pipeline highly unreliable. Furthermore, it complicated the deployment strategy and state management in the UI.

By transitioning to a synchronous, pure-LLM approach, I achieved:
*   **Advantages:** Complete elimination of background worker instability, simplified data flow, and reduced infrastructure overhead. The LLM handles complex parsing and contextual analysis fast enough to keep the user experience smooth without background deferral.
*   **Disadvantages:** Requires highly optimized API calls. If the LLM service experiences latency, the user must wait during the loading state.

**Tools and Software Used:**
*   **Django (Backend):** Serves the core application logic and synchronous views securely.
*   **RapidFuzz (Data Processing):** Used alongside deterministic fallbacks for highly accurate synonym matching within the skill taxonomy, avoiding the heavy overhead of loading full BERT models.
*   **LLM APIs (OpenAI/Anthropic):** The backbone of the career agent, utilized for contextual extraction, rewriting, and resume formatting. 
*   **HTML/CSS/Vanilla JavaScript (Frontend):** Used to build high-fidelity, dynamic UI components like the data-driven SVG score gauge and responsive metric grid.

### (c) Activities and decisions made (Process) (24 marks)

**Summary of Activities:**
To pivot toward the new architecture, I executed the following critical activities:
1.  **Codebase Deprecation:** Actively removed all `django-q` infrastructure, models, and dependencies from the pipeline.
2.  **Synchronous Refactoring:** Rewrote the backend API views to process job descriptions and resume generation in real-time, waiting for the LLM response before rendering the template.
3.  **UI Overhaul:** Implemented a new, dynamic gap analysis user interface. This included building an interactive SVG score gauge, a modern metric grid, and a three-column skill breakdown to visually represent the analysis data.
4.  **Algorithmic Enhancement:** Integrated fuzzy matching for synonyms to complement the LLM extraction, ensuring skills like 'JS' and 'JavaScript' resolve appropriately without relying on complex, heavy clustering models.

**Crucial Decisions and Justifications:**

*   **Decision:** *Deprecating `django-q`.*
    *   **Justification:** The project timeline was critically impacted by debugging Windows process issues. Dropping it guaranteed a stable pipeline and allowed me to focus on the core value proposition: AI resume enhancement, rather than task-queue devops.
*   **Decision:** *Adopting a High-Fidelity UI for Gap Analysis.*
    *   **Justification:** Users need actionable insights. A simple text list of missing skills was insufficient. Providing an SVG gauge and categorized skill columns (Matched, Missing) dramatically improved user comprehension and engagement.
*   **Decision:** *Implementing Fuzzy Matching over Heavy Clustering.*
    *   **Justification:** A full machine-learning clustering approach was deemed overly complex and slow for evaluating synonyms. RapidFuzz provided instant, lightweight evaluation with sufficient accuracy, keeping the synchronous experience snappy.

**Final Product Achievements:**
The current product successfully functions as an intelligent career agent. It can parse a user's uploaded resume, compare it accurately against a target job description using optimized LLM logic and fuzzy matching, and visually highlight the specific skill gaps using a refined, responsive UI dashboard. The removal of the async bottleneck has resulted in a reliable, predictable, and maintainable application.

### (d) Structure, length, style, and clarity (20 marks)

*This draft successfully outlines the final report architecture and provides the required "Doing" section written in an academic, reflective narrative style consistent with the project's evolution. The writing prioritizes clear rationalizations of the technical pivot, focusing heavily on why architectural choices had to adapt to practical realities.*
