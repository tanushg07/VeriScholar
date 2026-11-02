import json, math, os, re, threading, time, uuid, logging
from pathlib import Path
import sources as src, llm, citations as cit
DATA = Path(os.getenv("DATA_DIR", "data")); DATA.mkdir(exist_ok=True)
LOCK = threading.RLock(); JOBS = {}; log = logging.getLogger("pipeline")
GUIDE = {"Introduction": "Motivate the topic, state scope and aim, outline the paper's structure.", "Background": "Define core concepts and foundations.",
 "Literature Review": "Synthesize thematically, not paper by paper; compare methods, findings and disagreements.", "Comparative Analysis": "Compare approaches along concrete dimensions using only the extracted analysis.",
 "Research Gaps": "State gaps that the sources' limitations/gaps support.", "Discussion": "Interpret the synthesis; label your own inferences as interpretation.", "Future Scope": "Present directions as proposals, citing sources that motivate them."}
SYS = ("You are a careful academic writer. Write formal prose in plain paragraphs (no markdown, bullets or headings). Use ONLY the supplied sources. Cite every source-based claim with [@key] "
 "(several: [@k1, @k2]); never cite unsupplied keys; never invent papers, data, statistics or experimental results. Source notes derive from abstracts only, so do not claim detail beyond them. "
 "Distinguish published findings from proposals.")

def save(j): (DATA / f"{j['id']}.json").write_text(json.dumps(j, indent=1))
def load(jid):
    if jid not in JOBS and (DATA / f"{jid}.json").exists(): JOBS[jid] = json.loads((DATA / f"{jid}.json").read_text())
    return JOBS.get(jid)
def st(j, stage, status, pct, msg=""):
    with LOCK:
        j["stages"][stage] = dict(status=status, pct=pct, msg=msg); j["log"] = (j["log"] + [f"{time.strftime('%H:%M:%S')} {stage}: {msg}"])[-200:]; save(j)
def new_job(title, sections, style, n, instr):
    j = dict(id=uuid.uuid4().hex[:10], title=title, sections=sections, style=style, n=max(25, min(30, n)), instructions=instr, seq=0, papers=[], excluded=[], text={}, order=[], keywords=[],
        synthesis=None, upload=None, ixc={}, upload_check=None, qa=None, queries=[], log=[], stages={k: dict(status="idle", pct=0, msg="") for k in ("discovery", "verification", "analysis", "generation")})
    JOBS[j["id"]] = j; save(j); return j
def safe(j, stage, fn, *a):
    try: fn(j, *a)
    except Exception as e:
        log.exception("%s failed", stage); st(j, stage, "error", j["stages"][stage]["pct"], f"{type(e).__name__}: {e}")

def _run_stage(j, stage, fn):
    """Run one pipeline stage; returns True on success, False on error."""
    try:
        fn(j)
        return True
    except Exception as e:
        log.exception("%s failed", stage)
        st(j, stage, "error", j["stages"][stage]["pct"], f"{type(e).__name__}: {e}")
        return False

def run_pipeline(j):
    """Run the full pipeline: discover → verify (inside discover) → analyze → generate."""
    if not _run_stage(j, "discovery", discover): return
    if not _run_stage(j, "analysis", analyze): return
    _run_stage(j, "generation", generate)

def score(j, cands):
    for i in range(0, len(cands), 15):
        b = cands[i:i + 15]
        try:
            r = llm.complete_json("Rate each paper's relevance (0-10) to the research topic.", f"Topic: {j['title']}\nSections: {j['sections']}\nPapers: " + json.dumps([dict(i=k, title=p["title"], abstract=p["abstract"][:350]) for k, p in enumerate(b)]) + '\nReturn [{"i":int,"score":number}]')
            sc = {x["i"]: x["score"] for x in r}
        except Exception as e: log.warning("scoring failed: %s", e); sc = {}
        for k, p in enumerate(b):
            w = set(src.norm(j["title"]).split()); p["relevance"] = sc.get(k, round(10 * len(w & set(src.norm(p["title"] + p["abstract"]).split())) / max(len(w), 1)))

def discover(j):
    st(j, "discovery", "running", 3, "Searching OpenAlex, Semantic Scholar, Crossref")
    qs = j["queries"] = [j["title"]] + [f'{j["title"]} {s}' for s in j["sections"]]; found = {}
    for i, q in enumerate(qs):
        for fn, k in ((src.openalex, 15), (src.s2, 15), (src.crossref, 8)):
            for p in fn(q, k):
                key = p["doi"] or src.norm(p["title"]).replace(" ", "")
                if p["title"] and key not in found: found[key] = p
        st(j, "discovery", "running", 3 + int(55 * (i + 1) / len(qs)), f"{len(found)} unique candidates")
    c = list(found.values()); st(j, "discovery", "running", 65, "Scoring relevance"); score(j, c)
    c = sorted([x for x in c if x["relevance"] >= 6], key=lambda x: (x["relevance"], math.log1p(x.get("cited_by") or 0)), reverse=True)[:math.ceil(j["n"] * 1.5)]
    st(j, "discovery", "done", 100, f"{len(c)} relevant candidates")
    st(j, "verification", "running", 0, "Verifying against Crossref")
    for i, p in enumerate(c):
        src.verify(p); st(j, "verification", "running", int(100 * (i + 1) / len(c)), f"{i+1}/{len(c)} checked")
    rank = {"VERIFIED": 0, "PARTIALLY VERIFIED": 1, "UNVERIFIED": 2}
    c.sort(key=lambda x: (rank[x["status"]], -x["relevance"]))
    keep = [p for p in c if p["status"] != "UNVERIFIED"][:j["n"]]
    for p in keep: j["seq"] += 1; p["key"] = f"p{j['seq']}"
    j["papers"] = keep; j["excluded"] = [p for p in c if p not in keep]
    nv = sum(p["status"] == "VERIFIED" for p in keep)
    st(j, "verification", "done", 100, f"{nv} verified, {len(keep)-nv} partially verified, {len(j['excluded'])} excluded (target {j['n']})")

def add_doi(j, doi):
    rec = src.crossref_doi(src.norm_doi(doi))
    if not rec: raise ValueError("DOI does not resolve in Crossref")
    rec["found_in"] = "Manual DOI"; rec["relevance"] = None; src.verify(rec); j["seq"] += 1; rec["key"] = f"p{j['seq']}"; j["papers"].append(rec); save(j); return rec

def verified(j): return [p for p in j["papers"] if p["status"] == "VERIFIED"]
def analyze(j):
    V = verified(j); st(j, "analysis", "running", 0, f"Analyzing {len(V)} verified papers (abstract-level)")
    for i in range(0, len(V), 4):
        b = V[i:i + 4]
        r = llm.complete_json("Extract structured notes from abstracts. Use null where the abstract does not say. Never guess.",
            "Papers: " + json.dumps([dict(key=p["key"], title=p["title"], abstract=p["abstract"][:1800]) for p in b]) + '\nReturn [{"key","objective","methods","technologies","findings","limitations","gaps","future"}]', 4000)
        m = {x["key"]: x for x in r}
        for p in b: p["analysis"] = m.get(p["key"], {}); p["full_text_accessed"] = False; p["abstract_available"] = bool(p["abstract"])
        st(j, "analysis", "running", int(90 * min(i + 4, len(V)) / len(V)), f"{min(i+4, len(V))}/{len(V)}")
    j["synthesis"] = llm.complete("Synthesize across papers: themes, agreements, disagreements, relationships, gaps. Plain text; cite with [@key].", json.dumps([dict(key=p["key"], year=p["year"], title=p["title"], **p["analysis"]) for p in V]), 3000)
    st(j, "analysis", "done", 100, "Analysis and synthesis complete")

def write_section(j, name):
    V = verified(j)
    if not V: raise ValueError("No verified papers to cite")
    ctx = json.dumps([dict(key=p["key"], authors=p["authors"][:3], year=p["year"], title=p["title"], venue=p["venue"], notes=p.get("analysis")) for p in V])
    done = {k: v[:300] for k, v in j["text"].items() if k != name}
    j["text"][name] = llm.complete(SYS, f"Paper title: {j['title']}\nSection to write: {name}\nGuidance: {GUIDE.get(name, 'Cover this topic in depth using the literature.')}\nUser instructions: {j['instructions']}\n"
        f"Cross-source synthesis:\n{j['synthesis']}\nSources:\n{ctx}\nOther sections (starts): {json.dumps(done)}\nWrite 450-800 words.", 3000)
    save(j)

def generate(j):
    if not j["synthesis"]: analyze(j)  # fallback when called directly (e.g. manual regen)
    j["order"] = ["Introduction", "Background", "Literature Review"] + j["sections"] + ["Comparative Analysis", "Research Gaps", "Discussion", "Future Scope"]
    st(j, "generation", "running", 0, "Writing sections")
    for i, s in enumerate(j["order"]):
        write_section(j, s); st(j, "generation", "running", int(90 * (i + 1) / len(j["order"])), f"Wrote {s}")
    try: j["keywords"] = llm.complete_json("Give 5-8 keywords.", f"Title: {j['title']}\nSections: {j['sections']}\nReturn a JSON list of strings.", 300)
    except Exception: j["keywords"] = []
    qa(j); st(j, "generation", "done", 100, "Paper generated; QA complete")

def qa(j):
    I = [dict(check="Citation accuracy", **x) for x in cit.check_generated(j)["issues"] if x["type"] != "CLAIM_NEEDS_CITATION"] + \
        [dict(check="Unsupported claims", **x) for x in cit.check_generated(j)["issues"] if x["type"] == "CLAIM_NEEDS_CITATION"]
    seen = {}
    for sec, t in j["text"].items():
        if len(t.split()) < 200: I.append(dict(check="Section completeness", severity="medium", section=sec, detail=f"Only {len(t.split())} words", type="SHORT_SECTION"))
        for s in cit.sentences(t):
            k = src.norm(s)
            if len(k) > 60 and k in seen: I.append(dict(check="Duplicate content", severity="medium", section=sec, detail=s[:120], type="DUPLICATE"))
            seen[k] = sec
    n = len(verified(j))
    if n < j["n"]: I.append(dict(check="Reference completeness", severity="medium", section="-", type="FEWER_PAPERS", detail=f"{n} verified papers vs {j['n']} requested; none were invented to fill the gap"))
    I.append(dict(check="Disclosure", severity="info", section="-", type="ABSTRACT_ONLY", detail="Literature analysis used titles/abstracts from open metadata; full texts were not read"))
    try:
        I += [dict(check="Writing/logic review", type="LLM_REVIEW", **x) for x in llm.complete_json("You are a peer reviewer. Report genuine problems only in academic writing quality, logical consistency, relevance, grammar.",
            "Return [{\"section\",\"severity\",\"detail\"}] for this draft:\n" + json.dumps(j["text"])[:30000], 1500)]
    except Exception as e: I.append(dict(check="Writing/logic review", severity="info", section="-", type="SKIPPED", detail=f"LLM review unavailable: {e}"))
    j["qa"] = dict(issues=I, note="Automated checks only. This does not establish plagiarism-free status or publication readiness; run a plagiarism checker and have the paper peer-reviewed."); save(j)

def extract(path, name):
    if name.lower().endswith(".pdf"):
        from pypdf import PdfReader; return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    from docx import Document; return "\n".join(p.text for p in Document(path).paragraphs)

def process_upload(j, text, name):
    j["upload"] = dict(filename=name, chars=len(text)); j["upload_check"] = cit.check_uploaded(text); write_ixc(j, text, "upload")
def write_ixc(j, text, source):
    r = llm.complete_json("Write the Abstract (150-250 words), Introduction and Conclusion for the supplied paper. Use ONLY content in it: no new claims, results, methods or contributions; add no citations that the paper itself lacks. Plain prose.",
        f"Title: {j['title']}\nPaper text:\n{text[:90000]}\nReturn a JSON object with exactly these keys: {{\"abstract\": \"...\", \"introduction\": \"...\", \"conclusion\": \"...\"}}", 5000)
    j["ixc"] = dict(source=source, **r); save(j)
def ixc_from_generated(j): write_ixc(j, "\n\n".join(f"{s}\n{cit.render(j, j['text'][s])}" for s in j["order"] if s in j["text"]), "generated paper (no upload provided)")
