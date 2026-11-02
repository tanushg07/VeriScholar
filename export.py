import io, json, zipfile
from pathlib import Path
from xml.sax.saxutils import escape
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
import citations as cit, pipeline as pl

def docx_bytes(title, blocks):
    d = Document(); d.styles["Normal"].font.name = "Times New Roman"; d.styles["Normal"].font.size = Pt(11)
    h = d.add_heading(title, 0); h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for head, paras in blocks:
        if head: d.add_heading(head, 1)
        for p in paras:
            x = d.add_paragraph(p); x.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY; x.paragraph_format.line_spacing = 1.15
    b = io.BytesIO(); d.save(b); return b.getvalue()

def pdf_bytes(title, blocks):
    ss = getSampleStyleSheet(); body = ParagraphStyle("b", parent=ss["Normal"], fontName="Times-Roman", fontSize=10.5, leading=14, alignment=TA_JUSTIFY, spaceAfter=6)
    t = ParagraphStyle("t", parent=ss["Title"], fontName="Times-Bold", fontSize=18, alignment=TA_CENTER)
    S = [Paragraph(escape(title), t), Spacer(1, 12)]
    for head, paras in blocks:
        if head: S.append(Paragraph(escape(head), ss["Heading2"]))
        S += [Paragraph(escape(p), body) for p in paras]
    b = io.BytesIO(); SimpleDocTemplate(b, pagesize=A4, leftMargin=60, rightMargin=60, topMargin=60, bottomMargin=60, title=title).build(S); return b.getvalue()

def issue_lines(I): return [f"[{x.get('severity','').upper()}] {x['type']}" + (f" ({x['section']})" if x.get("section") else "") + f": {x['detail']}" for x in I] or ["No issues detected."]

def build(j):
    out = pl.DATA / j["id"] / "export"; out.mkdir(parents=True, exist_ok=True); t = j["title"]; ix = j.get("ixc") or {}
    if not ix: pl.ixc_from_generated(j); ix = j["ixc"]
    paper = [("Abstract", ["[Abstract placeholder: see Abstract.docx]"]), ("Keywords", [", ".join(j.get("keywords", []))])] + [(s, cit.render(j, j["text"][s]).split("\n\n")) for s in j["order"] if s in j["text"]] + [("References", cit.reflist(j))]
    allrefs = cit.reflist(j, only_cited=False); gen = cit.check_generated(j)
    vr = [("Verified references (%s)" % j["style"], allrefs)] + [(None, [f"{p['key']}: {p['title']} | DOI: {p.get('doi')} | Source: {p['evidence'].get('source_url')} | Checks: {'; '.join(p['evidence']['checks'])}"]) for p in pl.verified(j)]
    ref_checks = (j.get("upload_check") or {}).get("ref_checks", [])
    cr = [("Disclosure", [j["qa"]["note"] if j.get("qa") else "QA not run."]), ("Generated paper: citation issues", issue_lines(gen["issues"])),
          ("Uploaded paper: citation issues", issue_lines((j.get("upload_check") or {}).get("issues", []))), ("Uploaded paper: reference existence checks", [f"{c['status']}: {c['entry']}" for c in ref_checks] or ["No upload checked."]),
          ("Quality assurance", issue_lines([{**x, "section": x.get("section")} for x in (j.get("qa") or {}).get("issues", [])]))]
    rd = [("Search", [f"Sources: OpenAlex, Semantic Scholar, Crossref. Registry for verification: Crossref.", *[f"Query: {q}" for q in j["queries"]]]),
          ("Selected papers", [f"[{p['status']}] {p['title']} ({p['year']}) {', '.join(p['authors'][:3])}. {p['venue']}. DOI: {p.get('doi')} | found in {p['found_in']} | relevance {p.get('relevance')} | {p['evidence'].get('source_url')}" for p in j["papers"]]),
          ("Excluded candidates", [f"[{p['status']}] {p['title']} ({p['year']}): {p['evidence'].get('reason') or '; '.join(p['evidence'].get('checks', []))}" for p in j["excluded"]] or ["None"]),
          ("Limitations", [f"{len(pl.verified(j))} verified papers against {j['n']} requested.", "Full texts were not accessed; analysis relied on abstracts.", "Google Scholar, IEEE Xplore, Springer and PubMed are not queried directly (no open API); their content is reached only via OpenAlex/Crossref/Semantic Scholar."])]
    parts = lambda k, h, v: [(h, [v] if v else ["Not generated."])]
    F = {"Generated_Research_Paper.docx": docx_bytes(t, paper), "Generated_Research_Paper.pdf": pdf_bytes(t, paper),
         "Abstract.docx": docx_bytes(t, parts(0, "Abstract", ix.get("abstract"))), "Introduction.docx": docx_bytes(t, parts(0, "Introduction", ix.get("introduction"))),
         "Conclusion.docx": docx_bytes(t, parts(0, "Conclusion", ix.get("conclusion"))), "Verified_References.docx": docx_bytes(t, vr),
         "Citation_Verification_Report.pdf": pdf_bytes("Citation Verification Report", cr), "Research_Discovery_Report.pdf": pdf_bytes("Research Discovery Report", rd),
         "Research_Metadata.json": json.dumps({k: v for k, v in j.items() if k != "log"}, indent=1).encode()}
    for n, b in F.items(): (out / n).write_bytes(b)
    with zipfile.ZipFile(out / "Research_Package.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for n in F: z.write(out / n, n)
    return out
