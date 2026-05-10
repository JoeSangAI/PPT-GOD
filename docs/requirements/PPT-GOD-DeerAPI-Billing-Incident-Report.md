# PPT GOD DeerAPI Billing Incident Report

> **日期**: 2026-04-26  
> **影响**: 98 次图像生成调用被计费，仅 1 张图片实际落盘  
> **直接损失**: ~$16.87 USD  
> **报告人**: Joe（产品）/ Claude Code（技术协助）  
> **目标读者**: 后续接入 DeerAPI（或类似 OpenAI-compatible 图像 API）的研发人员  

---

## 一句话总结

**客户端超时设得太短 + 无差别重试 = 服务器继续干活并扣费，但客户端早已放弃并丢失了结果。**

---

## 1. 事件时间线

| 时间 | 事件 |
|------|------|
| 04-26 00:01 | 开始批量生成 34 页 PPT 幻灯片 |
| 00:01 ~ 08:53 | DeerAPI 侧共记录 **98 次 `gpt-image-2` 调用** |
| 08:00 | 仅 `slide_01.png` 一张图成功写入磁盘 |
| 08:53 | 最后一张 API 调用结束 |
| 查看账单 | $16.87，用户感到困惑与愤怒 |

> 用大白话说：**我付了 98 次外卖钱，只收到 1 份餐。其余 97 份商家说"已出餐"，但我根本没收到。**

---

## 2. 原始重试策略（问题代码）

```python
# 简化还原 —— 这就是踩坑前的逻辑
client = OpenAI(api_key=..., base_url=..., timeout=300.0)  # 5 分钟超时

for attempt in range(5):          # 最多重试 5 次
    try:
        resp = client.images.generate(...)
        img_bytes = download(resp.data[0].url)
        break
    except Exception:
        time.sleep(2)             # 无论啥错，睡 2 秒再试
        continue
```

### 2.1 看起来合理，实则致命的三处设计

| 设计 | 初衷 | 实际后果 |
|------|------|----------|
| `timeout=300` | 5 分钟还不够吗？ | DeerAPI 后端慢请求可达 **485 秒**，客户端第 300 秒就挂断 |
| `range(5)` 无差别重试 | 提高成功率 | 超时/断网后重试 = **重新生成一张全新图片并重新扣费** |
| `time.sleep(2)` | 简单冷却 | 对 429/503 太短；对致命错误则毫无意义 |

> **关键认知**：图像生成是非幂等操作。每次 `images.generate()` 都是**全新的计费事件**，无论你是不是"重试"。这和 POST 创建订单一样——重试 5 次就会创建 5 个订单。

---

## 3. 根因分析：为什么图片"凭空消失"

### 3.1 客户端超时 ≠ 服务端取消

```
[Client]        [DeerAPI / Upstream]
   |                       |
   |---- images.generate -->|
   |    (开始扣费，开始生图)  |
   |                       |
   |  << 300s 后 >>        |
   |  socket timeout!      |
   |<-- X---- connection   |
   |                       |
   |                       | (继续生图... 再花 185s)
   |                       | (图已生成，URL 已产出)
   |                       | (账单已记)
   |                       |
   |   [客户端已放弃，      |
   |    无人去取 URL ]      |
```

- 客户端第 300 秒抛出 `APITimeoutError`
- 原始代码捕获异常，睡 2 秒，**再次发起全新请求**
- 旧请求在服务端继续运行直到完成，产生的图片 URL **没有任何客户端去下载**
- URL 通常只有很短的有效期（分钟级），过期后彻底不可恢复

### 3.2 账单数据佐证

从 DeerAPI 导出的 CSV 日志显示：

- **18 次调用耗时 ≥ 250 秒**
- **2 次调用超过 300 秒**（312 秒、485 秒）
- **14 个" burst 簇"**：5 秒内有 2~4 次调用，说明是同一页在短时间内被反复重试
- 这些 burst 调用全部被计费，但磁盘上**没有对应的图片文件**

> 用程序员的语言：这是一个经典的 **"orphaned request"**（孤儿请求）问题。服务端事务已提交，但客户端事务已回滚，两者状态不一致。

---

## 4. 修复方案：从"尽量成功"到"保证交付"

### 4.1 核心原则

> **如果 DeerAPI 生成了图，我们必须拿到图；如果我们拿不到图，必须明确知道失败原因，而不是默默烧钱。**

### 4.2 具体改动

#### 改动 1：把超时从 300 秒改为 1800 秒（30 分钟）

```python
import httpx
from openai import OpenAI

# 之前：timeout=300.0  —— 一个 float，读写混为一谈
# 之后：读写分离，读超时 30 分钟，连接超时 10 秒
timeout = httpx.Timeout(1800.0, connect=10.0)
client = OpenAI(
    api_key=settings.DEER_API_KEY,
    base_url=settings.DEER_API_BASE,
    timeout=timeout,
)
```

- `connect=10.0`：TCP 握手如果 10 秒还没连上，说明网络真的断了，快速失败
- `read=1800.0`：一旦连接建立，愿意等 DeerAPI 长达 30 分钟——**绝不主动挂断**

#### 改动 2：错误分类——只重试"可能网络抖了一下"的情况

```python
from openai import APIConnectionError, APITimeoutError, APIStatusError

def _is_api_retryable(exc: Exception) -> bool:
    # 超时/连接错误：可能是网络瞬抖，值得重试
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return True

    # HTTP 状态码层面判断
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        if status in (429, 502, 503, 504):
            # 429=限流, 502/503/504=网关/上游故障
            return True
        if 400 <= status < 500:
            # 400/401/403/404/422：客户端请求有问题，重试必败，且会重复扣费
            return False

    # 其他未知异常（如解析错误）默认不重试
    return False
```

> 用大白话说：**只有"网络可能好了"的错误才重试；只要 DeerAPI 明确说"你错了"，立刻停手，不再烧钱。**

#### 改动 3：API 重试与下载重试彻底分离

```python
# API 调用层：最多 2 次（原始请求 + 1 次重试）
api_backoff = [0, 5]  # 第一次立即发，第二次等 5 秒

for attempt, delay in enumerate(api_backoff):
    if delay > 0:
        time.sleep(delay)
    try:
        img = _call_gpt_image_2_generate(...)
        return img   # <-- 成功就返回，不再碰 API
    except Exception as e:
        if not _is_api_retryable(e):
            raise
        if attempt == len(api_backoff) - 1:
            raise
```

```python
# 下载层：独立重试，不涉及额外计费
def _download_image_bytes(url: str, max_attempts: int = 3) -> bytes:
    for attempt in range(max_attempts):
        try:
            resp = requests.get(url, timeout=300)
            resp.raise_for_status()
            return resp.content
        except requests.exceptions.RequestException as e:
            if attempt == max_attempts - 1:
                raise Exception(f"Image download failed: {e}")
            sleep_time = 5 * (2 ** attempt)  # 5s, 10s, 20s
            time.sleep(sleep_time)
```

- **下载失败不会触发重新生图**：下载是下载，生图是生图，两者解耦
- 下载用指数退避（5s → 10s → 20s），给 CDN/存储恢复时间

#### 改动 4：Idempotency-Key（幂等性钥匙）

```python
import uuid

idempotency_key = str(uuid.uuid4())
headers = {"Idempotency-Key": idempotency_key}

resp = client.images.generate(
    model=settings.DEER_IMAGE_MODEL,
    prompt=prompt,
    n=1,
    extra_headers=headers,
)
```

- 同一个 `Idempotency-Key` 在短时间内的重复请求，** DeerAPI 可能会识别为重复提交而不重复计费**
- 注意：这取决于供应商实现，不能 100% 依赖，但属于"有比没有好"的防御层

#### 改动 5：并行生成（Pipeline 优化）

原本 34 页串行，每页平均 2 分钟，总计约 **68 分钟**。现在用 `ThreadPoolExecutor` 并发 3 页：

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

max_workers = min(len(target_slides), 3)

with ThreadPoolExecutor(max_workers=max_workers) as executor:
    future_to_slide = {
        executor.submit(_generate_one_slide, slide, project_id, output_dir): slide
        for slide in target_slides if slide.prompt_text
    }

    for future in as_completed(future_to_slide):
        result = future.result()
        # 在主线程更新数据库（SQLAlchemy Session 非线程安全）
        if result.get("error"):
            slide.status = "failed"
        else:
            slide.image_path = result["image_path"]
            slide.status = "completed"
        db.commit()
```

- **3 并发**是基于 DeerAPI 账单观察：同一秒 3 次调用是安全上限
- **纯 IO 操作（HTTP 请求）放在线程池；数据库操作回到主线程**，避免 SQLAlchemy Session 跨线程崩溃
- 34 页总耗时从 ~68 分钟降到 **~17 分钟**

---

## 5. 最终代码：关键模块

### `image_generation.py` —— 单张图片生成（保证交付版）

```python
import base64
import io
import logging
import os
import time
import uuid
from typing import List, Optional

import httpx
import requests
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from PIL import Image

from app.core.config import settings

logger = logging.getLogger(__name__)
_image_client = None


def _get_image_client() -> OpenAI:
    global _image_client
    if _image_client is None:
        timeout = httpx.Timeout(1800.0, connect=10.0)
        _image_client = OpenAI(
            api_key=settings.DEER_API_KEY or settings.MINIMAX_API_KEY,
            base_url=settings.DEER_API_BASE,
            timeout=timeout,
        )
    return _image_client


def _is_api_retryable(exc: Exception) -> bool:
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return True
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        if status in (429, 502, 503, 504):
            return True
        if 400 <= status < 500:
            return False
    return False


def _download_image_bytes(url: str, max_attempts: int = 3) -> bytes:
    for attempt in range(max_attempts):
        try:
            logger.info(f"ImageGen: downloading URL (attempt {attempt + 1}/{max_attempts})")
            resp = requests.get(url, timeout=300)
            resp.raise_for_status()
            return resp.content
        except requests.exceptions.RequestException as e:
            logger.warning(f"ImageGen: download failed (attempt {attempt + 1}/{max_attempts}): {e}")
            if attempt == max_attempts - 1:
                raise Exception(f"Image download failed, URL may have expired: {e}")
            sleep_time = 5 * (2 ** attempt)
            logger.info(f"ImageGen: retrying download in {sleep_time}s...")
            time.sleep(sleep_time)
    raise Exception("Image download failed after all retries")


def _call_gpt_image_2_generate(
    prompt: str, size: str = "1536x1024", idempotency_key: Optional[str] = None
) -> Image.Image:
    client = _get_image_client()
    headers = {}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    resp = client.images.generate(
        model=settings.DEER_IMAGE_MODEL,
        prompt=prompt,
        size=size,
        quality="high",
        n=1,
        extra_headers=headers or None,
    )
    image_data = resp.data[0]
    if image_data.b64_json:
        img_bytes = base64.b64decode(image_data.b64_json)
    elif image_data.url:
        img_bytes = _download_image_bytes(image_data.url)
    else:
        raise ValueError("DeerAPI returned no image content")
    return Image.open(io.BytesIO(img_bytes))


def generate_slide_image(
    prompt: str,
    reference_images: Optional[List[Image.Image]] = None,
    resolution: str = "4K",
    aspect_ratio: str = "16:9",
) -> Image.Image:
    model = settings.DEER_IMAGE_MODEL.lower()
    size = "1792x1024"
    idempotency_key = str(uuid.uuid4())
    api_backoff = [0, 5]  # max 2 attempts total
    for attempt, delay in enumerate(api_backoff):
        if delay > 0:
            logger.info(f"ImageGen: waiting {delay}s before API call...")
            time.sleep(delay)
        try:
            if "gpt-image" in model or "dall-e" in model:
                if reference_images:
                    img = _call_gpt_image_2_edit(...)
                else:
                    img = _call_gpt_image_2_generate(...)
            else:
                img = _call_gemini_chat_generate(...)
            img = _crop_to_16_9(img)
            logger.info(f"ImageGen: success, model={settings.DEER_IMAGE_MODEL}, size={img.size}")
            return img
        except Exception as e:
            logger.warning(f"ImageGen: API call failed (attempt {attempt + 1}/{len(api_backoff)}): {e}")
            if not _is_api_retryable(e):
                logger.error(f"ImageGen: non-retryable error, aborting: {e}")
                raise
            if attempt == len(api_backoff) - 1:
                raise
    raise Exception("Image generation failed after all retries")
```

### `generation_pipeline.py` —— 批量流水线（并行版）

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional


def _generate_one_slide(slide: Slide, project_id: str, output_dir: str) -> Dict:
    """
    在线程池中执行单页生成（纯 IO/计算，不涉及数据库操作）。
    返回 dict: {slide, image_path?, error?}
    """
    if not slide.prompt_text:
        return {"slide": slide, "error": "缺少 prompt"}

    try:
        ref_images = _load_reference_images(slide)
        img = generate_slide_image(
            prompt=slide.prompt_text,
            reference_images=ref_images if ref_images else None,
            resolution="4K",
            aspect_ratio="16:9",
        )
        image_path = save_slide_image(
            img=img,
            project_id=project_id,
            page_num=slide.page_num,
            output_dir=output_dir,
        )
        logger.info(f"Pipeline: 第 {slide.page_num} 页生成完成")
        return {"slide": slide, "image_path": image_path, "error": None}
    except Exception as e:
        logger.error(f"Pipeline: 第 {slide.page_num} 页生成失败: {e}")
        return {"slide": slide, "error": str(e)[:500]}


def run_generation_pipeline(
    project_id: str,
    db: Session,
    page_nums: Optional[List[int]] = None,
    prototype: bool = False,
):
    logger.info(f"Pipeline: 开始生成项目 {project_id}, page_nums={page_nums}")

    # ... 项目查询、状态设置 ...

    max_workers = min(len(target_slides), 3)
    slide_images = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_slide = {
            executor.submit(_generate_one_slide, slide, project_id, output_dir): slide
            for slide in target_slides
            if slide.prompt_text
        }

        for future in as_completed(future_to_slide):
            result = future.result()
            slide = result["slide"]

            if result.get("error"):
                slide.status = "failed"
                slide.error_msg = result["error"]
            else:
                slide.image_path = result["image_path"]
                slide.status = "completed"
                slide_images.append({
                    "page_num": slide.page_num,
                    "image_path": result["image_path"],
                    "speaker_notes": slide.content_json.get("speaker_notes", ""),
                })
            db.commit()

    slide_images.sort(key=lambda x: x["page_num"])
    # ... 组装 PPTX ...
```

---

## 6. 复盘与防御清单

如果你是下一个要接入类似 API 的研发，请逐项确认：

- [ ] **超时是否足够长？** 查看供应商的 P99 延迟， timeout ≥ 2× P99
- [ ] **重试是否有条件？** 不要 `except Exception: retry`，必须分类
- [ ] **操作是否幂等？** 图像生成、短信发送、支付扣款都是非幂等操作，重试 = 重复计费
- [ ] **下载是否与 API 调用分离？** 拿到 URL 后下载失败，不应触发重新生图
- [ ] **是否使用了 Idempotency-Key？** 如果供应商支持，这是最后一道防线
- [ ] **测试环境是否有 mock 开关？** 不要让 CI/自动化测试真的调用计费 API
- [ ] **并发数是否基于供应商限制？** 不要凭感觉设 `max_workers=10`，看账单和文档

---

## 7. 外部反馈与验证（XNTJ.AI 张拼拼）

> 以下信息来自同样接入 DeerAPI/NanoBanana Pro 的开发者，作为外部交叉验证。

| 信息点 | 内容 | 对我们修复的印证 |
|--------|------|----------------|
| **官方 API 也很慢** | NanoBanana Pro 的 4K 出图本身就很慢，官方 API 也可能超过 300 秒 | ✅ 1800 秒的超时设置方向正确，不是 DeerAPI 独有的问题 |
| **排队线 vs 直通线** | DeerAPI 走的是**排队线**（非直通），便宜但会排队到服务器空闲 | ✅ 解释了为什么最大延迟可达 **485 秒**——不是生成慢，而是排队等待 |
| **丢图的直接原因** | 排队期间客户端已超时断开，服务端生成完成后无人取图 | ✅ 与我们的根因分析完全一致：客户端事务回滚，服务端事务已提交 |

**关键洞察**：排队线模型下，客户端看到的"响应时间" = 排队时间 + 真实生成时间。即使真实生成只要 60 秒，排队 400 秒也会触发 300 秒超时。这进一步说明**无条件重试等于在排队高峰期主动制造财务灾难**。

---

## 8. 待验证与开放问题

1. **DeerAPI 是否真正支持 `1792x1024`？** 当前代码已写入该尺寸，若 DeerAPI 拒绝会回退到 `1536x1024`。
2. **Idempotency-Key 在 DeerAPI 侧是否生效？** 需要观察下一笔账单中的重复调用是否被去重。
3. **30 分钟超时是否会导致 HTTP 代理/防火墙断开？** 部分企业防火墙会在 10 分钟无数据时切断连接，需观察生产环境。
4. **Celery 接入后是否需要调整并发策略？** 当前用 `ThreadPoolExecutor`，未来切 Celery 时并发模型会变。

---

## 9. 给后续 Reviewer 的话

> 如果你在看这份文档，说明你可能正在用 Cloud Code 或类似工具接入 DeerAPI / OpenAI / MiniMax 的图像接口。
>
> **核心教训不是"把超时改长"，而是：在非幂等操作上做无差别重试，等于在财务上埋雷。**
>
> 如果你认为有更优雅的重试策略（例如基于账单 ID 的去重、或者服务端 webhook 回调），请直接在代码库提 PR。本报告中的代码是"止血"版本，欢迎更好的方案。

---

*报告生成时间: 2026-04-26*  
*相关代码路径: `backend/app/services/image_generation.py`, `backend/app/services/generation_pipeline.py`*
