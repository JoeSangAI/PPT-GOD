import { useCallback, useEffect, useState } from "react";
import { fetchWorkflowStatus } from "../api/client";
import { isActiveRun } from "../workflow";
import type { WorkflowRun } from "../workflow";

export interface WorkflowProgress {
  run_id?: string;
  kind?: string;
  status?: string;
  stage?: string;
  label?: string;
  message?: string | null;
  current?: number;
  total?: number;
  failed?: number;
  unit?: string;
  percent?: number;
  target_page_nums?: number[] | null;
  can_cancel?: boolean;
  current_page?: number;
  total_pages?: number;
  active_page_nums?: number[];
  running_count?: number;
}

export interface WorkflowStatus {
  project_id: string;
  project_phase: string;
  project_status: string;
  total_slides: number;
  completed_slides: number;
  total_completed_slides?: number;
  target_completed_slides?: number;
  target_failed_slides?: number;
  target_count?: number;
  target_page_nums?: number[] | null;
  active_run?: WorkflowRun | null;
  last_run?: WorkflowRun | null;
  progress?: WorkflowProgress | null;
  has_pptx?: boolean;
  pptx_path?: string | null;
  quality_report?: {
    status?: string;
    signature?: string;
    summary?: string;
    message?: string;
    issues?: Array<{
      kind?: string;
      severity?: string;
      title?: string;
      pages?: number[];
      recommendation?: string;
    }>;
    agent_role?: "visual" | "content";
  } | null;
  slides?: Array<{ id?: string; page_num: number; status: string; error_msg?: string | null }>;
}

export function useProjectWorkflow(projectId?: string | null) {
  const [workflowStatus, setWorkflowStatus] = useState<WorkflowStatus | null>(null);

  const refreshWorkflowStatus = useCallback(async () => {
    if (!projectId) {
      setWorkflowStatus(null);
      return null;
    }
    const data = await fetchWorkflowStatus(projectId);
    if (data?.project_id === projectId) {
      setWorkflowStatus(data);
    }
    return data as WorkflowStatus;
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    if (!projectId) {
      setWorkflowStatus(null);
      return;
    }

    fetchWorkflowStatus(projectId)
      .then((data) => {
        if (!cancelled && data?.project_id === projectId) {
          setWorkflowStatus(data);
        }
      })
      .catch((err) => {
        if (!cancelled) console.warn("Workflow status load failed:", err);
      });

    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const activeRun = workflowStatus?.active_run || null;
  const hasActiveRun = isActiveRun(activeRun);

  useEffect(() => {
    if (!projectId || !hasActiveRun) return;
    let cancelled = false;
    let isFetching = false;

    const interval = setInterval(async () => {
      if (isFetching) return;
      isFetching = true;
      try {
        const data = await fetchWorkflowStatus(projectId);
        if (!cancelled && data?.project_id === projectId) {
          setWorkflowStatus(data);
        }
      } catch (err) {
        if (!cancelled) console.warn("Workflow status poll failed:", err);
      } finally {
        isFetching = false;
      }
    }, 3000);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [projectId, hasActiveRun, activeRun?.id, activeRun?.status]);

  return {
    workflowStatus,
    setWorkflowStatus,
    refreshWorkflowStatus,
    activeRun,
    hasActiveRun,
  };
}
