import re, collections
import sources as src
MARK = re.compile(r"\[@([^\]]+)\]")
CLAIM = re.compile(r"\d+(\.\d+)?\s?%|\bet al\b|\bstud(y|ies)\b|\bresearchers?\b|\bhas been (shown|demonstrated|reported)\b|\boutperform|\bsignificantly\b|\bstate[- ]of[- ]the[- ]art\b|\bevidence\b", re.I)
sentences = lambda t: [s for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]
def keys_in(t): return [k.strip().lstrip("@") for m in MARK.findall(t) for k in re.split(r"[,;]", m) if k.strip()]

def order(j):
    seen = []
    for sec in j.get("order", []):
        for k in keys_in(j["text"].get(sec, "")):
            if k not in seen: seen.append(k)
    ver = [p["key"] for p in j["papers"] if p["status"] == "VERIFIED"]
    return [k for k in seen if k in ver], [k for k in ver if k not in seen]

def _surname(a): return a.split()[-1] if a.split() else a
def _initials(a): return " ".join(w[0] + "." for w in a.split()[:-1])
def fmt_ref(p, style, n=None):
    A = p["authors"]; doi = f"https://doi.org/{p['doi']}" if p.get("doi") else p.get("url") or ""
    if style == "IEEE":
        names = [f"{_initials(a)} {_surname(a)}".strip() for a in A[:6]] + (["et al."] if len(A) > 6 else [])
        return f"[{n}] {', '.join(names)}, \"{p['title']},\" {p['venue']}, {p['year']}. {doi}"
    names = [f"{_surname(a)}, {_initials(a)}" for a in A[:20]]
    s = names[0] if len(names) == 1 else ", ".join(names[:-1]) + ", & " + names[-1] if names else ""
    return f"{s} ({p['year']}). {p['title']}. {p['venue']}. {doi}"

def render(j, text):
    cited, _ = order(j); P = {p["key"]: p for p in j["papers"]}
    def one(k):
        if k not in P or P[k]["status"] != "VERIFIED": return "?"
        if j["style"] == "IEEE": return str(cited.index(k) + 1)
        a = P[k]["authors"]; s = _surname(a[0]) if a else "Anon"
        return f"{s}{' et al.' if len(a) > 2 else (' & ' + _surname(a[1]) if len(a) == 2 else '')}, {P[k]['year']}"
    def sub(m):
        ks = [k.strip().lstrip("@") for k in re.split(r"[,;]", m.group(1)) if k.strip()]
        return "[" + ", ".join(one(k) for k in ks) + "]" if j["style"] == "IEEE" else "(" + "; ".join(one(k) for k in ks) + ")"
    return MARK.sub(sub, text)

def reflist(j, only_cited=True):
    cited, rest = order(j); P = {p["key"]: p for p in j["papers"]}
    ks = cited if only_cited else cited + rest
    rows = [(P[k], i + 1) for i, k in enumerate(ks)]
    if j["style"] == "APA": rows.sort(key=lambda r: _surname(r[0]["authors"][0]) if r[0]["authors"] else "")
    return [fmt_ref(p, j["style"], n) for p, n in rows]

def check_generated(j):
    I, P = [], {p["key"]: p for p in j["papers"]}; used = collections.Counter()
    for sec, text in j["text"].items():
        for s in sentences(text):
            ks = keys_in(s)
            for k in ks:
                used[k] += 1
                if k not in P: I.append(dict(type="MISSING_REFERENCE", severity="high", section=sec, detail=f"Cites unknown key {k}"))
                elif P[k]["status"] != "VERIFIED": I.append(dict(type="UNVERIFIED_CITATION", severity="high", section=sec, detail=f"{k} is {P[k]['status']}"))
            if not ks and CLAIM.search(s): I.append(dict(type="CLAIM_NEEDS_CITATION", severity="medium", section=sec, detail=s[:200]))
            for sn, yr in re.findall(r"\b([A-Z][a-z]+)(?: et al\.| and [A-Z][a-z]+)?,? \(?((?:19|20)\d\d)\)?", s):
                if not any(p["authors"] and _surname(p["authors"][0]) == sn and str(p["year"]) == yr for p in P.values()):
                    I.append(dict(type="INCORRECT_AUTHOR_YEAR", severity="high", section=sec, detail=f"'{sn} {yr}' matches no reference"))
    for p in P.values():
        if p["status"] == "VERIFIED" and not used[p["key"]]: I.append(dict(type="UNUSED_REFERENCE", severity="low", section="-", detail=p["title"]))
    return dict(total_citations=sum(used.values()), issues=I)

def split_refs(text):
    m = list(re.finditer(r"^\s*(references|bibliography)\s*$", text, re.I | re.M))
    if not m: return text, []
    body, tail = text[:m[-1].start()], text[m[-1].end():].strip()
    ents = re.split(r"\n\s*(?=\[\d+\]|\d+\.\s)", tail)
    if len(ents) < 3: ents = re.split(r"\n(?=[A-Z][A-Za-z'\-]+,\s)", tail)
    return body, [re.sub(r"\s+", " ", e).strip() for e in ents if len(e.strip()) > 20]

def check_uploaded(text, max_lookups=40):
    body, refs = split_refs(text); I = []
    if not refs: return dict(style=None, n_refs=0, issues=[dict(type="NO_REFERENCE_LIST", severity="high", detail="No 'References' heading or entries detected")], ref_checks=[])
    ieee = re.findall(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]", body)
    apa = [c for g in re.findall(r"\(([^()]*?\b(?:19|20)\d\d[a-z]?[^()]*?)\)", body) for c in g.split(";")]
    style = "IEEE" if len(ieee) >= len(apa) else "APA"; cited = set()
    if style == "IEEE":
        for g in ieee:
            for part in re.split(r"\s*,\s*", g):
                r = re.split(r"\s*[–-]\s*", part); lo, hi = int(r[0]), int(r[-1])
                cited.update(range(lo, hi + 1))
        for n in sorted(cited):
            if n > len(refs): I.append(dict(type="MISSING_REFERENCE", severity="high", detail=f"[{n}] cited but list has {len(refs)} entries"))
        for n in range(1, len(refs) + 1):
            if n not in cited: I.append(dict(type="UNUSED_REFERENCE", severity="low", detail=f"Reference {n}: {refs[n-1][:100]}"))
    else:
        hit = set()
        for c in apa:
            m = re.search(r"([A-Z][A-Za-z'\-]+).*?((?:19|20)\d\d)", c)
            if not m: continue
            sn, yr = m.groups(); match = [i for i, r in enumerate(refs) if sn in r[:80]]
            if not match: I.append(dict(type="MISSING_REFERENCE", severity="high", detail=f"({sn}, {yr}) has no reference entry"))
            elif not any(yr in refs[i] for i in match): I.append(dict(type="INCORRECT_AUTHOR_YEAR", severity="high", detail=f"{sn} is in the list but not with year {yr}"))
            else: hit.update(i for i in match if yr in refs[i])
        I += [dict(type="UNUSED_REFERENCE", severity="low", detail=r[:100]) for i, r in enumerate(refs) if i not in hit]
    for s in sentences(body):
        if CLAIM.search(s) and not (re.search(r"\[\d", s) or re.search(r"\b(19|20)\d\d\)", s)):
            I.append(dict(type="CLAIM_NEEDS_CITATION", severity="medium", detail=s[:200]))
    checks = []
    for r in refs[:max_lookups]:
        d = re.search(r"10\.\d{4,9}/[^\s\"<>]+", r); rec = None
        if d: rec = src.crossref_doi(d.group(0).rstrip(".,;)").lower())
        if not rec: rec = (src.crossref(r[:300], 1) or [None])[0]
        w = set(src.norm(rec["title"]).split()) if rec and rec["title"] else set()
        ok = bool(w) and len(w & set(src.norm(r).split())) / len(w) >= 0.8
        checks.append(dict(entry=r[:160], status="FOUND" if ok else "SUSPICIOUS", matched_doi=rec["doi"] if ok else None))
        if not ok: I.append(dict(type="SUSPICIOUS_REFERENCE", severity="high", detail=f"Not matched in Crossref (may be a book or non-indexed work): {r[:140]}"))
    return dict(style=style, n_refs=len(refs), n_citations=len(cited) or len(apa), issues=I, ref_checks=checks)
