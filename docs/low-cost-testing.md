# Low-Cost Testing

PPT God 的普通自动化测试默认不应该触发真实生图。真实生图只用于人工确认的 smoke test，并且要显式设置每轮上限。

## 生图模式

后端通过以下环境变量控制生图成本：

- `IMAGE_GEN_MODE=real`：真实调用生图 API。默认模式，生产使用。
- `IMAGE_GEN_MODE=mock`：返回本地占位图，不调用真实 API。适合自动化测试和 UI 调试。
- `IMAGE_GEN_MODE=cached`：同一个 prompt 命中缓存时复用图片，未命中才调用真实 API。
- `MAX_REAL_IMAGES_PER_RUN=1`：限制当前进程最多真实生成 1 张图。`0` 表示不限制。
- `IMAGE_GEN_CACHE_DIR=.pptgod-data/outputs/image-cache`：cached 模式的默认图片缓存目录。

## 推荐命令

后端离线测试，不触发真实生图：

```bash
cd backend
IMAGE_GEN_MODE=mock python -m pytest tests/ -q
```

前端构建检查：

```bash
cd frontend
npm run build
```

低成本浏览器交互测试。需要先启动 Vite：

```bash
cd frontend
npm run dev -- --host 127.0.0.1
npm run test:low-cost
```

这条浏览器测试会 mock 后端 API，覆盖打样启动、停止生成、失败页重试、回退确认和聊天错误显示，不调用真实 LLM 或真实生图。

## 一张真实图 Smoke Test

需要人工确认真实生图链路时，再显式开启：

```bash
cd backend
IMAGE_GEN_MODE=real MAX_REAL_IMAGES_PER_RUN=1 python your_smoke_script.py
```

不要把真实生图 smoke test 放进默认 CI 或普通回归测试。
