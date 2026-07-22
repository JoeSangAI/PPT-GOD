# PPT God 上手指南

PPT God 是本地开源工作流，不是自带模型额度的在线服务。它负责项目、内容确认、视觉确认、页面生成和 PPTX 导出；真正的文本生成与图片生成能力，可以来自你自己的模型 API，也可以由外部 Agent 提供对应成果。

## 先记住一件事

没有 API Key 也可以打开 PPT God、创建项目和查看工作台。系统只会在真正需要某项模型能力时提醒你补充，不会把模型配置当作登录条件。

## 1. 启动

先安装并打开 [Docker Desktop](https://www.docker.com/products/docker-desktop/)。

macOS 推荐直接双击项目根目录里的 **`打开 PPT GOD.command`**。它会检查运行环境、在代码更新后自动重新构建，并主动打开 `http://localhost:8000`。

Windows、Linux，或希望从终端启动时运行：

```bash
git clone https://github.com/JoeSangAI/PPT-GOD.git
cd PPT-GOD
docker compose up --build -d
```

浏览器打开 `http://localhost:8000`。

## 2. 看懂两项能力

| 能力 | PPT God 用它做什么 | 可以从哪里来 |
| --- | --- | --- |
| 文本生成 | 内容规划、视觉方向、每页画面描述 | BYOK 文本模型；或由 Agent 导入结构化规划成果 |
| 图片生成 | 整页画面生成、改单页、参考图编辑 | BYOK 生图模型；或由具备生图能力的 Agent 提供最终页面成果 |

独立在网页中完成整条流程时，通常需要同时配置文本模型和图片模型。由 WorkBuddy、Codex、Claude Code 等 Agent 承载工作时，Agent 已经完成并交给 PPT God 的那部分能力不需要重复付费或重复配置。

关键区别是：**Agent 只是打开网页，不等于 Agent 已经提供了模型成果。** 只有它确实生成并导入了规划或页面，才能替代对应的模型能力。

## 3. 配置模型

进入启动页或工作台左下角的“运行设置”，打开“模型设置”。

- 想省事：可以从 [CometAPI 模型大厅](https://www.cometapi.com/pricing/) 选择文本和图片模型，同一枚 Key 可以填入两项。
- 想自由组合：也可以使用不同平台，只要接口与 OpenAI Chat Completions / Images 兼容。
- PPT God 不锁定模型名称。不同模型的结构化输出、图片比例、参考图编辑能力不同，最终效果也会不同。

浏览器中填写的 Key 长期只保存在当前浏览器；任务运行时会临时传给本地 PPT God 服务。不同浏览器、`localhost` 与 `127.0.0.1` 会使用各自独立的浏览器存储，所以请统一从 `http://localhost:8000` 进入。

不要把真实 Key 写进 README、Issue、截图或提交到 Git。仓库里的 `backend/.env.example` 只用于说明可配置项，必须保持空白占位；网页用户直接在“模型设置”中填写即可。

## 4. CLI / CUI 检查

第一次由 Agent 或命令行使用时，先运行：

```bash
python scripts/pptgod_cli.py doctor
```

终端会直接用人话说明：本地服务是否正常、文本生成是否就绪、图片生成是否就绪、每一项缺失会影响什么，以及下一步去哪里配置。

上面这条命令只输出给人看的检查结果，而且即使模型尚未配齐，只要本地服务正常也会正常结束。Agent 需要机器可读结果时使用：

```bash
python scripts/pptgod_cli.py doctor --json
```

自动化流程希望“能力没配齐就立即停止”时，再加 `--strict`。日常新手检查不需要它。

如果当前 Agent 会实际提供相应成果，可以声明：

```bash
python scripts/pptgod_cli.py doctor --agent-text
python scripts/pptgod_cli.py doctor --agent-text --agent-image
```

只有当 Agent 确实会生成并交付对应成果时才应声明；不要因为“正在 Codex 或 WorkBuddy 里运行”就默认两项能力都已具备。外部 Agent 生成最终页面图后，可用下面的命令逐页导入：

```bash
python scripts/pptgod_cli.py import-slide-image <project_id> <page_num> path/to/slide.png
```

导入最终页面图前要先确认内容规划，避免后续内容变化让页面图失效。图片必须是 16:9，至少 800×450。PPT God 会把它作为该页的正式页面成果，并保留被替换的旧版本。

如果 Agent 负责文本模型的全部工作、图片仍交给 PPT God 生成，可以导入每页画面方案和 Prompt：

```json
[
  {
    "page_num": 1,
    "visual_description": "这一页的构图、主次、色调与图文关系",
    "prompt": "可直接交给图片模型的 16:9 整页生成提示词"
  }
]
```

```bash
python scripts/pptgod_cli.py import-visual-plan <project_id> path/to/visual-plan.json
python scripts/pptgod_cli.py generate-slides <project_id> --prototype
```

这样，用户只需要提供图片模型；不必因为 PPT God 后半程还需要视觉文本而重复配置文本模型。

## 常见卡点

### 提示“缺少文本生成能力”

在“模型设置”里补充文本模型 Key、API 地址和模型名称；如果内容和视觉规划由 Agent 生成，让 Agent 依次导入内容规划与画面方案即可。

### 提示“缺少图片生成能力”

在“模型设置”里补充图片模型配置。图片接口需要支持 OpenAI Images；需要参考图或改单页时，还要支持图片编辑。

### Agent 打开项目后提示属于其他账号

本地网页不再要求账号登录。通过 CLI 打开的项目会自动完成当前项目衔接，不需要手动切换用户名。
