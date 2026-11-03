# Research Paper Generator
Setup: `pip install -r requirements.txt && cp .env.example .env` (add keys) then ``uvicorn app:app --port 8000 and open http://localhost:8000.
Flow: start research → review/remove/add-by-DOI papers → Generate → (optional) upload paper → Export → Download ZIP.
Pipeline: OpenAlex + Semantic Scholar + Crossref discovery → relevance scoring (LLM) → Crossref DOI verification (title/year/author checks, evidence stored)
→ abstract-level analysis → sections written with [@key] markers that are rendered to IEEE/APA, so every citation maps to a stored reference.
Limits: full texts are not read; Scholar/IEEE/Springer/PubMed are not queried directly; QA is automated and does not prove originality.
Job state: ./data/<id>.json, exports in ./data/<id>/export/.
