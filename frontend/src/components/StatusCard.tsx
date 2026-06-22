import type { StatusCardData, StatusCardAction, StatusActionKey } from "../workflow";

const TONE_ICON: Record<StatusCardData["tone"], string> = {
  running: "🔵",
  danger: "🔴",
  warning: "🟡",
  success: "✅",
  info: "📝",
};

interface StatusCardProps {
  card: StatusCardData;
  onAction: (key: StatusActionKey) => void;
}

export function StatusCard({ card, onAction }: StatusCardProps) {
  const renderAction = (action: StatusCardAction) => {
    const baseClass = "pg-status-card-button pg-action text-sm rounded disabled:opacity-50 whitespace-nowrap px-3 py-1";
    const variantClass =
      action.variant === "danger"
        ? "bg-red-600 text-white hover:bg-red-700 border border-red-600"
        : action.variant === "secondary"
        ? "bg-white text-slate-700 hover:bg-slate-50 border border-slate-300"
        : "bg-slate-900 text-white hover:bg-slate-800 border border-slate-900";
    return (
      <button
        key={action.key}
        type="button"
        onClick={() => onAction(action.key)}
        disabled={action.disabled}
        className={`${baseClass} ${variantClass}`}
        title={action.title || action.label}
        aria-label={action.title || action.label}
      >
        {action.label}
      </button>
    );
  };

  return (
    <section
      className={`pg-status-card pg-status-card--${card.tone}`}
      role={card.tone === "danger" || card.tone === "warning" ? "alert" : "status"}
      aria-live="polite"
    >
      <span className="pg-status-card-icon" aria-hidden="true">
        {TONE_ICON[card.tone]}
      </span>
      <div className="pg-status-card-content">
        <div className="pg-status-card-title-row">
          <b className="pg-status-card-title">{card.title}</b>
          {card.progress && (
            <span className="pg-status-card-progress-text" aria-label={`进度 ${card.progress.current} / ${card.progress.total} ${card.progress.unit}`}>
              {card.progress.current} / {card.progress.total} {card.progress.unit}
            </span>
          )}
        </div>
        {card.description && (
          <p className="pg-status-card-desc">{card.description}</p>
        )}
        {card.progress && (
          <div
            className="pg-status-card-bar"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(card.progress.percent)}
          >
            <i style={{ transform: `scaleX(${Math.max(0, Math.min(100, card.progress.percent)) / 100})` }} />
          </div>
        )}
      </div>
      <div className="pg-status-card-actions">
        {card.secondary && renderAction(card.secondary)}
        {card.primary && renderAction(card.primary)}
      </div>
    </section>
  );
}
