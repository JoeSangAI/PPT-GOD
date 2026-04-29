#!/usr/bin/env python3
"""
PPT GOD 端到端测试脚本
测试范围：创建项目 → 内容规划 → 上传素材 → 风格提案 → 选择风格 → 生图方案(prompt_ready)
绝不调用 /generate（实际生图）
"""
import json, os, sys, time, traceback, uuid
from pathlib import Path

BASE = "http://localhost:8000"
ASSETS = Path(__file__).parent / "test-assets"

def req(method, path, json_data=None, files=None, timeout=180):
    import urllib.request
    url = f"{BASE}{path}"
    if files:
        import requests
        r = requests.request(method, url, files=files, json=json_data, timeout=timeout)
        return r.status_code, r.json() if r.text else {}
    else:
        data = json.dumps(json_data).encode() if json_data else None
        h = {"Content-Type": "application/json"} if json_data else {}
        r = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            resp = urllib.request.urlopen(r, timeout=timeout)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read()
            return e.code, json.loads(body) if body else {}

def create_project(title):
    code, data = req("POST", "/projects", {"title": title})
    assert code == 200, f"create failed: {code} {data}"
    return data["id"]

def gen_content_plan(pid, topic=None, page_count=None):
    body = {}
    if topic: body["topic"] = topic
    if page_count: body["page_count"] = page_count
    code, data = req("POST", f"/projects/{pid}/content-plan", body)
    assert code == 200, f"content plan failed: {code} {data}"
    return data

def get_slides(pid):
    code, data = req("GET", f"/projects/{pid}/slides")
    assert code == 200
    return data

def get_project(pid):
    code, data = req("GET", f"/projects/{pid}")
    assert code == 200
    return data

def poll_content_plan(pid, max_wait=120):
    """内容规划是后台异步生成，需要轮询等待。"""
    for i in range(max_wait):
        time.sleep(2)
        slides = get_slides(pid)
        if slides and len(slides) > 0:
            return slides
    raise TimeoutError("content plan generation timeout")

def upload_file(pid, filepath, role, timeout=60):
    import requests
    url = f"{BASE}/projects/{pid}/upload"
    with open(filepath, "rb") as f:
        r = requests.post(url, files={"file": f}, data={"role": role}, timeout=timeout)
    return r.status_code, r.json() if r.text else {}

def gen_style_proposals(pid):
    code, data = req("POST", f"/projects/{pid}/style-proposals")
    assert code in (200, 202), f"style proposals failed: {code} {data}"
    return data

def poll_style_proposals(pid, max_wait=120):
    for i in range(max_wait):
        time.sleep(2)
        code, data = req("GET", f"/projects/{pid}")
        data = data or {}
        if (data.get("style_proposal") or {}).get("proposals"):
            return data["style_proposal"]["proposals"]
    raise TimeoutError("style proposals timeout")

def select_style(pid, style):
    code, data = req("PATCH", f"/projects/{pid}/style", {"selected_style": style})
    assert code == 200, f"select style failed: {code} {data}"
    return data

def gen_visual_prompts(pid):
    try:
        code, data = req("POST", f"/projects/{pid}/visual-prompts", timeout=300)
    except TimeoutError:
        # 同步 API 可能耗时较长，超时后检查项目状态
        print(f"  Visual prompts API timed out, checking project status...")
        proj = get_project(pid)
        if proj.get("status") == "prompt_ready":
            print(f"  Project already prompt_ready (backend completed)")
            return proj
        raise
    assert code == 200, f"visual prompts failed: {code} {data}"
    return data

def get_status(pid):
    code, data = req("GET", f"/projects/{pid}/status")
    return data

def retry_failed(pid):
    code, data = req("POST", f"/projects/{pid}/retry-failed")
    return code, data

def rollback(pid, stage):
    code, data = req("POST", f"/projects/{pid}/rollback", {"target_stage": stage})
    return code, data

def update_slide_content(pid, page_num, content_json):
    code, data = req("PATCH", f"/projects/{pid}/slides/content", {
        "page_num": page_num,
        "content_json": content_json
    })
    return code, data

def upload_document(pid, filepath):
    import requests
    url = f"{BASE}/projects/{pid}/upload-document"
    with open(filepath, "rb") as f:
        r = requests.post(url, files={"file": f})
    return r.status_code, r.json() if r.text else {}

def chat_with_agent(pid, message, history=None, agent_role="content"):
    import requests
    url = f"{BASE}/projects/{pid}/chat"
    r = requests.post(url, json={
        "message": message,
        "history": history or [],
        "agent_role": agent_role
    }, stream=True, timeout=60)
    return r.status_code, r

def run_scenario(name, steps_fn):
    print(f"\n{'='*60}")
    print(f"SCENARIO: {name}")
    print(f"{'='*60}")
    try:
        steps_fn()
        print(f"✅ SCENARIO PASSED: {name}")
        return True
    except Exception as e:
        print(f"❌ SCENARIO FAILED: {name}")
        traceback.print_exc()
        return False

# ============================================================
# 具体场景
# ============================================================

def scenario_1_basic_flow():
    """基础流程：创建 → 内容规划 → 确认 → 上传Logo → 风格提案 → 选风格 → 生图方案"""
    pid = create_project("S1-基础流程测试")
    gen_content_plan(pid, topic="AI时代的品牌营销", page_count=5)
    slides = poll_content_plan(pid)
    assert len(slides) > 0, "内容规划未生成"

    # 模拟确认内容计划（直接修改状态，或调用确认API）
    # 注意：目前后端没有显式的"确认内容计划"API，前端通过 local state 处理
    # 我们直接继续到视觉阶段

    # 上传 Logo
    logo = ASSETS / "logo.png"
    code, data = upload_file(pid, logo, "logo")
    assert code == 200, f"logo upload failed: {code} {data}"

    # 生成风格提案
    gen_style_proposals(pid)
    proposals = poll_style_proposals(pid)
    assert len(proposals) >= 1, "风格提案未生成"
    print(f"  收到 {len(proposals)} 套风格提案")

    # 选择第一套风格
    style = proposals[0]
    select_style(pid, style)

    # 检查状态变为 visual_ready
    proj = get_project(pid)
    assert proj["status"] == "visual_ready", f"选风格后状态应为 visual_ready，实际是 {proj['status']}"

    # 生图方案（文本提示词）
    gen_visual_prompts(pid)

    # 检查状态变为 prompt_ready
    proj = get_project(pid)
    assert proj["status"] == "prompt_ready", f"生图方案后状态应为 prompt_ready，实际是 {proj['status']}"

    # 检查 prompts 已生成
    slides = get_slides(pid)
    for s in slides:
        assert s.get("prompt_text"), f"Slide {s['page_num']} 缺少 prompt_text"
    print(f"  全部 {len(slides)} 页 prompt 已生成")

def scenario_2_no_assets():
    """无素材流程：不上传任何文件，直接生成风格"""
    pid = create_project("S2-无素材测试")
    gen_content_plan(pid, topic="新能源汽车市场分析", page_count=4)
    poll_content_plan(pid)

    gen_style_proposals(pid)
    proposals = poll_style_proposals(pid)
    assert len(proposals) >= 1

    select_style(pid, proposals[0])
    gen_visual_prompts(pid)

    proj = get_project(pid)
    assert proj["status"] == "prompt_ready"
    print(f"  无素材流程完成，状态: {proj['status']}")

def scenario_3_multiple_refs():
    """多参考图：上传 Logo + 2张风格参考 + 1张模板"""
    pid = create_project("S3-多素材测试")
    gen_content_plan(pid, topic="快消品数字化转型", page_count=6)
    poll_content_plan(pid)

    upload_file(pid, ASSETS / "logo.png", "logo")
    upload_file(pid, ASSETS / "ref1.png", "style_ref")
    upload_file(pid, ASSETS / "ref2.png", "style_ref")
    upload_file(pid, ASSETS / "template.png", "template")

    gen_style_proposals(pid)
    proposals = poll_style_proposals(pid)
    select_style(pid, proposals[0])
    gen_visual_prompts(pid)

    proj = get_project(pid)
    assert proj["status"] == "prompt_ready"
    print(f"  多素材流程完成")

def scenario_4_edit_then_proceed():
    """编辑内容后继续：修改 slide 内容，然后生成风格"""
    pid = create_project("S4-编辑内容测试")
    gen_content_plan(pid, topic="SaaS企业出海策略", page_count=5)
    slides = poll_content_plan(pid)

    # 修改第一页内容
    s0 = slides[0]
    new_content = dict(s0.get("content_json", {}))
    new_content["title"] = "【修改后】" + new_content.get("title", "")
    code, data = update_slide_content(pid, s0["page_num"], new_content)
    assert code == 200, f"update slide failed: {code} {data}"

    # 继续流程
    gen_style_proposals(pid)
    proposals = poll_style_proposals(pid)
    select_style(pid, proposals[0])
    gen_visual_prompts(pid)

    proj = get_project(pid)
    assert proj["status"] == "prompt_ready"
    print(f"  编辑后流程完成")

def scenario_5_regenerate_style():
    """重新生成风格提案：第一次不满意，重新生成"""
    pid = create_project("S5-重选风格测试")
    gen_content_plan(pid, topic="医疗健康AI应用", page_count=4)
    poll_content_plan(pid)

    gen_style_proposals(pid)
    p1 = poll_style_proposals(pid)

    # 强制重新生成
    gen_style_proposals(pid)  # 注意：目前 force 参数默认 false，可能返回缓存
    # 直接清空缓存模拟重新生成
    req("PATCH", f"/projects/{pid}/style", {"selected_style": None})

    # 重新获取（如果有缓存则直接用）
    proj = get_project(pid)
    if not proj.get("style_proposal", {}).get("proposals"):
        gen_style_proposals(pid)
        p1 = poll_style_proposals(pid)

    select_style(pid, p1[0])
    gen_visual_prompts(pid)

    proj = get_project(pid)
    assert proj["status"] == "prompt_ready"
    print(f"  风格重选流程完成")

def scenario_6_rollback():
    """回退测试：走到 visual_ready 然后回退到 planning"""
    pid = create_project("S6-回退测试")
    gen_content_plan(pid, topic="零售行业数字化", page_count=5)
    poll_content_plan(pid)

    # 走到选风格
    gen_style_proposals(pid)
    proposals = poll_style_proposals(pid)
    select_style(pid, proposals[0])

    proj = get_project(pid)
    assert proj["status"] == "visual_ready"

    # 回退到 planning
    code, data = rollback(pid, "planning")
    assert code == 200, f"rollback failed: {code} {data}"

    proj = get_project(pid)
    assert proj["status"] == "planning", f"回退后状态应为 planning，实际是 {proj['status']}"
    assert proj["selected_style"] is None, "回退后 selected_style 应为空"
    assert proj["style_proposal"] is None, "回退后 style_proposal 应为空"

    # 重新走一遍
    gen_style_proposals(pid)
    proposals = poll_style_proposals(pid)
    select_style(pid, proposals[0])
    gen_visual_prompts(pid)

    proj = get_project(pid)
    assert proj["status"] == "prompt_ready"
    print(f"  回退后再走流程完成")

def scenario_7_chat_agent_content():
    """Agent 聊天驱动内容规划"""
    pid = create_project("S7-Agent聊天测试")

    # 通过 Agent 聊天生成内容（模拟）
    # 注意：chat 接口返回 SSE stream，解析比较复杂
    # 这里简化：直接调用 content-plan API，但验证 chat 接口可用
    code, resp = chat_with_agent(pid, "我想做一份关于跨境电商的PPT", agent_role="content")
    assert code == 200, f"chat failed: {code}"

    # 读取流式响应
    lines = []
    for line in resp.iter_lines():
        if line:
            lines.append(line.decode())

    # 解析最后一条 JSON
    last_data = None
    for line in lines:
        if line.startswith("data: "):
            try:
                last_data = json.loads(line[6:])
            except:
                pass

    assert last_data is not None, "Agent 未返回数据"
    assert "action" in last_data or "message" in last_data, f"Agent 返回格式异常: {last_data}"
    print(f"  Agent 聊天返回: {last_data.get('action', 'answer')}")

    # 继续标准流程
    gen_content_plan(pid, topic="跨境电商趋势分析", page_count=5)
    poll_content_plan(pid)

    gen_style_proposals(pid)
    proposals = poll_style_proposals(pid)
    select_style(pid, proposals[0])
    gen_visual_prompts(pid)

    proj = get_project(pid)
    assert proj["status"] == "prompt_ready"
    print(f"  Agent 聊天流程完成")

def scenario_8_document_upload():
    """上传文档作为内容参考"""
    pid = create_project("S8-文档上传测试")

    # 创建一个临时 txt 文档
    doc_path = ASSETS / "test_doc.txt"
    doc_path.write_text("这是一份关于人工智能在金融行业应用的调研报告。\n\n第一章：市场现状...\n第二章：技术趋势...\n第三章：应用案例...")

    code, data = upload_document(pid, doc_path)
    assert code == 200, f"doc upload failed: {code} {data}"

    gen_content_plan(pid, topic="AI金融应用", page_count=5)
    poll_content_plan(pid)

    gen_style_proposals(pid)
    proposals = poll_style_proposals(pid)
    select_style(pid, proposals[0])
    gen_visual_prompts(pid)

    proj = get_project(pid)
    assert proj["status"] == "prompt_ready"
    print(f"  文档上传流程完成")

def scenario_9_visual_chat_style():
    """视觉阶段通过 Agent 聊天调整风格"""
    pid = create_project("S9-视觉聊天测试")
    gen_content_plan(pid, topic="智能制造2025", page_count=5)
    poll_content_plan(pid)

    # 上传 Logo
    upload_file(pid, ASSETS / "logo.png", "logo")

    # 生成风格
    gen_style_proposals(pid)
    proposals = poll_style_proposals(pid)

    # 模拟 Agent 聊天调整风格（验证接口）
    code, resp = chat_with_agent(pid, "我想要更科技感一点的风格", agent_role="visual")
    assert code == 200
    lines = []
    for line in resp.iter_lines():
        if line:
            lines.append(line.decode())

    last_data = None
    for line in lines:
        if line.startswith("data: "):
            try:
                last_data = json.loads(line[6:])
            except:
                pass

    print(f"  视觉 Agent 返回: {last_data}")

    # 继续选择风格
    select_style(pid, proposals[0])
    gen_visual_prompts(pid)

    proj = get_project(pid)
    assert proj["status"] == "prompt_ready"
    print(f"  视觉聊天流程完成")

def scenario_10_long_content():
    """长内容：生成较多页数"""
    pid = create_project("S10-长内容测试")
    gen_content_plan(pid, topic="全球气候变化与碳中和战略", page_count=10)
    slides = poll_content_plan(pid)
    assert len(slides) >= 8, f"期望至少8页，实际 {len(slides)} 页"

    gen_style_proposals(pid)
    proposals = poll_style_proposals(pid)
    select_style(pid, proposals[0])
    gen_visual_prompts(pid)

    proj = get_project(pid)
    assert proj["status"] == "prompt_ready"
    slides = get_slides(pid)
    for s in slides:
        assert s.get("prompt_text"), f"Slide {s['page_num']} 缺少 prompt"
    print(f"  长内容 {len(slides)} 页流程完成")

# ============================================================
if __name__ == "__main__":
    scenarios = [
        ("S1-基础流程", scenario_1_basic_flow),
        ("S2-无素材", scenario_2_no_assets),
        ("S3-多素材", scenario_3_multiple_refs),
        ("S4-编辑内容", scenario_4_edit_then_proceed),
        ("S5-重选风格", scenario_5_regenerate_style),
        ("S6-回退测试", scenario_6_rollback),
        ("S7-Agent聊天", scenario_7_chat_agent_content),
        ("S8-文档上传", scenario_8_document_upload),
        ("S9-视觉聊天", scenario_9_visual_chat_style),
        ("S10-长内容", scenario_10_long_content),
    ]

    results = []
    for name, fn in scenarios:
        ok = run_scenario(name, fn)
        results.append((name, ok))

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'} {name}")
    print(f"\n总计: {passed}/{len(results)} 通过")
