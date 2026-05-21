import wordmarkAlpha from "../assets/ppt-god-wordmark-approved-inscription-alpha.png";

interface PptGodLogoProps {
  className?: string;
  subtitle?: string;
  markOnly?: boolean;
}

export function PptGodMark({ className = "" }: { className?: string }) {
  return (
    <span className={`pg-brand-mark pg-brand-mark-wordmark ${className}`} aria-hidden="true">
      <img src={wordmarkAlpha} alt="" />
    </span>
  );
}

export default function PptGodLogo({ className = "", subtitle = "古希腊掌管 PPT 的神", markOnly = false }: PptGodLogoProps) {
  if (markOnly) return <PptGodMark className={className} />;

  return (
    <span className={`pg-logo-lockup ${className}`}>
      <img className="pg-logo-wordmark" src={wordmarkAlpha} alt="" aria-hidden="true" />
      <span className="sr-only">PPT GOD - {subtitle}</span>
    </span>
  );
}
