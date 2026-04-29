import json
import json_repair
import os
import re
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.models.base import get_db
from app.models.models import Project, Slide
from app.core.llm_client import get_llm_client
from app.core.config import settings

router = APIRouter(prefix="/projects", tags=["chat"])


class ChatMessage(BaseModel):
    message: str
    history: list[dict] = []
    page_context: dict | None = None
    agent_role: str = "content"  # "content" | "visual"

    @field_validator("agent_role")
    @classmethod
    def _validate_agent_role(cls, v: str) -> str:
        allowed = {"content", "visual"}
        if v not in allowed:
            raise ValueError(f"agent_role must be one of {allowed}, got '{v}'")
        return v


def _load_project_documents(project_id: str) -> str:
    """读取项目已上传文档的提取文本。"""
    docs_dir = os.path.join(settings.UPLOAD_DIR, project_id, "docs")
    if not os.path.exists(docs_dir):
        return ""

    parts = []
    for filename in sorted(os.listdir(docs_dir)):
        if filename.endswith(".extracted.txt"):
            original_name = filename[:-14]
            path = os.path.join(docs_dir, filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
                if len(text) > 8000:
                    text = text[:8000] + "\n\n[文档内容过长，已截断]"
                parts.append(f"--- 文档: {original_name} ---\n{text}")
            except Exception:
                continue

    return "\n\n".join(parts)


def _build_draft_prompt(has_documents: bool) -> str:
    """draft 阶段（无 slides）的对话收集 prompt。"""
    doc_hint = """
【重要：用户已上传文档素材】
用户已经上传了文档（PDF / Word / PPT / Markdown 等），文档内容已包含在系统上下文中。你必须：
1. 基于这些文档内容来回答，绝对不要要求用户重新发送文档内容。
2. 仔细阅读文档，提取核心主题、关键论点、数据。
3. 在追问时引用文档中的具体内容来确认理解。
4. 最终生成时把文档内容作为核心素材融入主题描述。""" if has_documents else ""

    return f"""你是 PPT GOD 的内容总监。你有三重背景：TED演讲教练、麦肯锡咨询顾问、顶尖商业文案。你不是问答机器人，你是在帮用户导演一场演示。

你的任务是通过多轮对话帮用户把 PPT 需求理清楚，然后输出定调摘要，等用户确认后再生成。

{doc_hint}

【场景推断规则】
根据用户输入自动判断场景类型，不要问用户"你要什么类型"：
- 年终总结/述职/业绩报告/公司介绍/讲义/培训 → reading（阅读/汇报型，侧重逻辑清晰、数据突出）
- 产品发布/品牌路演/keynote/演讲 → presentation（演讲驱动型，侧重情绪节奏、钩子、高潮）
- 客户提案/方案/商业计划书 → mixed（混合型）

【工作流 action 说明】
- "diagnose"：首轮对话且信息极少时（如用户只说"帮我做个PPT"），先给出场景诊断和策略建议
- "collect_content"：信息不够，追问1-2个关键问题。但**每次追问时必须附带下一步预告**，比如"回答这两个问题后，我会立即为你生成内容规划定调"。**禁止只问问题不给方向**。
- "propose_plan"：信息已足够，**立即**输出定调摘要，**绝对禁止再追问**。用户看到摘要后，会自己决定是否点击"开始生成"。
- "generate_plan"：当用户明确表达"立即开始生成"的意图时触发。包括但不限于这些表达："直接生成"、"开始生成"、"就这样"、"开始吧"、"生成吧"、"确认生成"、"生成"、"走起"、"开搞"、"开始制作"。**只要用户表达了明确的立即开始意图，即使措辞不在列表中，也应触发 generate_plan。不要在用户只回复"ok"、"好的"、"明白了"时触发 generate_plan。**
- "answer"：用户问无关问题，正常回答

【信息足够判定标准 —— 满足以下任意两项就必须输出 propose_plan，绝对禁止返回 collect_content】
1. 有明确的主题/标题（如"销售训练营"、"年终汇报"）
2. 有明确的场景类型或目标受众（如"内部培训"、"客户提案"、"给老板看"）
3. 有核心内容方向或关键信息点（如"基于刚上传的文档"、"关于AI应用"）
4. 用户已上传文档并明确表达"做成PPT"等制作意图
**当满足两项及以上时，action 必须是 "propose_plan" 或 "generate_plan"，绝对禁止返回 "collect_content"或反问用户。**

【推动原则 —— 绝对禁止停在反问】
1. 当用户已上传文档并明确表达"做成PPT""做一个精美的PPT"等制作意图时，**信息已足够，直接输出 propose_plan**，不要反问"你想做什么"。
2. 当用户给出主题 + 明确场景（如"销售训练营""年终汇报"）时，**信息已足够，直接输出 propose_plan**，不要再问"这是什么场景"。
3. Agent 的每次回复都必须给出**明确的下一步**：要么直接输出定调摘要，要么告诉用户"再确认X和Y两点，我就立即开始生成"。**禁止把决策成本抛给用户**。
4. 如果用户回复了"内部青年销售训练营。我要做成一个精美的 ppt"这类明确指令，你的 action 必须是 "propose_plan"，response 里直接给出定调摘要和结构建议。

【输出 JSON 格式】
{{
  "action": "diagnose" | "collect_content" | "propose_plan" | "generate_plan" | "answer",
  "response": "给用户的友好中文回复，用内容总监的口吻，专业但有温度。不要出现'diagnose'、'propose_plan'等技术词汇。每次回复都必须包含下一步行动指引，不能停在反问。",
  "scene_type": "reading" | "presentation" | "mixed" | null,
  "diagnosis": {{  // 仅在 action="diagnose" 时输出
    "input_type": "raw_document" | "vague_request" | "mature_outline" | "data_report",
    "suggested_strategy": "人话描述策略，如'建议先抛核心数据做钩子，再展开过程'",
    "confidence": 0.8,
    "missing_focus": ["还缺的关键信息"]
  }},
  "positioning": {{  // 仅在 action="propose_plan" 时输出
    "core_thesis": "一句话核心洞察",
    "strategy": "整体结构策略，人话描述",
    "tone": "文案调性，如'克制专业，数据驱动'",
    "estimated_pages": 12,
    "key_highlights": ["亮点1", "亮点2", "亮点3"]
  }},
  "title": "为项目起一个简洁的中文标题，8-15字，能概括主题。不要照搬用户原文，要提炼。如用户说'做一份关于AI在医疗领域应用的PPT'，标题应为'AI医疗应用洞察'",
  "topic": "整理后的完整主题描述。propose_plan 和 generate_plan 时都必须输出，供生成接口使用"
}}

【规则】
- 用户明确说"直接生成"、"不用问了"、"就这样"、"开始吧" → action="generate_plan"
- 用户只给模糊需求（如"帮我做个PPT"）→ 先 action="diagnose" 给出判断和建议，同时指出还缺什么
- 不要问用户"你要什么框架"，直接推断并给出建议
- 每次只追问1-2个问题
- 必须只返回合法JSON，不要markdown代码块，不要任何解释性文字"""


def _build_visual_prompt(content_plan_summary: str, assets_summary: str = "") -> str:
    """视觉总监的 system prompt。"""
    asset_section = f"\n\n【用户已上传的设计素材】\n{assets_summary}\n" if assets_summary else ""

    # 根据是否有素材，调整首次介入的策略
    if assets_summary:
        first_interaction_rule = """- **首次介入时，用户已经上传了设计素材**。你的任务是：
  1. 简要确认收到的素材（如"已收到你的Logo和3张风格参考"）
  2. 询问用户是否还有其他素材需要补充
  3. 如果素材已经足够，直接输出风格提案（action="propose_styles"）
  4. 如果用户想补充素材，等待补充后再提案"""
    else:
        first_interaction_rule = """- **首次介入时，用户还没有上传任何设计素材**。你的首要任务是**引导用户上传设计素材**。回复结构：自我介绍（1句）+ 询问用户是否有以下素材可以上传：参考模板、参考图、Logo、文字风格描述（清晰列出4项）+ 说明上传这些素材如何帮助提案更精准。
- **绝对不能**在首次回复中直接给出配色方案、字体建议、风格判断或完整的视觉分析。你必须先确认用户的素材情况。"""

    return f"""你是 PPT GOD 的视觉总监。你有三重背景：顶尖平面设计师、品牌视觉顾问、演示设计专家。你不是模板推荐机器人，你是在帮客户制定视觉策略。

【绝对规则】你必须且只能输出合法的 JSON 对象。不要输出任何解释性文字、markdown 代码块、HTML 标签或多余的自然语言。无论用户说什么，你的每一次回复都必须是且只能是一个可被直接解析的 JSON 对象。违反此规则会导致系统错误。

你的任务是根据客户的内容规划，为他们制定视觉策略、提案风格方案，并解答视觉相关咨询。

【当前项目内容规划】
{content_plan_summary}{asset_section}

【工作流 action 说明】
- "collect_assets"：用户还没有素材，或素材不够，你需要引导用户上传/描述更多设计素材。**不要直接输出风格提案**。
- "propose_styles"：用户已上传素材，或在聊天中明确表示"没有素材""直接提案吧""你推荐吧""生成风格提案""确认素材"之后，基于已有信息输出风格提案。如果有素材，必须基于素材来阐述风格；如果没有素材，基于内容自行推荐。当用户点击「确认素材已齐，生成风格提案」按钮时，系统会发送一条确认消息，你也必须返回 propose_styles。
- "adjust_style"：用户对已有提案提出调整意见（如"太冷了"、"太花哨"、"更商务一点"），你在 response 中说明调整思路。
- "confirm_style"：用户明确确认选择某个风格（如说"ok"、"就用这个"、"确认"、"选这个"等），返回此 action 并带上完整的风格对象。系统会自动保存该风格并进入下一步。
- "reroll_page_visual_plan"：用户在单页/页面上下文里表达"再来一版"、"这个不满意"、"换个方向"等，希望**重新生成**这一页画面方案（由 LLM 自动重新写）。这个 action 只更新画面描述和生图提示词，不生图。
- "update_slide_visual"：用户给出**明确的、具体的画面修改指令**（如"这一页加入里尔克的头像"、"背景换成深海蓝"、"把参考图放在右侧"），你直接修改该页的 `visual_description` 或 `design_notes`，而不是让 LLM 重新生成一整版。修改后前端会自动更新生图提示词。**单页模式下优先使用此 action，它比 reroll_page_visual_plan 更精准。**
- "update_all_slides_visual"：用户在全局模式下给出影响多页的视觉调整指令（如"所有页面背景都换成深色"、"统一把点缀色改成金色"），你返回多个页面的 visual_json 修改。前端会批量应用并自动更新生图提示词。
- "request_generate_image"：用户表达"可以了"、"生成图片"、"就按这个出图"等希望生图的意图。注意：这会产生成本，你只能返回该 action 让前端提示用户确认，不能直接生图。
- "answer"：用户咨询视觉相关问题，或描述风格偏好（如"要小红书那种温暖生活感的风格"），你正常回答并给出专业建议。
- "forward_to_content"：用户要求修改页面文字内容、重生成内容规划、调整结构等**不属于视觉策略**的需求时，返回此 action，并在 response 中礼貌告知用户"这是内容总监的工作范围，我帮你转接"。

【输出 JSON 格式】
{{
  "action": "collect_assets" | "propose_styles" | "adjust_style" | "confirm_style" | "reroll_page_visual_plan" | "update_slide_visual" | "update_all_slides_visual" | "request_generate_image" | "answer" | "forward_to_content",
  "response": "给用户的友好中文回复，用视觉总监的口吻，专业但有审美品味。",
  "style": {{  // 仅在 action="confirm_style" 时输出，必须包含完整的风格定义
    "name": "风格名称",
    "palette": [
      {{"name": "颜色名称", "hex": "#FF2442", "role": "主色/背景色/标题色/点缀色"}}
    ],
    "mood": "3-5个氛围词",
    "font": "字体建议",
    "description": "风格说明（150-250字）",
    "source": "original"
  }},
  "page_nums": [5],  // 仅在 reroll_page_visual_plan、update_slide_visual、request_generate_image 且能判断页码时输出
  "updated_visual": {{  // 仅在 action="update_slide_visual" 时输出
    "page_num": 2,
    "visual_json": {{
      "visual_description": "修改后的画面描述（只改需要变的部分，保留其他原有内容）",
      "design_notes": "可选的设计备注"
    }}
  }},
  "updated_slides_visual": [  // 仅在 action="update_all_slides_visual" 时输出
    {{ "page_num": 1, "visual_json": {{ "visual_description": "..." }} }},
    {{ "page_num": 3, "visual_json": {{ "visual_description": "..." }} }}
  ],
  "style_proposal": {{  // 仅在 action="propose_styles" 或 action="adjust_style" 时输出
    "name": "风格名称（简洁直观）",
    "palette": [
      {{"name": "颜色名称", "hex": "#FF2442", "role": "主色/背景色/标题色/点缀色"}},
      ...
    ],
    "mood": "3-5个氛围词",
    "font": "字体建议",
    "description": "风格说明（150-250字，具体说人话，不要堆砌形容词）",
    "source": "original"
  }}
}}
- style_proposal 的 palette 必须包含 4 个颜色，每个颜色必须有 name、hex（6位大写）、role
- style_proposal 的 description 要和 response 里的风格描述保持一致

【规则】
{first_interaction_rule}
- 只有用户明确说"没有素材"、"直接提案吧"、"你推荐吧"，才能进入 propose_styles。
- **当用户明确确认选择某个风格时（如"ok"、"就用这个"、"确认"、"选这个"），必须返回 action="confirm_style"，并在 `style` 字段中输出完整的风格对象。不要只返回 "answer"。**
- 说话要有设计师的品味，但不要说空话套话。具体、有观点。
- 如果用户提到颜色、字体、排版、风格，给出专业建议。
- 如果用户在单页里给出**具体的画面修改指令**（如"加入里尔克的头像"、"背景换成深蓝"、"人物放左边"、"参考图更突出"），返回 action="update_slide_visual"，并在 `updated_visual` 中给出修改后的 `visual_description`。这是**首选 action**，比 reroll_page_visual_plan 更精准。
- 如果用户在单页里说"再来一版""不满意""换个方向"等模糊的重做需求，返回 action="reroll_page_visual_plan"。
- 如果用户在全局模式下说"所有页面都..."、"统一改成..."等影响多页的指令，返回 action="update_all_slides_visual"，在 `updated_slides_visual` 数组中给出每页的修改。
- 如果用户说"可以了，生成图片""确认生图""就按这个出图"，返回 action="request_generate_image"，并提醒用户需要在页面中确认，避免误产生生图成本。
- 你绝不能直接触发真实生图。任何会产生成本的动作，都必须让用户在 UI 中确认。
- **当用户描述某个品牌/平台的风格偏好时（如"要小红书那种风格"、"想要温暖生活感的调性"），使用 action="answer"，在 response 中确认理解该风格特征，并给出基于此风格的专业建议。不要返回空内容。**
- **【关键】每次回复的末尾，必须根据当前素材状态，明确告诉用户下一步可以点击什么按钮。格式：另起一行写 "👉 下一步：..."**
  - 如果用户已上传素材或描述了风格，但还没生成提案：👉 下一步：点击「确认素材已齐，生成风格提案」按钮，我立即开始
  - 如果用户素材明显不够（只有文字描述，没有图）：👉 下一步：你可以继续上传参考图或 Logo，补完后点击「确认素材已齐，生成风格提案」
  - 如果风格提案已生成，等待用户选择：👉 下一步：请在主舞台选择一套风格方案，或在聊天中直接告诉我你的选择
  - 如果用户在聊天中直接确认风格（如说"ok"、"选这个"）：返回 action="confirm_style"，👉 下一步：系统会自动保存并进入画面设计阶段，无需额外操作
  - 如果用户已选风格，还没生成生图方案：👉 下一步：点击「确认风格，生成生图方案」按钮，开始为每一页生成画面描述
  - 如果是纯咨询问题：👉 下一步：如果还有其他视觉问题随时问我，或者点击按钮继续推进
- 必须只返回合法JSON，不要markdown代码块，不要任何解释性文字"""


def _build_normal_prompt() -> str:
    """有 slides 后的内容执行阶段 prompt（内容总监）。"""
    return """你是 PPT GOD 的内容总监。你有三重背景：TED演讲教练、麦肯锡咨询顾问、顶尖商业文案。用户已经进入了内容执行阶段，你的任务是根据用户指令执行内容操作或给出专业建议。解析用户意图并返回 JSON：
- "action": "regenerate_pages" | "retry_failed" | "update_style" | "update_slide_content" | "update_all_slides" | "regenerate_plan" | "add_slide_before" | "add_slide_after" | "answer"
- "page_nums": int[]（regenerate_pages 时提取页码）
- "style_id": string（update_style 时）
- "updated_content": object（update_slide_content 时，返回该页完整的 content_json，必须包含 page_num、type、section_title、text_content、speaker_notes、visual_suggestion）
- "updated_slides": object[]（update_all_slides 时，数组中每个元素只需包含 page_num 和 text_content）
- "new_slide": object（add_slide_before / add_slide_after 时，返回新页的完整 content_json，必须包含 page_num、type、section_title、text_content、speaker_notes、visual_suggestion）
- "topic": string（regenerate_plan 时必须输出，完整的主题描述用于重新生成内容规划）
- "page_count": number（regenerate_plan 时可选，用户明确要求多少页就输出多少页，未提及则不输出）
- "response": string（给用户的中文回复）

规则：
- "重新生成第X页" / "重做第X页" → action="regenerate_pages"
- "重试失败" / "重新生成失败的页" → action="retry_failed"
- 用户明确要求修改某一页 → action="update_slide_content"
- 用户要求修改全部页面、全局调整、整体改写文字 → action="update_all_slides"
- **用户提到"按照 content plan"、"按照原文/文档"、"完全按照...来"、"按原来的大纲"等，意图是让现有页面内容对齐文档/大纲时 → action="update_all_slides"，不要只口头答应**
- **用户要求"重新生成内容规划"、"重新规划页面"、"按大纲重新来"、页数需要增减变化时 → action="regenerate_plan"，并在 topic 字段中输出完整的主题描述（用于重新生成内容规划）**
- 用户说"在第X页前面加一页"、"在前面插入一页"、"加一页" → action="add_slide_before"
- 用户说"在第X页后面加一页"、"在后面插入一页"、"追加一页" → action="add_slide_after"
- 其他 → action="answer"
- 如果用户提到"文档""原文""MD""文件"里的内容，请基于已上传的文档内容回答，不要反问用户。
- update_slide_content 时：
  1. 必须在 updated_content 中返回该页完整的 content_json（包含所有字段）
  2. 只修改用户明确要求改的部分，其他字段保持原样
  3. 同步在 response 中简要说明改了什么
- update_all_slides 时：
  1. 在 updated_slides 数组中返回需要修改的页面，每个元素格式：{"page_num": N, "text_content": {"headline":"...","subhead":"...","body":"markdown正文..."}}
  2. 只返回确实需要改的页面，无需修改的页面不要出现在数组中
  3. body 是 markdown 格式的字符串，不是数组
- add_slide_before / add_slide_after 时：
  1. 必须在 new_slide 中返回新页完整的 content_json（包含所有字段）
  2. page_num 填用户指定的目标位置页码；如果用户没有明确指定，填当前上下文中的 page_num
  3. type 根据内容推断（cover/toc/content/data/hero/ending），默认 content
  4. 如果用户没有提供具体内容，生成与上下文风格一致、自然过渡的页面内容
- 只返回 JSON，不要 markdown。
- 【重要】JSON 字符串值中如果包含双引号 "，必须转义为 \"。建议避免在字符串中使用双引号，可用中文引号「」或单引号代替。

示例输出（必须严格遵循此格式）：
{"action": "regenerate_pages", "page_nums": [3, 4], "response": "好的，正在重新生成第3页和第4页。"}
{"action": "update_slide_content", "updated_content": {"page_num":1,"type":"cover","section_title":"","text_content":{"headline":"新标题","subhead":"新副标题","body":""},"speaker_notes":"","visual_suggestion":""}, "response": "已更新封面标题和副标题。"}
{"action": "update_all_slides", "updated_slides": [{"page_num":1,"text_content":{"headline":"...","subhead":"...","body":"markdown正文..."}},{"page_num":2,"text_content":{"headline":"...","subhead":"...","body":"markdown正文..."}}], "response": "已根据原文调整所有页面。"}
{"action": "add_slide_after", "new_slide": {"page_num":3,"type":"content","section_title":"","text_content":{"headline":"新标题","subhead":"新副标题","body":""},"speaker_notes":"","visual_suggestion":""}, "response": "已在第3页后插入新页。"}

- 必须只返回合法 JSON，不要 markdown 代码块，不要任何解释性文字。确保 JSON 可以被直接解析。"""


def _stream_intent(user_message: str, project_context: dict, history: list[dict], documents: str = "", page_context: dict | None = None, agent_role: str = "content", content_plan_summary: str = "", assets_summary: str = ""):
    """流式解析用户意图，yield SSE 事件。"""
    import logging
    logger = logging.getLogger(__name__)

    client = get_llm_client()

    # draft 阶段：没有 slides 时（无论 status 是 draft 还是 planning），都视为内容收集阶段
    is_draft = project_context["total_slides"] == 0
    has_documents = bool(documents and documents.strip())
    logger.info(f"Chat stream: project={project_context['title']}, role={agent_role}, is_draft={is_draft}, has_documents={has_documents}, doc_len={len(documents) if documents else 0}")

    # 根据 agent_role 选择 system prompt
    if agent_role == "visual":
        system_prompt = _build_visual_prompt(content_plan_summary, assets_summary)
    elif agent_role == "content":
        if is_draft:
            system_prompt = _build_draft_prompt(has_documents)
        else:
            system_prompt = _build_normal_prompt()
    else:
        # 兜底：未指定角色时按有无 slides 判断
        if is_draft:
            system_prompt = _build_draft_prompt(has_documents)
        else:
            system_prompt = _build_normal_prompt()

    # 把文档内容放到系统 prompt 中（内容总监和视觉总监都需要）
    if has_documents and agent_role != "visual":
        system_prompt += f"\n\n=== 用户已上传的文档内容（你必须基于这些文档回答） ===\n{documents}\n=== 文档结束 ==="

    # 把页面上下文放到 system prompt 中
    if page_context:
        try:
            if isinstance(page_context, dict) and page_context.get("mode") == "global":
                slides_summary = page_context.get("slides", [])
                system_prompt += "\n\n【当前处于全局调整模式 —— 用户指令可能影响多个页面】"
                system_prompt += f"\n所有页面摘要：\n{json.dumps(slides_summary, ensure_ascii=False, indent=2)}"
            elif isinstance(page_context, dict) and page_context.get("mode") == "page":
                current_page = page_context.get("current_page", {})
                other_pages = page_context.get("other_pages", [])
                system_prompt += f"\n\n【当前处于单页编辑模式 —— 你只能修改第 {current_page.get('page_num')} 页，不得影响其他页面】"
                system_prompt += f"\n\n=== 当前正在编辑的页面（修改目标） ===\n{json.dumps(current_page, ensure_ascii=False, indent=2)}"
                if other_pages:
                    system_prompt += f"\n\n=== 其他页面摘要（仅作风格/格式参考，禁止修改） ===\n{json.dumps(other_pages, ensure_ascii=False, indent=2)}"
            else:
                page_json = json.dumps(page_context, ensure_ascii=False, indent=2)
                system_prompt += f"\n\n=== 当前正在编辑的单页上下文 ===\n{page_json}\n=== 单页上下文结束 ==="
        except Exception:
            pass

    context = f"项目：{project_context['title']}，状态：{project_context['status']}，共 {project_context['total_slides']} 页，已完成 {project_context['completed_slides']} 页"

    # 把 history 中的 system 操作日志合并到 system prompt 中
    # MiniMax API 不支持 messages 中出现多条 system 角色，所以必须合并
    system_log_parts = []
    for h in history:
        if h.get("role") == "system":
            system_log_parts.append(h.get("content", ""))
    if system_log_parts:
        system_prompt += "\n\n【用户在主舞台的操作日志】\n" + "\n".join(f"- {p}" for p in system_log_parts)
        system_prompt += "\n\n你必须基于上述日志理解用户当前的项目进展和状态，给出精准的建议。不要反问用户\"你做了什么\"，因为日志里已经有了。"

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for h in history:
        role = h.get("role")
        if role == "system":
            continue  # 已合并到 system_prompt，不再重复传入
        if role not in ("user", "assistant"):
            role = "assistant"
        messages.append({"role": role, "content": h.get("content", "")})

    user_content = f"上下文：{context}\n用户：{user_message}"
    messages.append({"role": "user", "content": user_content})

    stream = client.chat.completions.create(
        model=settings.MINIMAX_LLM_MODEL,
        messages=messages,
        temperature=0.5 if is_draft and agent_role != "visual" else 0.4 if agent_role == "visual" else 0.1,
        stream=True,
    )

    buffer = ""
    in_think = False
    content_buffer = ""
    full_buffer = ""
    chunk_count = 0

    logger.info(f"Chat stream: starting LLM stream, messages_count={len(messages)}")
    try:
        for chunk in stream:
            if not chunk.choices:
                continue
            delta_obj = chunk.choices[0].delta
            if not delta_obj:
                continue
            delta = delta_obj.content or ""
            buffer += delta
            full_buffer += delta
            chunk_count += 1

            while buffer:
                if not in_think:
                    idx = buffer.find("<think>")
                    if idx == -1:
                        if buffer:
                            yield {"type": "content", "delta": buffer}
                            content_buffer += buffer
                        buffer = ""
                        break
                    else:
                        if idx > 0:
                            yield {"type": "content", "delta": buffer[:idx]}
                            content_buffer += buffer[:idx]
                        buffer = buffer[idx + 7:]
                        in_think = True
                else:
                    idx = buffer.find("</think>")
                    if idx == -1:
                        if buffer:
                            yield {"type": "thinking", "delta": buffer}
                        buffer = ""
                        break
                    else:
                        if idx > 0:
                            yield {"type": "thinking", "delta": buffer[:idx]}
                        buffer = buffer[idx + 8:]
                        in_think = False
    except Exception as stream_exc:
        logger.error(f"Chat stream: LLM stream exception: {stream_exc}", exc_info=True)
        raise

    logger.info(f"Chat stream: LLM stream finished, chunks={chunk_count}, content_len={len(content_buffer)}, full_len={len(full_buffer)}")

    def _try_parse(text: str):
        text = text.strip()
        # 去掉 think 标签（可能被截断或不完整）
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.startswith("json"):
                text = text[4:].strip()

        # 1. 优先用 json_repair 自动修复 LLM 常见的 JSON 错误
        try:
            parsed = json_repair.loads(text)
            # 纯自然语言无 JSON 时 json_repair 常返回 ''，不能当作合法结果（否则会绕过上层 None 兜底）
            if parsed == "":
                pass
            elif isinstance(parsed, list):
                # LLM 偶尔输出 JSON 数组而非对象，拒绝并继续尝试提取对象
                pass
            else:
                return parsed
        except Exception:
            pass

        # 2. 提取第一个 JSON 对象/数组后再次尝试 json_repair
        start_obj = text.find("{")
        start_arr = text.find("[")
        start = start_obj if start_obj != -1 and (start_arr == -1 or start_obj < start_arr) else start_arr
        if start != -1:
            end = text.rfind("}") if text[start] == "{" else text.rfind("]")
            if end != -1 and end > start:
                snippet = text[start:end + 1]
                try:
                    snip_parsed = json_repair.loads(snippet)
                    if snip_parsed == "":
                        pass
                    else:
                        return snip_parsed
                except Exception:
                    pass

        # 兜底：记录解析失败信息以便排查
        preview = text[:200].replace("\n", " ")
        logger.warning(f"[Chat] JSON parse failed after all fixes. Preview: {preview!r}")
        return None

    def _escape_newlines_in_json(text: str) -> str:
        """修复 JSON 字符串中未转义的换行符。"""
        result = []
        in_string = False
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == '"' and (i == 0 or text[i - 1] != '\\'):
                in_string = not in_string
                result.append(ch)
            elif in_string and ch in ('\n', '\r'):
                result.append('\\n')
                if ch == '\r' and i + 1 < len(text) and text[i + 1] == '\n':
                    i += 1  # 跳过 \n
            else:
                result.append(ch)
            i += 1
        return ''.join(result)

    def _escape_quotes_in_json(text: str) -> str:
        """修复 JSON 字符串中未转义的双引号。

        LLM 经常在字符串值里写 增加了"爱"这一维度 但不转义，导致整个 JSON 非法。
        启发式判断：字符串内遇到的未转义 "，如果后面不是紧跟逗号/冒号/}]/空白，就认为是内部引号，需要转义。
        """
        result = []
        in_string = False
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == '"' and (i == 0 or text[i - 1] != '\\'):
                if not in_string:
                    in_string = True
                    result.append(ch)
                else:
                    # 字符串内的未转义引号——判断是字符串结束还是内部引号
                    j = i + 1
                    while j < len(text) and text[j] in ' \t\n\r':
                        j += 1
                    if j < len(text) and text[j] in ',:}]':
                        in_string = False
                        result.append(ch)
                    else:
                        result.append('\\"')
            else:
                result.append(ch)
            i += 1
        return ''.join(result)

    # 依次尝试解析 content_buffer、full_buffer
    result = _try_parse(content_buffer) or _try_parse(full_buffer)

    if result is None:
        # 尝试从 full_buffer 中提取自然语言回复作为兜底
        clean = full_buffer.strip()
        # 去掉 think 标签内容
        clean = re.sub(r"<think>.*?</think>", "", clean, flags=re.DOTALL).strip()
        # 去掉 markdown 代码块
        clean = re.sub(r"```[\s\S]*?```", "", clean).strip()
        # 去掉首尾引号
        clean = clean.strip('"').strip()

        # 如果清理后仍然像 JSON，强制尝试解析（有时 LLM 把 JSON 包在引号里）
        if clean and (clean.startswith("{") or clean.startswith("[")):
            forced = _try_parse(clean)
            if forced and isinstance(forced, dict) and "action" in forced:
                result = forced

        if result is None:
            # JSON 解析失败时，根据用户消息意图兜底，不要默认反问
            user_msg_lower = user_message.lower()
            force_generate = any(k in user_msg_lower for k in ["直接生成", "开始生成", "就这样", "开始吧", "生成吧", "确认生成", "生成", "走起", "开搞", "开始制作"])
            force_propose = any(k in user_msg_lower for k in ["做个ppt", "做一个", "帮我做", "生成ppt", "做成ppt"])

            if clean and len(clean) > 5:
                if agent_role == "visual":
                    result = {"action": "answer", "response": clean}
                else:
                    if is_draft and force_generate:
                        result = {"action": "generate_plan", "response": clean}
                    elif is_draft and force_propose:
                        result = {"action": "propose_plan", "response": clean}
                    else:
                        result = {"action": "answer" if not is_draft else "collect_content", "response": clean}
            else:
                if agent_role == "visual":
                    result = {"action": "answer", "response": "抱歉，我没太理解你的视觉需求。你可以直接描述喜欢的风格（如「想要小红书那种温暖、生活感的调性」），或者上传参考图让我更精准地把握方向。"}
                elif is_draft and force_generate:
                    result = {"action": "generate_plan", "response": "好的，我立即为你开始生成内容规划。"}
                elif is_draft and force_propose:
                    result = {"action": "propose_plan", "response": "好的，我基于你的需求输出内容定调摘要。"}
                elif is_draft:
                    result = {"action": "collect_content", "response": "抱歉，我没太听懂，能再详细说说你的需求吗？比如主题是什么、给谁看、核心想传达什么？"}
                else:
                    result = {"action": "answer", "response": "抱歉，我不太理解您的指令，请尝试说\"重新生成第3页\"或\"重试失败的页面\"。"}

    # 角色权限过滤：视觉总监不能返回内容规划相关 action
    if result and isinstance(result, dict):
        if agent_role == "visual":
            allowed_actions = {
                "collect_assets",
                "propose_styles",
                "adjust_style",
                "confirm_style",
                "reroll_page_visual_plan",
                "update_slide_visual",
                "update_all_slides_visual",
                "request_generate_image",
                "forward_to_content",
                "answer",
            }
            if result.get("action") not in allowed_actions:
                result["action"] = "answer"
        elif agent_role == "content":
            allowed_actions = {
                "regenerate_pages",
                "retry_failed",
                "update_style",
                "update_slide_content",
                "update_all_slides",
                "regenerate_plan",
                "add_slide_before",
                "add_slide_after",
                "answer",
            }
            if result.get("action") not in allowed_actions:
                result["action"] = "answer"
    elif result and not isinstance(result, dict):
        # 解析结果不是合法对象，强制兜底
        if agent_role == "visual":
            result = {"action": "answer", "response": "抱歉，我没太理解你的视觉需求。你可以直接描述喜欢的风格（如「想要小红书那种温暖、生活感的调性」），或者上传参考图让我更精准地把握方向。"}
        else:
            result = {"action": "answer", "response": "抱歉，我不太理解您的指令，请尝试说\"重新生成第3页\"或\"重试失败的页面\"。"}

    # 视觉总监 fallback：如果 JSON 解析失败导致返回了 answer，但用户明显表达了具体修改意图，
    # 再发一次低 temperature 请求强制输出 JSON，避免用户指令被忽略。
    if result and result.get("action") == "answer" and agent_role == "visual":
        modification_keywords = ["加入", "添加", "换成", "改成", "修改", "调整", "放", "移", "删", "加", "换", "改", "去掉", "增加", "放大", "缩小"]
        user_msg_lower = user_message.lower()
        has_modification_intent = any(k in user_msg_lower for k in modification_keywords)
        response_text = (result.get("response", "") or "").lower()
        has_confirmation = any(k in response_text for k in ["好的", "已为你", "已经", "加入", "修改", "调整", "换成", "添加"])
        if has_modification_intent and has_confirmation:
            logger.info("[Chat] Visual director fallback triggered for modification intent")
            try:
                fallback_messages = list(messages)
                fallback_messages.append({"role": "assistant", "content": result.get("response", "")})
                fallback_messages.append({
                    "role": "user",
                    "content": (
                        "请把你刚才的回复转换成合法的 JSON 格式。根据用户指令，action 必须是 update_slide_visual（单页修改）或 update_all_slides_visual（全局修改）。"
                        "updated_visual 或 updated_slides_visual 中必须包含修改后的 visual_description。"
                        "只输出 JSON，不要任何解释文字。"
                    ),
                })
                fallback_response = client.chat.completions.create(
                    model=settings.MINIMAX_LLM_MODEL,
                    messages=fallback_messages,
                    temperature=0.1,
                )
                fallback_text = (fallback_response.choices[0].message.content or "").strip()
                fallback_text = re.sub(r"<think>.*?</think>", "", fallback_text, flags=re.DOTALL).strip()
                fallback_text = re.sub(r"^```(?:json)?\s*|```$", "", fallback_text, flags=re.MULTILINE | re.IGNORECASE).strip()
                if fallback_text:
                    fallback_parsed = json_repair.loads(fallback_text)
                    if isinstance(fallback_parsed, dict) and fallback_parsed.get("action") in allowed_actions:
                        result = fallback_parsed
                        logger.info(f"[Chat] Visual director fallback success, action={result.get('action')}")
            except Exception as e:
                logger.warning(f"[Chat] Visual director fallback failed: {e}")

    logger.info(f"Chat stream: yielding result, action={result.get('action') if isinstance(result, dict) else 'n/a'}, content_len={len(result.get('response', '')) if isinstance(result, dict) else 0}")
    yield {"type": "result", "data": result}


@router.post("/{project_id}/chat")
def chat_with_agent(project_id: str, body: ChatMessage, db: Session = Depends(get_db)):
    """Agent 聊天接口：流式返回思考过程和最终结果。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slides = db.query(Slide).filter(Slide.project_id == project_id).order_by(Slide.page_num).all()
    completed = sum(1 for s in slides if s.status == "completed")

    context = {
        "title": project.title,
        "status": project.status,
        "total_slides": len(slides),
        "completed_slides": completed,
    }

    documents = _load_project_documents(project_id)

    # 为视觉总监构建内容规划摘要
    content_plan_summary = ""
    if body.agent_role == "visual" and slides:
        summary_parts = []
        summary_parts.append(f"项目主题：{project.title}")
        summary_parts.append(f"共 {len(slides)} 页，场景类型：{project.status}")
        summary_parts.append("页面结构：")
        for s in slides[:20]:
            tc = s.content_json.get("text_content", {}) if s.content_json else {}
            headline = tc.get("headline", "") if isinstance(tc, dict) else ""
            ptype = s.type or "content"
            summary_parts.append(f"  第{s.page_num}页（{ptype}）：{headline}")
        content_plan_summary = "\n".join(summary_parts)

    # 构建素材摘要（视觉总监使用）
    assets_summary = ""
    if body.agent_role == "visual" and project.reference_images:
        asset_counts: dict[str, int] = {}
        has_template = False
        for ref in project.reference_images:
            if ref.role == "template":
                has_template = True
            else:
                asset_counts[ref.role] = asset_counts.get(ref.role, 0) + 1
        parts = []
        if asset_counts.get("logo", 0):
            parts.append(f"- Logo：{asset_counts['logo']} 张")
        if asset_counts.get("style_ref", 0):
            parts.append(f"- 风格参考图：{asset_counts['style_ref']} 张")
        if has_template:
            parts.append("- 参考模板：已上传（含封面/目录/内容/封底种子页）")
        if asset_counts.get("content_ref", 0):
            parts.append(f"- 内容配图：{asset_counts['content_ref']} 张（页面级）")
        if parts:
            assets_summary = "\n".join(parts)

    import logging as _logging
    _logger = _logging.getLogger(__name__)

    def event_stream():
        _logger.info(f"Chat API: starting stream for project={project_id}, role={body.agent_role}")
        try:
            for event in _stream_intent(
                body.message, context, body.history, documents,
                body.page_context, body.agent_role, content_plan_summary, assets_summary
            ):
                line = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") == "result":
                    _logger.info(f"Chat API: yielding result action={event.get('data', {}).get('action') if isinstance(event.get('data'), dict) else 'n/a'}")
                yield line
        except Exception as e:
            _logger.error(f"Chat API: stream exception: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        _logger.info(f"Chat API: stream ended for project={project_id}")

    return StreamingResponse(event_stream(), media_type="text/event-stream")
