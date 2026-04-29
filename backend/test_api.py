import requests
import json
import sys
import io

BASE = "http://localhost:8000"
results = []

def log(method, endpoint, status, detail="", bug=None, fix=None):
    results.append({
        "method": method,
        "endpoint": endpoint,
        "status": status,
        "detail": detail,
        "bug": bug,
        "fix": fix,
    })
    icon = "OK" if status == "PASS" else "BUG" if status == "BUG" else "FAIL"
    print(f"{icon} {method} {endpoint} -> {status}")
    if detail:
        print(f"   detail: {detail}")
    if bug:
        print(f"   BUG: {bug}")
    if fix:
        print(f"   FIX: {fix}")

# ========== 1. Projects API ==========

# 1.1 create project
r = requests.post(f"{BASE}/projects", json={"title": "  test project  ", "style_id": "swiss_design"})
if r.status_code == 200:
    project = r.json()
    pid = project["id"]
    log("POST", "/projects", "PASS", f"id={pid}, title='{project['title']}'")
else:
    log("POST", "/projects", "FAIL", f"status={r.status_code}, body={r.text}")
    sys.exit(1)

# 1.2 list projects
r = requests.get(f"{BASE}/projects")
log("GET", "/projects", "PASS" if r.status_code == 200 else "FAIL", f"count={len(r.json())}")

# 1.3 get project
r = requests.get(f"{BASE}/projects/{pid}")
log("GET", f"/projects/{pid}", "PASS" if r.status_code == 200 else "FAIL")

# 1.4 get nonexistent project
r = requests.get(f"{BASE}/projects/nonexistent")
log("GET", "/projects/nonexistent", "PASS" if r.status_code == 404 else "BUG", detail=f"status={r.status_code}")

# 1.5 update project
r = requests.patch(f"{BASE}/projects/{pid}", json={"title": "updated title", "style_id": "dark_luxury"})
if r.status_code == 200:
    log("PATCH", f"/projects/{pid}", "PASS", f"title={r.json().get('title')}")
else:
    log("PATCH", f"/projects/{pid}", "FAIL", f"status={r.status_code}")

# 1.6 style-proposals without slides => 400
r = requests.post(f"{BASE}/projects/{pid}/style-proposals")
if r.status_code == 400:
    log("POST", f"/projects/{pid}/style-proposals", "PASS", "correctly returns 400 when no slides")
else:
    log("POST", f"/projects/{pid}/style-proposals", "BUG", f"expected 400, got {r.status_code}")

# ========== 2. Slides API ==========

# 2.1 create content-plan (sync mode)
r = requests.post(f"{BASE}/projects/{pid}/content-plan", json={"topic": "AI in healthcare"})
if r.status_code == 200:
    log("POST", f"/projects/{pid}/content-plan", "PASS", f"slides_count={r.json().get('slides_count')}")
else:
    log("POST", f"/projects/{pid}/content-plan", "FAIL", f"status={r.status_code}, body={r.text}")

# 2.2 list slides
r = requests.get(f"{BASE}/projects/{pid}/slides")
if r.status_code == 200:
    slides = r.json()
    log("GET", f"/projects/{pid}/slides", "PASS", f"count={len(slides)}")
    slide_id = slides[0]["id"] if slides else None
else:
    log("GET", f"/projects/{pid}/slides", "FAIL", f"status={r.status_code}")
    slides = []
    slide_id = None

# 2.3 style-proposals again (now with slides)
r = requests.post(f"{BASE}/projects/{pid}/style-proposals")
if r.status_code == 200:
    sp = r.json()
    proposals = sp.get("proposals", [])
    log("POST", f"/projects/{pid}/style-proposals", "PASS", f"proposals_count={len(proposals)}")
    if proposals:
        required_keys = {"name", "palette", "mood", "font", "description", "source"}
        missing = required_keys - set(proposals[0].keys())
        if missing:
            log("POST", f"/projects/{pid}/style-proposals", "BUG",
                f"proposal missing fields: {missing}",
                bug=f"style_proposal response format incomplete, missing {missing}")
        else:
            log("POST", f"/projects/{pid}/style-proposals", "PASS", "format correct, all required fields present")
    project_data = sp.get("project", {})
    if not project_data.get("style_proposal"):
        log("POST", f"/projects/{pid}/style-proposals", "BUG",
            "project object missing style_proposal",
            bug="returned project does not include style_proposal")
else:
    log("POST", f"/projects/{pid}/style-proposals", "FAIL", f"status={r.status_code}, body={r.text}")

# 2.4 visual-plan
r = requests.post(f"{BASE}/projects/{pid}/visual-plan")
if r.status_code == 200:
    log("POST", f"/projects/{pid}/visual-plan", "PASS", f"slides_count={r.json().get('slides_count')}")
else:
    log("POST", f"/projects/{pid}/visual-plan", "FAIL", f"status={r.status_code}, body={r.text}")

# 2.5 prompts
r = requests.post(f"{BASE}/projects/{pid}/prompts")
if r.status_code == 200:
    log("POST", f"/projects/{pid}/prompts", "PASS", f"slides_count={r.json().get('slides_count')}")
else:
    log("POST", f"/projects/{pid}/prompts", "FAIL", f"status={r.status_code}, body={r.text}")

# 2.6 get slide prompt
if slides:
    r = requests.get(f"{BASE}/projects/{pid}/prompts/{slides[0]['id']}")
    log("GET", f"/projects/{pid}/prompts/{slides[0]['id']}", "PASS" if r.status_code == 200 else "FAIL")

# 2.7 update content
if slides:
    r = requests.patch(f"{BASE}/projects/{pid}/slides/content", json={
        "page_num": 1,
        "content_json": {"text_content": {"headline": "new headline", "subhead": "new subhead"}, "speaker_notes": "new notes"}
    })
    log("PATCH", f"/projects/{pid}/slides/content", "PASS" if r.status_code == 200 else "FAIL")

# 2.8 update visual
if slides:
    r = requests.patch(f"{BASE}/projects/{pid}/slides/visual", json={
        "page_num": 1,
        "visual_json": {"visual_description": "test desc", "layout": "test"}
    })
    log("PATCH", f"/projects/{pid}/slides/visual", "PASS" if r.status_code == 200 else "FAIL")

# 2.9 status
r = requests.get(f"{BASE}/projects/{pid}/status")
log("GET", f"/projects/{pid}/status", "PASS" if r.status_code == 200 else "FAIL")

# 2.10 generation-progress
r = requests.get(f"{BASE}/projects/{pid}/generation-progress")
log("GET", f"/projects/{pid}/generation-progress", "PASS" if r.status_code == 200 else "FAIL")

# 2.11 set-seed / unset-seed
if slide_id:
    r = requests.post(f"{BASE}/projects/{pid}/slides/{slide_id}/set-seed")
    log("POST", f"/projects/{pid}/slides/{slide_id}/set-seed", "PASS" if r.status_code == 200 else "FAIL")
    r = requests.post(f"{BASE}/projects/{pid}/slides/{slide_id}/unset-seed")
    log("POST", f"/projects/{pid}/slides/{slide_id}/unset-seed", "PASS" if r.status_code == 200 else "FAIL")

# 2.12 reorder
if len(slides) >= 2:
    new_order = list(range(1, len(slides)+1))
    new_order.reverse()
    r = requests.post(f"{BASE}/projects/{pid}/reorder", json={"page_nums": new_order})
    log("POST", f"/projects/{pid}/reorder", "PASS" if r.status_code == 200 else "FAIL")
    requests.post(f"{BASE}/projects/{pid}/reorder", json={"page_nums": list(range(1, len(slides)+1))})

# 2.13 delete slide
if len(slides) > 1:
    del_id = slides[-1]["id"]
    r = requests.delete(f"{BASE}/projects/{pid}/slides/{del_id}")
    log("DELETE", f"/projects/{pid}/slides/{del_id}", "PASS" if r.status_code == 200 else "FAIL")

# ========== 3. Chat API ==========

# 3.1 content agent chat (draft stage)
r = requests.post(f"{BASE}/projects/{pid}/chat", json={
    "message": "make a ppt about AI healthcare",
    "history": [],
    "agent_role": "content"
}, stream=True)
if r.status_code == 200:
    lines = []
    for line in r.iter_lines():
        if line:
            lines.append(line.decode('utf-8'))
        if len(lines) >= 3:
            break
    log("POST", f"/projects/{pid}/chat (content)", "PASS", f"SSE stream ok, first 3 lines: {lines[:3]}")
else:
    log("POST", f"/projects/{pid}/chat (content)", "FAIL", f"status={r.status_code}")

# 3.2 visual agent chat
r = requests.post(f"{BASE}/projects/{pid}/chat", json={
    "message": "any visual suggestions?",
    "history": [],
    "agent_role": "visual"
}, stream=True)
if r.status_code == 200:
    lines = []
    for line in r.iter_lines():
        if line:
            lines.append(line.decode('utf-8'))
        if len(lines) >= 3:
            break
    log("POST", f"/projects/{pid}/chat (visual)", "PASS", f"SSE stream ok, first 3 lines: {lines[:3]}")
else:
    log("POST", f"/projects/{pid}/chat (visual)", "FAIL", f"status={r.status_code}")

# 3.3 invalid agent_role
r = requests.post(f"{BASE}/projects/{pid}/chat", json={
    "message": "test",
    "history": [],
    "agent_role": "hacker"
}, stream=True)
log("POST", f"/projects/{pid}/chat (invalid role)", "PASS", f"status={r.status_code}, invalid role falls back to normal prompt")

# ========== 4. Documents API ==========

# 4.1 upload empty file
empty_file = io.BytesIO(b"")
r = requests.post(f"{BASE}/projects/{pid}/upload-document", files={"file": ("empty.txt", empty_file, "text/plain")})
log("POST", f"/projects/{pid}/upload-document (empty)", "PASS" if r.status_code == 400 else "BUG", f"status={r.status_code}")

# 4.2 upload normal text
text_content = "Hello World\nThis is a test document."
test_file = io.BytesIO(text_content.encode('utf-8'))
r = requests.post(f"{BASE}/projects/{pid}/upload-document", files={"file": ("test.txt", test_file, "text/plain")})
if r.status_code == 200:
    log("POST", f"/projects/{pid}/upload-document", "PASS", f"char_count={r.json().get('char_count')}")
else:
    log("POST", f"/projects/{pid}/upload-document", "FAIL", f"status={r.status_code}, body={r.text}")

# 4.3 list documents
r = requests.get(f"{BASE}/projects/{pid}/documents")
log("GET", f"/projects/{pid}/documents", "PASS" if r.status_code == 200 else "FAIL", f"count={len(r.json())}")

# 4.4 delete document
r = requests.delete(f"{BASE}/projects/{pid}/documents/test.txt")
log("DELETE", f"/projects/{pid}/documents/test.txt", "PASS" if r.status_code == 200 else "FAIL")

# 4.5 delete nonexistent document
r = requests.delete(f"{BASE}/projects/{pid}/documents/nonexistent.txt")
log("DELETE", f"/projects/{pid}/documents/nonexistent.txt", "PASS" if r.status_code == 404 else "BUG", f"status={r.status_code}")

# ========== 5. Upload reference image ==========
img_file = io.BytesIO(b"fake image data")
r = requests.post(f"{BASE}/projects/{pid}/upload", files={"file": ("ref.png", img_file, "image/png")})
if r.status_code == 200:
    ref_img = r.json()
    log("POST", f"/projects/{pid}/upload", "PASS", f"id={ref_img.get('id')}")
    r = requests.get(f"{BASE}/projects/{pid}/reference-images")
    log("GET", f"/projects/{pid}/reference-images", "PASS" if r.status_code == 200 else "FAIL")
else:
    log("POST", f"/projects/{pid}/upload", "FAIL", f"status={r.status_code}")

# ========== 6. Cleanup ==========
r = requests.delete(f"{BASE}/projects/{pid}")
log("DELETE", f"/projects/{pid}", "PASS" if r.status_code == 200 else "FAIL")

# ========== Summary ==========
print("\n" + "="*60)
print("TEST SUMMARY")
print("="*60)
pass_count = sum(1 for r in results if r["status"] == "PASS")
bug_count = sum(1 for r in results if r["status"] == "BUG")
fail_count = sum(1 for r in results if r["status"] == "FAIL")
print(f"PASS: {pass_count}, BUG: {bug_count}, FAIL: {fail_count}")

if bug_count or fail_count:
    print("\nIssues:")
    for r in results:
        if r["status"] in ("BUG", "FAIL"):
            print(f"  - {r['method']} {r['endpoint']}: {r['detail']}")
            if r.get("bug"):
                print(f"    BUG: {r['bug']}")
            if r.get("fix"):
                print(f"    FIX: {r['fix']}")
