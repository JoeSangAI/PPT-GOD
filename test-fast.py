#!/usr/bin/env python3
"""快速 API 测试（不涉及 LLM 调用，验证端点可用性和基础逻辑）"""
import json, time, uuid
import urllib.request
import urllib.error

BASE = "http://localhost:8000"

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
    return data["id"]

def test_create_project():
    pid = create_project("FAST-创建项目")
    assert len(pid) > 0
    print("✅ test_create_project")

def test_list_projects():
    code, data = req("GET", "/projects")
    assert code == 200
    assert isinstance(data, list)
    print("✅ test_list_projects")

def test_update_project():
    pid = create_project("FAST-更新前")
    code, data = req("PATCH", f"/projects/{pid}", {"title": "FAST-更新后"})
    assert code == 200
    assert data["title"] == "FAST-更新后"
    print("✅ test_update_project")

def test_delete_project():
    pid = create_project("FAST-删除")
    code, data = req("DELETE", f"/projects/{pid}")
    assert code == 200
    code2, _ = req("GET", f"/projects/{pid}")
    assert code2 == 404
    print("✅ test_delete_project")

def test_get_project_status():
    pid = create_project("FAST-状态")
    code, data = req("GET", f"/projects/{pid}/status")
    assert code == 200
    assert "project_status" in data
    print("✅ test_get_project_status")

def test_rollback():
    pid = create_project("FAST-回退")
    # 回退到 planning（即使已经是 planning）
    code, data = req("POST", f"/projects/{pid}/rollback", {"target_stage": "planning"})
    assert code == 200
    assert data["status"] == "planning"
    print("✅ test_rollback")

def test_invalid_rollback():
    pid = create_project("FAST-无效回退")
    code, data = req("POST", f"/projects/{pid}/rollback", {"target_stage": "invalid"})
    assert code == 400
    print("✅ test_invalid_rollback")

def test_empty_title():
    pid = create_project("")
    code, data = req("GET", f"/projects/{pid}")
    assert code == 200
    assert data["title"] == "未命名项目"
    print("✅ test_empty_title")

def test_style_update():
    pid = create_project("FAST-风格更新")
    style = {"name": "测试风格", "palette": ["#000"], "mood": "测试", "font": "测试", "description": "测试"}
    code, data = req("PATCH", f"/projects/{pid}/style", {"selected_style": style})
    assert code == 200
    assert data["selected_style"]["name"] == "测试风格"
    assert data["status"] == "visual_ready"
    print("✅ test_style_update")

def test_stop_generation_no_task():
    pid = create_project("FAST-停止无任务")
    code, data = req("POST", f"/projects/{pid}/stop-generation")
    assert code == 200
    assert "No generation in progress" in data.get("message", "")
    print("✅ test_stop_generation_no_task")

def test_chat_endpoint():
    import requests
    pid = create_project("FAST-聊天")
    url = f"{BASE}/projects/{pid}/chat"
    r = requests.post(url, json={"message": "你好", "history": [], "agent_role": "content"}, stream=True)
    assert r.status_code == 200
    lines = list(r.iter_lines())
    assert len(lines) > 0
    print("✅ test_chat_endpoint")

def test_upload_logo():
    import requests
    pid = create_project("FAST-上传")
    url = f"{BASE}/projects/{pid}/upload"
    # 创建一个临时图片文件
    from PIL import Image
    import io
    img = Image.new('RGB', (100, 100), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    r = requests.post(url, files={"file": ("test.png", buf, "image/png")}, data={"role": "logo"})
    assert r.status_code == 200, f"upload failed: {r.status_code} {r.text}"
    print("✅ test_upload_logo")

if __name__ == "__main__":
    tests = [
        test_create_project, test_list_projects, test_update_project,
        test_delete_project, test_get_project_status, test_rollback,
        test_invalid_rollback, test_empty_title, test_style_update,
        test_stop_generation_no_task, test_chat_endpoint, test_upload_logo,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"❌ {t.__name__}: {e}")
    print(f"\n快速测试: {passed}/{len(tests)} 通过")
