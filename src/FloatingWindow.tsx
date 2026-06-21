import { useState, useEffect, useRef, ReactNode } from "react";

interface WindowState {
  x: number;
  y: number;
  width: number;
  height: number;
  minimized: boolean;
  maximized: boolean;
  closed: boolean;
}

interface Props {
  id: string;
  title: string;
  children: ReactNode;
  defaultState: WindowState;
  onClose?: () => void;
  tradingViewTicker?: string;
  onStateChange?: (state: Partial<WindowState>) => void;
  windowType?: string; // "scanner" | "watchlist" | "newsroom" | "chart" | "level2" | "timesales" | "paper"
}

const MIN_WIDTH  = 200;
const MIN_HEIGHT = 120;

let zCounter = 100;
function getNextZ() { return ++zCounter; }

export function FloatingWindow({ id, title, children, defaultState, onClose, tradingViewTicker, onStateChange, windowType }: Props) {
  const [state, setState]             = useState<WindowState>(defaultState);
  const [zIndex, setZIndex]           = useState<number>(() => getNextZ());
  const [preMaxState, setPreMaxState] = useState<WindowState | null>(null);
  const isUserDriving                 = useRef(false);

  // Synkroniser state når defaultState ændres udefra (fx auto-arrange)
  useEffect(() => {
    if (!isUserDriving.current) {
      setState(defaultState);
    }
  }, [
    defaultState.x, defaultState.y,
    defaultState.width, defaultState.height,
    defaultState.minimized, defaultState.maximized,
    defaultState.closed,
  ]);

  useEffect(() => {
    localStorage.setItem(`window_${id}`, JSON.stringify(state));
    onStateChange?.(state);
  }, [state, id]);

  function bringToFront() { setZIndex(getNextZ()); }

  // ── Drag ──────────────────────────────────────────────────
  function onTitleMouseDown(e: React.MouseEvent) {
    if (e.button !== 0) return;
    if (state.maximized) return;
    if ((e.target as HTMLElement).closest(".window-btn")) return;
    e.preventDefault();
    bringToFront();
    const origX = state.x, origY = state.y;
    const startX = e.clientX, startY = e.clientY;
    isUserDriving.current = true;

    function onMouseMove(e: MouseEvent) {
      if (e.buttons === 0) { cleanup(); return; }
      setState(prev => ({ ...prev, x: Math.max(0, origX + e.clientX - startX), y: Math.max(0, origY + e.clientY - startY) }));
    }
    function onMouseUp() { cleanup(); }
    function cleanup() {
      isUserDriving.current = false;
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup",   onMouseUp);
    }
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup",   onMouseUp);
  }

  // ── Resize ────────────────────────────────────────────────
  function onResizeMouseDown(e: React.MouseEvent, handle: string) {
    if (e.button !== 0) return;
    if (state.maximized) return;
    e.preventDefault();
    e.stopPropagation();
    bringToFront();
    const origX = state.x, origY = state.y, origW = state.width, origH = state.height;
    const startX = e.clientX, startY = e.clientY;
    isUserDriving.current = true;

    function onMouseMove(e: MouseEvent) {
      if (e.buttons === 0) { cleanup(); return; }
      const dx = e.clientX - startX, dy = e.clientY - startY;
      let newX = origX, newY = origY, newW = origW, newH = origH;
      if (handle.includes("e")) newW = Math.max(MIN_WIDTH,  origW + dx);
      if (handle.includes("s")) newH = Math.max(MIN_HEIGHT, origH + dy);
      if (handle.includes("w")) { newW = Math.max(MIN_WIDTH,  origW - dx); newX = origX + origW - newW; }
      if (handle.includes("n")) { newH = Math.max(MIN_HEIGHT, origH - dy); newY = origY + origH - newH; }
      setState(prev => ({ ...prev, x: newX, y: newY, width: newW, height: newH }));
    }
    function onMouseUp() { cleanup(); }
    function cleanup() {
      isUserDriving.current = false;
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup",   onMouseUp);
    }
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup",   onMouseUp);
  }

  function toggleMinimize() { setState(prev => ({ ...prev, minimized: !prev.minimized })); }

  function toggleMaximize() {
    if (state.maximized) {
      setState(prev => ({ ...prev, maximized: false, ...(preMaxState || {}) }));
      setPreMaxState(null);
    } else {
      setPreMaxState({ ...state });
      setState(prev => ({ ...prev, maximized: true, x: 0, y: 0, width: window.innerWidth, height: window.innerHeight - 34 }));
    }
    bringToFront();
  }

  function handleClose() {
    setState(prev => ({ ...prev, closed: true }));
    onClose?.();
  }

  async function openTradingView() {
    if (!tradingViewTicker) return;
    const url = `https://www.tradingview.com/chart/?symbol=${tradingViewTicker}`;
    try {
      const { openUrl } = await import("@tauri-apps/plugin-opener");
      await openUrl(url);
    } catch {
      window.open(url, "_blank");
    }
  }

  if (state.closed) return null;

  // CSS-klasse til font-størrelse per vinduestype
  const typeClass = windowType ? `win-type-${windowType}` : "";

  const style: React.CSSProperties = {
    zIndex,
    ...(state.maximized
      ? { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight - 34 }
      : { left: state.x, top: state.y, width: state.width, height: state.minimized ? "auto" : state.height }
    ),
  };

  return (
    <div
      className={`floating-window ${typeClass} ${state.minimized ? "minimized" : ""}`}
      style={style}
      onMouseDown={bringToFront}
    >
      <div className="window-titlebar" onMouseDown={onTitleMouseDown}>
        <span className="window-title">{title}</span>
        <div className="window-buttons">
          {tradingViewTicker && (
            <button className="window-btn tv-btn" onClick={openTradingView} title="Åbn i TradingView">TV</button>
          )}
          <button className="window-btn minimize-btn" onClick={toggleMinimize}  title="Minimer">─</button>
          <button className="window-btn maximize-btn" onClick={toggleMaximize}  title="Maksimer">□</button>
          <button className="window-btn close-btn"    onClick={handleClose}     title="Luk">✕</button>
        </div>
      </div>

      {!state.minimized && (
        <div className="window-content">{children}</div>
      )}

      {!state.minimized && !state.maximized && (
        <>
          <div className="resize-handle resize-n"  onMouseDown={e => onResizeMouseDown(e, "n")} />
          <div className="resize-handle resize-s"  onMouseDown={e => onResizeMouseDown(e, "s")} />
          <div className="resize-handle resize-e"  onMouseDown={e => onResizeMouseDown(e, "e")} />
          <div className="resize-handle resize-w"  onMouseDown={e => onResizeMouseDown(e, "w")} />
          <div className="resize-handle resize-nw" onMouseDown={e => onResizeMouseDown(e, "nw")} />
          <div className="resize-handle resize-ne" onMouseDown={e => onResizeMouseDown(e, "ne")} />
          <div className="resize-handle resize-sw" onMouseDown={e => onResizeMouseDown(e, "sw")} />
          <div className="resize-handle resize-se" onMouseDown={e => onResizeMouseDown(e, "se")} />
        </>
      )}
    </div>
  );
}
