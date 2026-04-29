#!/usr/bin/env python3
"""Run a single scenario with verbose output."""
import json, time, sys
import urllib.request
import urllib.error
import requests

BASE = "http://localhost:8000"
ASSETS = __import__('pathlib').Path(__file__).parent / "test-assets"

def req(method, path, json_data=None):
    url = f"{BASE}{path}"
    data = json.dumps(json_data).encode() if json_data else None
    h = {"Content-Type": "application/json"} if json_data else {}
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        resp = urllib.request.urlopen(r)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        return e.code, json.loads(body) if body else {}

def create_project(title):
    code, data = req("POST", "/projects", {"title": title})
    assert code == 200, f"create failed: {code} {data}"
    print(f"  Created project: {data['id']}")
    return data["id"]

def gen_content_plan(pid, topic, page_count):
    print(f"  Generating content plan...")
    code, data = req("POST", f"/projects/{pid}/content-plan", {"topic": topic, "page_count": page_count})
    assert code == 200, f"content plan failed: {code} {data}"
    print(f"  Content plan queued: {data}")

def poll_slides(pid):
    for i in range(120):
        time.sleep(2)
        code, data = req("GET", f"/projects/{pid}/slides")
        if data and len(data) > 0:
            print(f"  Content plan ready: {len(data)} slides")
            return data
    raise TimeoutError("content plan timeout")

def gen_style_proposals(pid):
    print(f"  Requesting style proposals...")
    code, data = req("POST", f"/projects/{pid}/style-proposals")
    print(f"  Style proposals response: {code} {data}")
    return data

def poll_style_proposals(pid):
    for i in range(120):
        time.sleep(2)
        code, data = req("GET", f"/projects/{pid}")
        data = data or {}
        proposals = (data.get("style_proposal") or {}).get("proposals")
        if proposals:
            print(f"  Style proposals ready: {len(proposals)} proposals")
            return proposals
    raise TimeoutError("style proposals timeout")

def select_style(pid, style):
    print(f"  Selecting style: {style['name']}")
    code, data = req("PATCH", f"/projects/{pid}/style", {"selected_style": style})
    assert code == 200
    print(f"  Status after select: {data['status']}")
    return data

def gen_visual_prompts(pid):
    print(f"  Generating visual prompts...")
    code, data = req("POST", f"/projects/{pid}/visual-prompts")
    assert code == 200, f"visual prompts failed: {code} {data}"
    print(f"  Visual prompts done")
    return data

def get_project(pid):
    code, data = req("GET", f"/projects/{pid}")
    return data

def scenario_2():
    print("\n=== SCENARIO 2: No Assets ===")
    pid = create_project("S2-无素材")
    gen_content_plan(pid, "新能源汽车市场分析", 4)
    poll_slides(pid)
    gen_style_proposals(pid)
    proposals = poll_style_proposals(pid)
    select_style(pid, proposals[0])
    gen_visual_prompts(pid)
    proj = get_project(pid)
    assert proj["status"] == "prompt_ready", f"Expected prompt_ready, got {proj['status']}"
    print(f"✅ SCENARIO 2 PASSED")

def scenario_3():
    print("\n=== SCENARIO 3: Multiple References ===")
    pid = create_project("S3-多素材")
    gen_content_plan(pid, "快消品数字化转型", 6)
    poll_slides(pid)

    for role, fname in [("logo", "logo.png"), ("style_ref", "ref1.png"), ("style_ref", "ref2.png"), ("template", "template.png")]:
        url = f"{BASE}/projects/{pid}/upload"
        with open(ASSETS / fname, "rb") as f:
            r = requests.post(url, files={"file": f}, data={"role": role})
        print(f"  Uploaded {fname}: {r.status_code}")

    gen_style_proposals(pid)
    proposals = poll_style_proposals(pid)
    select_style(pid, proposals[0])
    gen_visual_prompts(pid)
    proj = get_project(pid)
    assert proj["status"] == "prompt_ready"
    print(f"✅ SCENARIO 3 PASSED")

def scenario_6():
    print("\n=== SCENARIO 6: Rollback ===")
    pid = create_project("S6-回退")
    gen_content_plan(pid, "零售行业数字化", 5)
    poll_slides(pid)
    gen_style_proposals(pid)
    proposals = poll_style_proposals(pid)
    select_style(pid, proposals[0])

    proj = get_project(pid)
    assert proj["status"] == "visual_ready"
    print(f"  Before rollback: {proj['status']}")

    code, data = req("POST", f"/projects/{pid}/rollback", {"target_stage": "planning"})
    assert code == 200
    print(f"  Rollback response: {data['status']}")
    assert data["status"] == "planning"
    assert data["selected_style"] is None

    # Retry flow
    gen_style_proposals(pid)
    proposals = poll_style_proposals(pid)
    select_style(pid, proposals[0])
    gen_visual_prompts(pid)
    proj = get_project(pid)
    assert proj["status"] == "prompt_ready"
    print(f"✅ SCENARIO 6 PASSED")

if __name__ == "__main__":
    for fn in [scenario_2, scenario_3, scenario_6]:
        try:
            fn()
        except Exception as e:
            print(f"❌ FAILED: {e}")
            import traceback
            traceback.print_exc()
