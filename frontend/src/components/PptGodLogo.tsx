interface PptGodLogoProps {
  className?: string;
  subtitle?: string;
  markOnly?: boolean;
}

export function PptGodMark({ className = "" }: { className?: string }) {
  return (
    <span className={`pg-brand-mark ${className}`} aria-hidden="true">
      <svg viewBox="0 0 64 64" fill="none">
        <rect x="12" y="18" width="40" height="30" rx="4" stroke="currentColor" strokeWidth="3" />
        <path d="M21 29h18M21 37h22" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
        <path d="M45 11v8M41 15h8" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" />
      </svg>
    </span>
  );
}

export default function PptGodLogo({ className = "", subtitle = "古希腊掌管 PPT 的神", markOnly = false }: PptGodLogoProps) {
  if (markOnly) return <PptGodMark className={className} />;

  return (
    <span className={`pg-logo-lockup ${className}`}>
      <PptGodMark />
      <span className="pg-logo-text">
        <span className="pg-logo-name">PPT God</span>
        <span className="pg-logo-subtitle">{subtitle}</span>
      </span>
    </span>
  );
}
