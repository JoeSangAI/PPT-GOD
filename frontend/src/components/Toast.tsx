import { useEffect } from "react";

export interface ToastItem {
  id: string;
  message: string;
  type: "success" | "error" | "info";
  duration?: number;
}

export default function ToastContainer({
  toasts,
  onRemove,
}: {
  toasts: ToastItem[];
  onRemove: (id: string) => void;
}) {
  return (
    <div className="pg-toast-container" aria-live="polite" aria-atomic="false">
      {toasts.map((toast) => (
        <ToastItemComponent key={toast.id} toast={toast} onRemove={onRemove} />
      ))}
    </div>
  );
}

function ToastItemComponent({
  toast,
  onRemove,
}: {
  toast: ToastItem;
  onRemove: (id: string) => void;
}) {
  useEffect(() => {
    const timer = setTimeout(() => onRemove(toast.id), toast.duration ?? 2600);
    return () => clearTimeout(timer);
  }, [toast.id, toast.duration, onRemove]);

  return (
    <div
      className={`pg-toast pg-toast-${toast.type}`}
      role={toast.type === "error" ? "alert" : "status"}
    >
      <div className="pg-toast-content">
        <span className="pg-toast-dot" aria-hidden="true" />
        <span className="pg-toast-message">{toast.message}</span>
      </div>
      <button
        type="button"
        className="pg-toast-close"
        onClick={() => onRemove(toast.id)}
        aria-label="关闭提示"
      >
        ×
      </button>
    </div>
  );
}
