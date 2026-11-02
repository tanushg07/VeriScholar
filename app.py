import logging, threading, tempfile, os
from dotenv import load_dotenv; load_dotenv()
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import pipeline as pl, citations as cit, export as ex
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
app = FastAPI(title="Research Paper Generator")
class NewJob(BaseModel): title: str; sections: list[str]; style: str = "IEEE"; n: int = 25; instructions: str = ""
def job(i):
    j = pl.load(i)
    if not j: raise HTTPException(404, "Unknown job")
    return j
def bg(j, stage, fn, *a): threading.Thread(target=pl.safe, args=(j, stage, fn, *a), daemon=True).start()
def bg_pipeline(j): threading.Thread(target=pl.run_pipeline, args=(j,), daemon=True).start()

@app.post("/api/jobs")
def create(b: NewJob):
    if not b.title.strip() or not b.sections: raise HTTPException(400, "Title and at least one section required")
    j = pl.new_job(b.title.strip(), [s.strip() for s in b.sections if s.strip()], b.style if b.style in ("IEEE", "APA") else "IEEE", b.n, b.instructions)
    bg_pipeline(j); return {"id": j["id"]}
@app.get("/api/jobs/{i}")
def state(i: str):
    j = job(i); return {**j, "rendered": {s: cit.render(j, t) for s, t in j["text"].items()}, "reflist": cit.reflist(j), "gen_check": cit.check_generated(j) if j["text"] else None}
@app.delete("/api/jobs/{i}/papers/{key}")
def drop(i: str, key: str):
    j = job(i); p = next((p for p in j["papers"] if p["key"] == key), None)
    if p: j["papers"].remove(p); pl.save(j)
    return {"ok": True}
class Doi(BaseModel): doi: str
@app.post("/api/jobs/{i}/papers")
def add(i: str, b: Doi):
    try: return pl.add_doi(job(i), b.doi)
    except ValueError as e: raise HTTPException(400, str(e))
@app.post("/api/jobs/{i}/generate")
def gen(i: str): j = job(i); bg(j, "generation", pl.generate); return {"ok": True}
@app.post("/api/jobs/{i}/sections/{name}/regenerate")
def regen(i: str, name: str):
    j = job(i)
    if name not in j["order"]: raise HTTPException(404, "Unknown section")
    def f(j): pl.write_section(j, name); pl.qa(j)
    bg(j, "generation", f); return {"ok": True}
@app.post("/api/jobs/{i}/upload")
def upload(i: str, file: UploadFile = File(...)):
    j = job(i)
    if not file.filename.lower().endswith((".pdf", ".docx")): raise HTTPException(400, "Only PDF or DOCX")
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as t: t.write(file.file.read())
    text = pl.extract(t.name, file.filename); os.unlink(t.name)
    if len(text.strip()) < 500: raise HTTPException(400, "Could not read enough text (scanned PDF?)")
    bg(j, "generation", pl.process_upload, text, file.filename); return {"ok": True}
@app.post("/api/jobs/{i}/export")
def export(i: str): j = job(i); ex.build(j); return {"files": sorted(p.name for p in (pl.DATA / i / "export").iterdir())}
@app.get("/api/jobs/{i}/file/{name}")
def file(i: str, name: str):
    f = pl.DATA / i / "export" / os.path.basename(name)
    if not f.exists(): raise HTTPException(404, "Run export first")
    return FileResponse(f, filename=f.name)
app.mount("/", StaticFiles(directory="frontend", html=True))
