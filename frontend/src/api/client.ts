export const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function checkRes(res: Response) {
  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error");
    const isHtml = text.trim().startsWith("<") && text.includes("</");
    if (isHtml) {
      const title = text.match(/<title>(.*?)<\/title>/i)?.[1];
      throw new Error(`HTTP ${res.status}: ${title || "服务器错误"}`);
    }
    // FastAPI 返回 { detail: "..." }，尝试提取
    try {
      const json = JSON.parse(text);
      if (json.detail) {
        throw new Error(`HTTP ${res.status}: ${json.detail}`);
      }
    } catch {
      // 不是 JSON，继续用原文
    }
    throw new Error(`HTTP ${res.status}: ${text.slice(0, 200)}`);
  }
  return res;
}

export async function fetchProjects() {
  const res = await fetch(`${API_BASE}/projects`);
  return (await checkRes(res)).json();
}

export async function fetchProject(projectId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}`);
  return (await checkRes(res)).json();
}

export async function createProject(title: string, styleId?: string) {
  const res = await fetch(`${API_BASE}/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, style_id: styleId }),
  });
  return (await checkRes(res)).json();
}

export async function updateProject(projectId: string, data: { title?: string; content_plan_confirmed?: boolean }) {
  const res = await fetch(`${API_BASE}/projects/${projectId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return (await checkRes(res)).json();
}

export async function deleteProject(projectId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}`, {
    method: "DELETE",
  });
  return (await checkRes(res)).json();
}

export async function generateContentPlan(projectId: string, topic?: string, pageCount?: number) {
  const body: any = {};
  if (topic) body.topic = topic;
  if (pageCount) body.page_count = pageCount;
  const res = await fetch(`${API_BASE}/projects/${projectId}/content-plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await checkRes(res)).json();
}

export async function fetchSlides(projectId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/slides`);
  return (await checkRes(res)).json();
}

export async function generateVisualPlan(projectId: string, pageNums?: number[]) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/visual-plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(pageNums ? { page_nums: pageNums } : {}),
  });
  return (await checkRes(res)).json();
}

export async function generatePrompts(projectId: string, pageNums?: number[]) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/prompts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(pageNums ? { page_nums: pageNums } : {}),
  });
  return (await checkRes(res)).json();
}

export async function generateVisualPrompts(projectId: string, pageNums?: number[]) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/visual-prompts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(pageNums ? { page_nums: pageNums } : {}),
  });
  return (await checkRes(res)).json();
}

/**

 * @returns {{ generation_status: "running" | "idle", project_status: string, active_run: object | null }}
 */
export async function fetchGenerationStatus(projectId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/generation-status`);
  return (await checkRes(res)).json();
}


export async function startGeneration(projectId: string, pageNums?: number[], prototype?: boolean) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page_nums: pageNums, prototype }),
  });
  return (await checkRes(res)).json();
}

export async function stopGeneration(projectId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/stop-generation`, {
    method: "POST",
  });
  return (await checkRes(res)).json();
}

export async function confirmPrototype(projectId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/confirm-prototype`, {
    method: "POST",
  });
  return (await checkRes(res)).json();
}

export async function fetchProjectStatus(projectId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/status`);
  return (await checkRes(res)).json();
}

export async function fetchGenerationProgress(projectId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/generation-progress`);
  return (await checkRes(res)).json();
}

export function getDownloadUrl(projectId: string, prototype?: boolean) {
  return prototype
    ? `${API_BASE}/projects/${projectId}/download?prototype=1`
    : `${API_BASE}/projects/${projectId}/download`;
}

export async function uploadFile(
  projectId: string,
  file: File,
  role: "style_ref" | "logo" | "template" | "content_ref" | "chart_ref" | "finetune_ref",
  slideId?: string,
  processMode?: "blend" | "crop" | "original"
) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("role", role);
  if (slideId) {
    formData.append("slide_id", slideId);
  }
  formData.append("process_mode", processMode || "blend");
  const res = await fetch(`${API_BASE}/projects/${projectId}/upload`, {
    method: "POST",
    body: formData,
  });
  return (await checkRes(res)).json();
}

export async function suggestReferenceImages(projectId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/suggest-reference-images`, {
    method: "POST",
  });
  return (await checkRes(res)).json();
}

export async function fetchReferenceImages(projectId: string, slideId?: string) {
  let url = `${API_BASE}/projects/${projectId}/reference-images`;
  if (slideId) {
    url += `?slide_id=${slideId}`;
  }
  const res = await fetch(url);
  return (await checkRes(res)).json();
}

export async function deleteReferenceImage(projectId: string, refId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/reference-images/${refId}`, {
    method: "DELETE",
  });
  return (await checkRes(res)).json();
}

export async function updateReferenceImageMode(projectId: string, refId: string, processMode: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/reference-images/${refId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ process_mode: processMode }),
  });
  return (await checkRes(res)).json();
}

export async function retrySlide(projectId: string, slideId: string, regeneratePrompt: boolean = false) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/slides/${slideId}/retry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ regenerate_prompt: regeneratePrompt }),
  });
  return (await checkRes(res)).json();
}

export async function retryFailed(projectId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/retry-failed`, {
    method: "POST",
  });
  return (await checkRes(res)).json();
}

// ========== 单页微调：版本管理 ==========

export async function getSlideVersions(projectId: string, slideId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/slides/${slideId}/versions`);
  return (await checkRes(res)).json();
}

export async function deleteSlideVersion(projectId: string, slideId: string, versionId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/slides/${slideId}/versions/${versionId}`, {
    method: "DELETE",
  });
  return (await checkRes(res)).json();
}

export async function restoreSlideVersion(projectId: string, slideId: string, versionId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/slides/${slideId}/versions/${versionId}/restore`, {
    method: "POST",
  });
  return (await checkRes(res)).json();
}

export async function finetuneSlide(projectId: string, slideId: string, instruction: string, attachmentIds?: string[]) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/slides/${slideId}/finetune`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instruction, attachment_ids: attachmentIds || [] }),
  });
  return (await checkRes(res)).json();
}

export async function* chatWithAgentStream(
  projectId: string,
  message: string,
  history?: { role: string; content: string }[],
  signal?: AbortSignal,
  pageContext?: any,
  agentRole?: string
) {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/projects/${projectId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history, page_context: pageContext, agent_role: agentRole || "content" }),
      signal,
    });
  } catch {
    yield { type: "error", message: "网络连接失败，请检查网络后重试" };
    return;
  }

  if (!response.ok) {
    const text = await response.text().catch(() => "Unknown error");
    yield { type: "error", message: `HTTP ${response.status}: ${text}` };
    return;
  }

  if (!response.body) {
    yield { type: "error", message: "服务器未返回数据" };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.slice(6));
            if (import.meta.env.DEV) {
              console.debug("[chatWithAgentStream] yield event:", data.type);
            }
            yield data;
          } catch {
            // ignore malformed lines
          }
        }
      }
    }
  } catch (readErr: any) {
    const errMsg = readErr?.message || "";
    // 只有用户主动取消（点击停止）才静默返回
    if (signal?.aborted) {
      return;
    }
    // Chrome 等浏览器在流意外中断时会抛 AbortError（如 BodyStreamBuffer was aborted）
    // 这不是用户主动取消，必须上报，否则聊天会"卡住"且用户无感知
    yield { type: "error", message: "读取响应流失败：" + (errMsg || "网络连接中断") };
    return;
  } finally {
    reader.releaseLock();
  }

  if (buffer.startsWith("data: ")) {
    try {
      yield JSON.parse(buffer.slice(6));
    } catch {
      // 流结束时还有未解析完的 data 行（JSON 被截断），主动报错而不是静默忽略
      yield { type: "error", message: "响应流被意外中断，JSON 不完整" };
    }
  }
}

export async function uploadDocument(projectId: string, file: File) {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE}/projects/${projectId}/upload-document`, {
    method: "POST",
    body: formData,
  });
  return (await checkRes(res)).json();
}

export async function fetchDocuments(projectId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/documents`);
  return (await checkRes(res)).json();
}

export async function deleteDocument(projectId: string, filename: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/documents/${encodeURIComponent(filename)}`, {
    method: "DELETE",
  });
  return (await checkRes(res)).json();
}

export async function updateSlideContent(projectId: string, pageNum: number, contentJson: any, slideId?: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/slides/content`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page_num: pageNum, slide_id: slideId, content_json: contentJson }),
  });
  return (await checkRes(res)).json();
}

export async function updateVisualPlan(projectId: string, pageNum: number, visualJson: any, slideId?: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/slides/visual`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page_num: pageNum, slide_id: slideId, visual_json: visualJson }),
  });
  return (await checkRes(res)).json();
}

export async function deleteSlide(projectId: string, slideId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/slides/${slideId}`, {
    method: "DELETE",
  });
  return (await checkRes(res)).json();
}

export async function createSlide(projectId: string, pageNum: number, contentJson: any) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/slides`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page_num: pageNum, content_json: contentJson }),
  });
  return (await checkRes(res)).json();
}

export async function reorderSlides(projectId: string, pageNums: number[]) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/reorder`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ page_nums: pageNums }),
  });
  return (await checkRes(res)).json();
}

export async function setSeedPage(projectId: string, slideId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/slides/${slideId}/set-seed`, {
    method: "POST",
  });
  return (await checkRes(res)).json();
}

export async function unsetSeedPage(projectId: string, slideId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/slides/${slideId}/unset-seed`, {
    method: "POST",
  });
  return (await checkRes(res)).json();
}

export async function extractTemplate(projectId: string, file: File) {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE}/projects/${projectId}/extract-template`, {
    method: "POST",
    body: formData,
  });
  return (await checkRes(res)).json();
}

export async function fetchTemplatePages(projectId: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/template-pages`);
  return (await checkRes(res)).json();
}

export async function updateProjectStyle(projectId: string, selectedStyle: any) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/style`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected_style: selectedStyle }),
  });
  return (await checkRes(res)).json();
}

export async function generateStyleProposals(projectId: string, force: boolean = false): Promise<any> {
  const url = new URL(`${API_BASE}/projects/${projectId}/style-proposals`);
  if (force) url.searchParams.set("force", "true");
  const res = await fetch(url.toString(), {
    method: "POST",
  });
  return (await checkRes(res)).json();
}

export async function pollForStyleProposals(
  projectId: string,
  maxAttempts = 40,
  intervalMs = 3000
): Promise<any[]> {
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise((r) => setTimeout(r, intervalMs));
    const project = await fetchProject(projectId);
    if (project?.style_proposal?.proposals) {
      return project.style_proposal.proposals;
    }
  }
  throw new Error("风格提案生成超时，请刷新页面后重试");
}

export async function updateTemplateRecommendations(projectId: string, recommendations: any) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/template-recommendations`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ recommendations }),
  });
  return (await checkRes(res)).json();
}

export async function rollbackProject(projectId: string, targetStage: string) {
  const res = await fetch(`${API_BASE}/projects/${projectId}/rollback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_stage: targetStage }),
  });
  return (await checkRes(res)).json();
}
