import { useState, useEffect, useRef, ReactNode } from "react";
import { invoke } from "@tauri-apps/api/core";

interface WindowState {
  x: number;
  y: number;
  width: number;
  height: number;
  minimized: boolean;
  maximized: boolean;
  closed: boolean;
  poppedOut?: boolean;
  popX?: number;
  popY?: number;
  popWidth?: number;
  popHeight?: number;
}

interface Props {
  id: string;
  title: string;
  children: ReactNode;
  defaultState: WindowState;
  onClose?: () => void;
  tradingViewTicker?: string;
  onStateChange?: (state: Partial<WindowState>) => void;
  onPopOut?: (id: string) => void;
  onPopIn?:  (id: string) => void;
}

const MIN_WIDTH  = 200;
const MIN_HEIGHT = 120;

let zCounter = 100;
function getNextZ() { return ++zCounter; }

export function FloatingWindow({
  id, title, children, defaultState,
  onClose, tradingViewTicker, onStateChange,
  onPopOut, onPopIn,
}: Props) {
  const [state, setState]         = useState<WindowState>(() => {
    const saved = localStorage.getItem(`window_${id}`);
    return saved ? JSON.parse(saved) : defaultState;
  });
  const [zIndex, setZIndex]       = useState<number>(() => getNextZ());
  const [popping, setPopping]     = useState(false);
  const [preMaxState, setPreMaxState] = useState<WindowState | null>(null);

  const dragRef   = useRef({ active: false, startX: 0, startY: 0, origX: 0, origY: 0 });
  const resizeRef = useRef({ active: false, handle: "", startX: 0, startY: 0, origX: 0, origY: 0, origW: 0, origH: 0 });

  useEffect(() => {
    localStorage.setItem(`window_${id}`, JSON.stringify(state));
    onStateChange?.(state);
  }, [state, id]);

  function bringToFront() { setZIndex(getNextZ()); }

  // ── Pop ud til ny skærm ───────────────────────────────────
  async function handlePopOut() {
    setPopping(true);
    try {
      const savedKey = `popout_pos_${id}`;
      const savedPos = localStorage.getItem(savedKey);
      let px = 200, py = 200, pw = state.width, ph = state.height;
      if (savedPos) {
        const p = JSON.parse(savedPos);
        px = p.x; py = p.y; pw = p.width; ph = p.height;
      }
      await invoke("create_popout_window", {
        windowId: id, title,
        x: px, y: py, width: pw, height: ph,
      });
      setState(prev => ({ ...prev, poppedOut: true, popX: px, popY: py, popWidth: pw, popHeight: ph }));
      onPopOut?.(id);
    } catch (e) {
      console.error("Pop-out fejl:", e);
    }
    setPopping(false);
  }

  // ── Pop tilbage ind ───────────────────────────────────────
  function handlePopIn() {
    setState(prev => ({ ...prev, poppedOut: false }));
    invoke("close_popout_window", { windowId: id }).catch(() => {});
    onPopIn?.(id);
  }

  // ── Drag ──────────────────────────────────────────────────
  function onTitleMouseDown(e: React.MouseEvent) {
    if (e.button !== 0) return;
    if (state.maximized) return;
    if ((e.target as HTMLElement).closest(".window-btn")) return;
    e.preventDefault();
    bringToFront();
    dragRef.current = { active: true, startX: e.clientX, startY: e.clientY, origX: state.x, origY: state.y };
    function onMouseMove(e: MouseEvent) {
      if (e.buttons === 0) { cleanup(); return; }
      if (!dragRef.current.active) return;
      setState(prev => ({
        ...prev,
        x: Math.max(0, dragRef.current.origX + e.clientX - dragRef.current.startX),
        y: Math.max(0, dragRef.current.origY + e.clientY - dragRef.current.startY),
      }));
    }
    function onMouseUp() { cleanup(); }
    function cleanup() {
      dragRef.current.active = false;
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
    resizeRef.current = { active: true, handle, startX: e.clientX, startY: e.clientY, origX: state.x, origY: state.y, origW: state.width, origH: state.height };
    function onMouseMove(e: MouseEvent) {
      if (e.buttons === 0) { cleanup(); return; }
      if (!resizeRef.current.active) return;
      const dx = e.clientX - resizeRef.current.startX;
      const dy = e.clientY - resizeRef.current.startY;
      const { handle, origX, origY, origW, origH } = resizeRef.current;
      let newX = origX, newY = origY, newW = origW, newH = origH;
      if (handle.includes("e")) newW = Math.max(MIN_WIDTH,  origW + dx);
      if (handle.includes("s")) newH = Math.max(MIN_HEIGHT, origH + dy);
      if (handle.includes("w")) { newW = Math.max(MIN_WIDTH,  origW - dx); newX = origX + origW - newW; }
      if (handle.includes("n")) { newH = Math.max(MIN_HEIGHT, origH - dy); newY = origY + origH - newH; }
      setState(prev => ({ ...prev, x: newX, y: newY, width: newW, height: newH }));
    }
    function onMouseUp() { cleanup(); }
    function cleanup() {
      resizeRef.current.active = false;
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup",   onMouseUp);
    }
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup",   onMouseUp);
  }

  // ── Knapper ───────────────────────────────────────────────
  function toggleMinimize() { setState(prev => ({ ...prev, minimized: !prev.minimized })); }

  function toggleMaximize() {
    if (state.maximized) {
      setState(prev => ({ ...prev, maximized: false, ...(preMaxState || {}) }));
      setPreMaxState(null);
    } else {
      setPreMaxState({ ...state });
      setState(prev => ({ ...prev, maximized: true, x: 0, y: 0, width: window.innerWidth - 200, height: window.innerHeight - 34 }));
    }
    bringToFront();
  }

  function handleClose() {
    setState(prev => ({ ...prev, closed: true }));
    onClose?.();
  }

  function openTradingView() {
    if (tradingViewTicker) {
      window.open(`https://www.tradingview.com/chart/?symbol=${tradingViewTicker}`, "_blank");
    }
  }

  if (state.closed) return null;

  // ── Pladsholder når vinduet er poppet ud ──────────────────
  if (state.poppedOut) {
    return (
      <div
        className="floating-window floating-window--popped"
        style={{ zIndex, left: state.x, top: state.y, width: state.width, height: 36 }}
        onMouseDown={bringToFront}
      >
        <div className="window-titlebar">
          <span className="window-title window-title--popped">{title} — på anden skærm</span>
          <div className="window-buttons">
            <button
              className="window-btn popin-btn"
              onClick={handlePopIn}
              title="Hent tilbage til Trading Dash"
            >⬅</button>
          </div>
        </div>
      </div>
    );
  }

  const style: React.CSSProperties = {
    zIndex,
    ...(state.maximized
      ? { left: 0, top: 0, width: window.innerWidth - 200, height: window.innerHeight - 34 }
      : { left: state.x, top: state.y, width: state.width, height: state.minimized ? "auto" : state.height }
    ),
  };

  return (
    <div
      className={`floating-window ${state.minimized ? "minimized" : ""}`}
      style={style}
      onMouseDown={bringToFront}
    >
      <div className="window-titlebar" onMouseDown={onTitleMouseDown}>
        <span className="window-title">{title}</span>
        <div className="window-buttons">
          {tradingViewTicker && (
            <button className="window-btn tv-btn"     onClick={openTradingView} title="Åbn i TradingView">TV</button>
          )}
          <button
            className="window-btn popout-btn"
            onClick={handlePopOut}
            disabled={popping}
            title="Pop ud på anden skærm"
          >⬆</button>
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
