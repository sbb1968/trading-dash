/**
 * Besked.tsx — modal til beskeder og bekræftelser
 * ════════════════════════════════════════════════════════════════════════
 * ⚠ ERSTATTER `window.alert()` OG `window.confirm()`.
 *
 * De to er browserens egne dialogbokse: store, hvide, systemskrift, midt på
 * skærmen, uanset hvilket tema Trading Dash kører. Målt 09-10 stod en hvid
 * systemboks oven på et mørkt handelsvindue mens prismodalen lige ved siden
 * af var sort — og de to betyder det samme: "læs det her, før du går videre".
 *
 * ⚠ OG DET ER IKKE KUN PYNT. En `alert()` BLOKERER browserens tråd. Mens den
 * står åben, tegner React ikke, timere fyrer ikke, og WebSocket-beskeder
 * hober sig op. Står den i et ordresvar — og det gør flere af dem — fryser
 * kursopdateringen bag den. En modal i React gør ikke det.
 *
 * Teksten er bevidst `whiteSpace: pre-line`, så "\n\n" i beskederne stadig
 * giver afsnit. Beskederne var skrevet til alert() og skal kunne læses som de
 * er.
 */
import { useEffect, useRef } from "react";

export type BeskedArt = "info" | "fejl" | "spoerg";

export interface BeskedData {
  art:     BeskedArt;
  titel:   string;
  tekst:   string;
  /** Kun for `spoerg`. Kaldes når brugeren bekræfter. */
  onJa?:   () => void;
  jaTekst?: string;
}

export function Besked({ data, onLuk }: {
  data: BeskedData | null;
  onLuk: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!data) return;
    ref.current?.focus();
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") onLuk();
      // ⚠ Enter bekræfter KUN et spørgsmål. På en ren besked ville det blot
      // lukke — og på en fejl man ikke har læst endnu, er det fint.
      if (e.key === "Enter" && data.art === "spoerg") { data.onJa?.(); onLuk(); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [data, onLuk]);

  if (!data) return null;
  const farve = data.art === "fejl" ? "var(--bear)"
              : data.art === "spoerg" ? "var(--accent)"
              : "var(--text-secondary)";

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)",
      display: "flex", alignItems: "center", justifyContent: "center",
      zIndex: 2000,
    }} onMouseDown={e => { if (e.target === e.currentTarget) onLuk(); }}>
      <div style={{
        background: "var(--bg-elevated)",
        border: `1px solid ${data.art === "fejl" ? "var(--bear)" : "var(--border-strong)"}`,
        borderRadius: 8, padding: 20, minWidth: 340, maxWidth: 520,
        boxShadow: "0 12px 40px rgba(0,0,0,0.5)",
      }}>
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8,
                      color: farve }}>
          {data.art === "fejl" ? "⚠ " : ""}{data.titel}
        </div>
        <div style={{ fontSize: 12.5, color: "var(--text-primary)",
                      lineHeight: 1.5, whiteSpace: "pre-line" }}>
          {data.tekst}
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 18,
                      justifyContent: "flex-end" }}>
          {data.art === "spoerg" && (
            <button onClick={onLuk}
              style={{ padding: "6px 14px", fontSize: 12, borderRadius: 4,
                       background: "transparent", color: "var(--text-secondary)",
                       border: "1px solid var(--border-default)",
                       cursor: "pointer" }}>
              Annullér
            </button>
          )}
          <button ref={ref}
            onClick={() => { if (data.art === "spoerg") data.onJa?.(); onLuk(); }}
            style={{ padding: "6px 16px", fontSize: 12, fontWeight: 700,
                     borderRadius: 4, cursor: "pointer",
                     border: `1px solid ${farve}`,
                     background: "transparent", color: farve }}>
            {data.art === "spoerg" ? (data.jaTekst || "OK") : "OK"}
          </button>
        </div>
      </div>
    </div>
  );
}
