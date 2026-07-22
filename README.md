<div align="center">

<h1>
  <img src="./docs/readme-assets/ppt-god-logo.png" alt="PPT God — 古希腊掌管 PPT 的神" width="340" />
</h1>

### 让 AI PPT 从“能生成”，走向“敢交付”

把主题、文档、逐字稿、Logo、产品图和参考图，变成一套风格一致、内容可控、品牌准确、可以继续修改的 PPT。

</div>

![PPT God：封面、数据与场景页面保持跨页一致](./docs/readme-assets/hero-consistency.jpg)

<p align="center"><sub>首屏中的 Apple Vision Pro 为公开资料 Demo；产品界面经过脱敏，展示内容均可公开。</sub></p>

> [!IMPORTANT]
> **PPT God 是本地开源的 BYOK 工作流，不是自带模型额度的在线服务。** 不需要注册或登录；没有 API Key 也可以先进入工作台。独立完成全流程时通常需要文本模型和图片模型；如果外部 Agent 已经生成并导入了对应成果，就不需要重复配置那一项。

## 3 分钟启动

先安装并打开 [Docker Desktop](https://www.docker.com/products/docker-desktop/)，然后选择一种启动方式。

### macOS：双击启动（推荐）

```bash
git clone https://github.com/JoeSangAI/PPT-GOD.git
cd PPT-GOD
```

在项目文件夹里双击 **`打开 PPT GOD.command`**。它会自动检查 Docker、同步最新代码、启动服务，并主动打开浏览器。以后再次使用，仍然双击同一个文件即可。

### Windows / Linux / 终端启动

```bash
docker compose up --build -d
```

打开 `http://localhost:8000`。启动页不是登录页：可以直接进入工作台，也会显示“文本生成”和“图片生成”是否就绪，并告诉你缺少的能力会影响哪一步。

- 想省事：可以从 [CometAPI 模型大厅](https://www.cometapi.com/pricing/) 选择文本和图片模型，同一枚 Key 可以用于两项。
- 想自由组合：也可以使用任意兼容 OpenAI Chat Completions / Images 的服务和模型。
- 人在终端使用：先运行 `python scripts/pptgod_cli.py doctor`，它会用人话说明当前缺什么。
- 由 Agent 使用：运行 `python scripts/pptgod_cli.py doctor --json`，只返回稳定的机器可读结果。

完整说明见 [从启动、BYOK 到 Agent 接入](./docs/getting-started.md)。

> [!CAUTION]
> **不要把真实 API Key 写进 README、Issue、截图或提交到 Git。** 网页中填写的 Key 只保存在当前浏览器，运行任务时才交给本机服务；仓库中的 `backend/.env.example` 只有空白占位符。换浏览器或换地址后如果显示“未配置”，请在该浏览器的“模型设置”中重新填写，不要把 Key 写进项目文件。

## 四个关键能力，直接看结果

你可以直接看到一套 PPT 是否跨页一致，品牌素材是否准确，风格是否可选，以及长内容如何先形成结构。

### 品牌素材：原 Logo、原产品图，准确进入最终页面

![Apple Vision Pro：原始 Logo 与产品图准确进入最终 PPT](./docs/readme-assets/apple-brand-proof.jpg?v=20260719-single-message)

### 风格选择：同一份内容，也可以先看两种方向

![绵棠手礼：同一份内容的两种风格方向](./docs/readme-assets/style-choice-comparison.jpg)

### 课程课件：长内容先建结构，再形成节奏

![古希腊神话：长内容形成有章节结构与讲述节奏的成套课件](./docs/readme-assets/course-deck-grid-showcase.jpg)

## 你真正买到的，是四种确定性

| 你需要的确定性 | PPT God 如何做到 |
| --- | --- |
| **成套** | 封面、数据、产品和结束页各有变化，但始终属于同一视觉系统 |
| **可控** | 生成前确认每页内容、页面角色和视觉方向，生成后可继续改单页或整套 |
| **准确** | Logo、图表、UI 和产品素材作为事实依据，不让 AI 随意重画 |
| **可交付** | 最终导出可以继续检查、修改和交付的 PPTX，而不是一组灵感图 |

**成套，不是拼图 · 可控，不是盲盒 · 准确，不是重画 · 可交付，不是只供欣赏**

## 从材料到可交付 PPTX，只做五个关键决定

1. **输入材料**：主题、Markdown、文档、逐字稿、旧 PPT、PDF 都可以成为起点。
2. **确认每页内容**：先看标题、正文重点、页面角色和讲述顺序。
3. **选择视觉方向**：比较不同风格提案，确定这一套 PPT 的视觉系统。
4. **决定素材如何使用**：Logo、图表、产品、人物和氛围图采用不同处理方式。
5. **生成、微调并导出**：逐页检查，继续修改，最后导出可编辑 PPTX。

## Logo、图表与产品图，不能用同一种方式处理

| 素材处理方式 | 适合什么 | 结果标准 |
| --- | --- | --- |
| **精确粘贴** | Logo、二维码、图表、UI 截图和原始证据 | 保持事实不变，并在落版时自动避让文字 |
| **智能融合** | 场景、人物和氛围图 | 进入整体构图、光影与视觉风格 |
| **精修融合** | 复杂产品与高还原要求的客户素材 | 先融入画面，再校准主体边缘、比例和关键细节 |

## 适合哪些工作

| 使用场景 | 你交给 PPT God 什么 | 最终解决什么问题 |
| --- | --- | --- |
| 品牌 / 产品发布 | Logo、产品图、发布内容 | 品牌准确，产品可信，整套像发布会 |
| 商务 / 研究汇报 | 报告、数据、方案材料 | 内容先理清，重点更适合演示 |
| 课程 / 知识分享 | 长文档、逐字稿、知识资料 | 从机械拆页变成有讲述节奏的课件 |
| 创意 / 生活方式 | 想法、参考图、审美方向 | 先比较风格，再生成完整视觉系统 |

## 每个案例，都会让下一次默认更稳

真实案例不只是作品，也是 PPT God 的训练场。每一次交付中的反馈，都会先脱敏为回归案例，再沉淀成可复用的能力与质量检查。

**真实反馈 → 脱敏回归 → 能力升级 → 下一次默认更稳**

## 当前状态与开始使用

PPT God 正在持续迭代，目前以本地开源版使用，不提供官方在线托管或内置模型额度。

- 想持续关注：可以 Star 或 Watch 本仓库。
- 想反馈问题或提出需求：欢迎通过 [Issues](https://github.com/JoeSangAI/PPT-GOD/issues) 留言。
- 已经运行本地整合版：默认从 `http://localhost:8000` 进入产品界面。

<details>
<summary><strong>Agent / CLI 接入</strong></summary>

外部 Agent 第一次调用时，必须先检查本地服务和模型能力：

```bash
python scripts/pptgod_cli.py doctor
python scripts/pptgod_cli.py doctor --json
python scripts/pptgod_cli.py capabilities
python scripts/pptgod_cli.py whoami
python scripts/pptgod_cli.py list-projects
```

`doctor` 会区分文本生成与图片生成，并明确说明每一项由 BYOK 提供、由 Agent 提供，还是仍然缺失。Agent 不应仅凭“运行在 Codex / WorkBuddy / Claude Code 中”就假定模型能力已经具备。

Agent 自带生图能力时，可以把 16:9 最终页面图直接交回项目，不需要再配置 PPT God 的图片模型：

```bash
python scripts/pptgod_cli.py import-slide-image <project_id> <page_num> path/to/slide.png
```

Agent 负责内容和视觉文本、但最终页面仍由 PPT God 的图片模型生成时，可以继续导入每页画面描述和生图 Prompt：

```bash
python scripts/pptgod_cli.py import-visual-plan <project_id> path/to/visual-plan.json
```

更新已有项目的内容规划时，默认只预览差异；确认后再应用：

```bash
python scripts/pptgod_cli.py update-content-plan <project_id> path/to/plan.md
python scripts/pptgod_cli.py update-content-plan <project_id> path/to/plan.md --apply --open
```

完整格式和工作流见 [上手指南](./docs/getting-started.md) 与 [Agent 内容规划说明](./docs/agent/content-planning-playbook.md)。

</details>

---

Apple、Apple Vision Pro 及相关标识归其权利人所有。示例资料来源：[Apple Vision Pro 技术规格](https://www.apple.com/apple-vision-pro/specs/)、[Apple Vision Pro 企业应用](https://www.apple.com/newsroom/2024/04/apple-vision-pro-brings-a-new-era-of-spatial-computing-to-business/)。示例仅用于展示 PPT God 的排版与素材控制能力，不代表 Apple 合作或背书。
