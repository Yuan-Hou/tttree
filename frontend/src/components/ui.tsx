import type { ButtonHTMLAttributes, ReactNode } from "react";

/** 小标题眉:衬线、字距拉开,作结构标记而非装饰。 */
export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <div className="font-serif text-[11px] uppercase tracking-[0.18em] text-ink-faint">{children}</div>
  );
}

/** 极小的等宽标签(slug / beat / kind / req)。 */
export function Tag({ children, tone = "soft" }: { children: ReactNode; tone?: "soft" | "accent" }) {
  const cls =
    tone === "accent"
      ? "bg-accent-soft text-accent-ink"
      : "bg-sunken text-ink-soft";
  return (
    <span className={`rounded-[5px] px-1.5 py-px font-mono text-[10.5px] leading-snug ${cls}`}>
      {children}
    </span>
  );
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "quiet" | "danger";
};

export function Button({ variant = "ghost", className = "", ...rest }: BtnProps) {
  const base =
    "inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-[12.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";
  const v = {
    primary: "bg-accent text-white hover:bg-accent-ink",
    ghost: "border border-line-strong bg-surface text-ink hover:border-ink-faint hover:bg-sunken",
    quiet: "text-ink-soft hover:bg-sunken hover:text-ink",
    danger: "text-ink-faint hover:bg-danger-soft hover:text-danger",
  }[variant];
  return <button className={`${base} ${v} ${className}`} {...rest} />;
}
