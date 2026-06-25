import { useEffect, useRef } from "react";

/**
 * Lille, genbrugelig bekraeftelses-dialog (samme look som SwingTop10's koersels-dialog).
 * Tastatur-sikker: default-fokus paa ANNULLER (et utilsigtet Enter afviser i stedet for at
 * bekraefte), Esc og backdrop-klik annullerer — aldrig auto-bekraeft. Bekraeft kraever et
 * eksplicit klik (eller at brugeren bevidst har tabbet til bekraeft-knappen og trykker Enter).
 *
 * Overlay'et er position:absolute -> render det i en position:relative container, saa det
 * daekker det aktuelle vindue og ikke hele app'en.
 */
export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  cancelLabel = "Annuller",
  onConfirm,
  onCancel,
  variant = "normal",
}: {
  title: string;
  body?: string;
  confirmLabel: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
  variant?: "normal" | "danger";
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    cancelRef.current?.focus();   // default-fokus paa Annuller
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const confirmBg = variant === "danger" ? "var(--bear)" : "var(--accent)";

  return (
    <div
      onClick={onCancel}
      style={{
        position: "absolute", inset: 0, background: "rgba(0,0,0,0.55)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 30, padding: 16,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: "var(--bg-elevated)", border: "1px solid var(--border-strong)",
          borderRadius: 8, padding: 16, maxWidth: 420, color: "var(--text-primary)",
        }}
      >
        <div style={{ fontSize: 15, fontWeight: 800, marginBottom: body ? 8 : 16 }}>{title}</div>
        {body && (
          <div style={{ fontSize: 13, lineHeight: 1.55, marginBottom: 16, color: "var(--text-secondary)" }}>
            {body}
          </div>
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button
            ref={cancelRef}
            onClick={onCancel}
            style={{
              background: "var(--bg-base)", color: "var(--text-primary)",
              border: "1px solid var(--border-strong)", borderRadius: 4,
              padding: "8px 14px", fontSize: 13, fontWeight: 700, cursor: "pointer",
            }}
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            style={{
              background: confirmBg, color: "var(--bg-base)", border: "none",
              borderRadius: 4, padding: "8px 14px", fontSize: 13, fontWeight: 700, cursor: "pointer",
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
