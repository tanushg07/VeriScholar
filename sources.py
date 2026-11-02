import os, re, time, logging, difflib, requests
log = logging.getLogger("sources")
MAIL = os.getenv("CONTACT_EMAIL") or os.getenv("CROSSREF_MAILTO", "")
S = requests.Session(); S.headers["User-Agent"] = f"ResearchPaperGenerator/1.0 (mailto:{MAIL})"

def get(url, params=None, headers=None, tries=4):
    for i in range(tries):
        try:
            r = S.get(url, params=params, headers=headers, timeout=25)
            if r.status_code in (429, 500, 502, 503): time.sleep(2 ** i); continue
            if r.status_code == 404: return None
            r.raise_for_status(); return r.json()
        except requests.RequestException as e:
            log.warning("GET %s failed: %s", url, e); time.sleep(2 ** i)
    return None

def norm_doi(d): return re.sub(r"^https?://(dx\.)?doi\.org/", "", d.strip()).lower() if d else None
def norm(t): return re.sub(r"\W+", " ", (t or "").lower()).strip()
def sim(a, b): return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()
def _abs(inv):
    w = {p: k for k, v in (inv or {}).items() for p in v}
    return " ".join(w[i] for i in sorted(w))

def openalex(q, n):
    p = {"search": q, "per-page": n, "filter": "has_doi:true,type:article", "mailto": MAIL}
    if os.getenv("OPENALEX_API_KEY"): p["api_key"] = os.getenv("OPENALEX_API_KEY")
    j = get("https://api.openalex.org/works", p) or {}
    return [dict(title=w.get("title") or "", authors=[a["author"]["display_name"] for a in w.get("authorships", [])], year=w.get("publication_year"),
        venue=((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "", doi=norm_doi(w.get("doi")), url=w.get("id"),
        abstract=_abs(w.get("abstract_inverted_index")), cited_by=w.get("cited_by_count", 0), found_in="OpenAlex") for w in j.get("results", [])]

def s2(q, n):
    h = {"x-api-key": os.environ["S2_API_KEY"]} if os.getenv("S2_API_KEY") else None
    j = get("https://api.semanticscholar.org/graph/v1/paper/search", {"query": q, "limit": n, "fields": "title,authors,year,venue,externalIds,abstract,url,citationCount"}, h) or {}
    return [dict(title=p.get("title") or "", authors=[a["name"] for a in p.get("authors", [])], year=p.get("year"), venue=p.get("venue") or "",
        doi=norm_doi((p.get("externalIds") or {}).get("DOI")), url=p.get("url"), abstract=p.get("abstract") or "", cited_by=p.get("citationCount", 0), found_in="Semantic Scholar") for p in j.get("data", [])]

def _cr(m):
    yr = ((m.get("issued") or {}).get("date-parts") or [[None]])[0][0]
    return dict(title=(m.get("title") or [""])[0], authors=[f'{a.get("given","")} {a.get("family","")}'.strip() for a in m.get("author", [])], year=yr,
        venue=(m.get("container-title") or [""])[0], doi=norm_doi(m.get("DOI")), url=m.get("URL"), abstract=re.sub("<[^>]+>", "", m.get("abstract") or ""),
        cited_by=m.get("is-referenced-by-count", 0), found_in="Crossref")

def crossref(q, n):
    j = get("https://api.crossref.org/works", {"query.bibliographic": q, "rows": n, "mailto": MAIL}) or {}
    return [_cr(m) for m in (j.get("message") or {}).get("items", [])]

def crossref_doi(doi):
    j = get(f"https://api.crossref.org/works/{doi}", {"mailto": MAIL})
    return _cr(j["message"]) if j else None

def verify(p):
    """Check a candidate against Crossref. Sets p['status'] and p['evidence']; never defaults to VERIFIED."""
    ev = dict(checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), checks=[])
    rec, via_doi = None, False
    if p.get("doi"):
        rec = crossref_doi(p["doi"]); via_doi = rec is not None
        ev["doi_resolves"] = via_doi
        if via_doi: ev["source_url"] = f"https://doi.org/{p['doi']}"
    if rec is None and p.get("title"):
        best = max(crossref(p["title"], 3), key=lambda x: sim(x["title"], p["title"]), default=None)
        if best and sim(best["title"], p["title"]) >= 0.9:
            rec = best; ev["note"] = "Matched by title search; the supplied DOI did not resolve" if p.get("doi") else "Matched by title search (no DOI supplied)"
            ev["source_url"] = f"https://doi.org/{best['doi']}" if best.get("doi") else best.get("url")
    if rec is None:
        p.update(status="UNVERIFIED", evidence={**ev, "reason": "No Crossref record found"}); return p
    t = sim(rec["title"], p["title"]); y = not (rec["year"] and p.get("year")) or abs(rec["year"] - p["year"]) <= 1
    sn = lambda L: {a.split()[-1].lower() for a in L[:1] if a.split()}
    a_ok = not (rec["authors"] and p["authors"]) or bool(sn(rec["authors"]) & sn(p["authors"]))
    ev["checks"] = [f"title similarity {t:.2f}", f"year {'ok' if y else 'MISMATCH'} (source {p.get('year')} vs Crossref {rec['year']})", f"first author {'ok' if a_ok else 'MISMATCH'}"]
    ev["crossref_record"] = {k: rec[k] for k in ("title", "authors", "year", "venue", "doi")}
    ok = via_doi and t >= 0.85 and y and a_ok
    p["status"] = "VERIFIED" if ok else ("PARTIALLY VERIFIED" if t >= 0.6 else "UNVERIFIED")
    if ok:  # canonical metadata from the registry
        p.update(title=rec["title"], authors=rec["authors"] or p["authors"], year=rec["year"] or p["year"], venue=rec["venue"] or p["venue"])
    p["evidence"] = ev; return p
