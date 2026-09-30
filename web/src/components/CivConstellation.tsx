import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * 3D constellation for the Civilization graph.
 *
 * Nodes, orbits and links all live in real 3D space (Y-up, disc lying in the
 * XZ plane). A rotation matrix turns the scene, a perspective divide projects
 * it to screen, and everything is depth-sorted so near stars occlude far ones.
 * Dragging orbits the camera, the wheel dollies, and a slow auto-spin keeps the
 * field alive when untouched.
 */

export type StarKind =
  | "bot"
  | "constitution"
  | "council"
  | "decision"
  | "leaf"
  | "proposal"
  | "fabric"
  | "store"
  | "projection"
  | "provider"
  | "scope"
  | "entity"
  | "note"
  | "code"
  | "world_fact"
  | "experience"
  | "observation"
  | "mental_model";

export interface StarNode {
  id: string;
  /**
   * The API may add kinds over time (e.g. "constitution"). Kept open so an
   * unknown kind renders in the default colour instead of crashing the layout.
   */
  kind: StarKind | (string & {});
  label: string;
  status: string;
  score?: number;
  meta?: Record<string, any>;
}

export interface StarEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  directed: boolean;
  weight?: number;
}

/** RGB triples, so the renderer can build rgba() strings with per-node alpha. */
const KIND_COLOR: Record<StarKind, [number, number, number]> = {
  bot: [101, 224, 195],
  constitution: [255, 214, 122],
  council: [145, 214, 229],
  decision: [185, 164, 255],
  leaf: [255, 207, 139],
  proposal: [255, 155, 176],
  fabric: [192, 132, 252],     // Violet/Purple
  store: [56, 189, 248],       // Cyan
  projection: [251, 191, 36],  // Amber
  provider: [52, 211, 153],    // Emerald
  scope: [129, 140, 248],      // Indigo
  entity: [251, 113, 133],     // Rose/Coral
  note: [125, 211, 252],       // Sky
  code: [251, 146, 60],        // Orange
  world_fact: [14, 165, 233],   // Sky Blue
  experience: [244, 63, 94],   // Rose
  observation: [16, 185, 129], // Emerald
  mental_model: [139, 92, 246],// Purple
};

const KIND_RADIUS: Record<StarKind, number> = {
  constitution: 17,
  fabric: 18,
  store: 14,
  projection: 15,
  council: 15,
  provider: 12,
  bot: 11,
  scope: 10,
  proposal: 9,
  decision: 8,
  entity: 8,
  note: 7,
  code: 7,
  world_fact: 8,
  experience: 8,
  observation: 9,
  mental_model: 10,
  leaf: 6,
};

/** Colour for any kind, falling back to a neutral for kinds we don't know. */
export const colorOf = (kind: string): [number, number, number] =>
  (KIND_COLOR as Record<string, [number, number, number]>)[kind] ?? [190, 200, 215];

/** World radius for any kind, so an unknown kind still gets a drawable size. */
export const radiusOf = (kind: string): number =>
  (KIND_RADIUS as Record<string, number>)[kind] ?? 9;

/** Shell radius per depth ring, in world units. */
const SHELL_RADIUS = [0, 130, 240, 360];

interface Vec3 { x: number; y: number; z: number }

/** Screen-space pan offset, in projected world units. */
interface Pan { x: number; y: number }

interface PlacedNode extends StarNode {
  pos: Vec3;
  ring: number;
}

function hashOf(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i += 1) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967295;
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;
}

/**
 * Radial 3D layout: councils sit at the core, everything else lands on a shell
 * derived from its distance to a council. Slot angle is a hash of the id so a
 * node keeps its place across reloads and view switches.
 */
function layout(nodes: StarNode[], edges: StarEdge[]): PlacedNode[] {
  const index = new Map(nodes.map(n => [n.id, n]));
  const depthOf = new Map<string, number>();
  const depth = (id: string, seen: Set<string>): number => {
    if (depthOf.has(id)) return depthOf.get(id) as number;
    if (seen.has(id)) return 1;
    seen.add(id);
    const parents = edges
      .filter(e => e.target === id && index.has(e.source) && index.get(e.source)!.kind === "council")
      .map(e => e.source);
    const value = parents.length === 0 ? 0 : 1 + Math.max(...parents.map(p => depth(p, seen)));
    depthOf.set(id, value);
    return value;
  };
  for (const node of nodes) depth(node.id, new Set());

  const shells: StarNode[][] = [[], [], [], []];
  for (const node of nodes) {
    shells[Math.min(depth(node.id, new Set()), 3)].push(node);
  }

  const placed: PlacedNode[] = [];
  shells.forEach((shell, ring) => {
    const radius = SHELL_RADIUS[ring];
    // Golden-angle offset keeps shells from lining up into spokes.
    const phase = ring * 2.39996;
    shell.forEach((node, i) => {
      const angle = (i / Math.max(shell.length, 1)) * Math.PI * 2 + phase + hashOf(node.id) * 0.5;
      // A little vertical scatter so the disc is not perfectly flat.
      const lift = (hashOf(`${node.id}#y`) - 0.5) * (ring === 0 ? 8 : 46);
      placed.push({
        ...node,
        ring,
        pos: { x: Math.cos(angle) * radius, y: lift, z: Math.sin(angle) * radius },
      });
    });
  });
  return placed;
}

/** Widest distance from the origin any node reaches, so the camera can frame it. */
function placedSpread(nodes: StarNode[], edges: StarEdge[]): number[] {
  return layout(nodes, edges).map(n => Math.hypot(n.pos.x, n.pos.y, n.pos.z));
}

/**
 * Camera basis from yaw/pitch, then perspective projection to screen pixels.
 * `pan` is a screen-space offset in world units, applied after projection so the
 * whole field can be shifted without distorting the perspective.
 */
function makeProjector(
  yaw: number,
  pitch: number,
  distance: number,
  width: number,
  height: number,
  pan: Pan,
) {
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  const focal = Math.min(width, height) * 0.9;
  const cx = width / 2;
  const cy2 = height / 2;

  return (p: Vec3) => {
    // Yaw around the Y axis.
    const x1 = p.x * cy - p.z * sy;
    const z1 = p.x * sy + p.z * cy;
    // Pitch around the X axis.
    const y2 = p.y * cp - z1 * sp;
    const z2 = p.y * sp + z1 * cp;
    // Camera sits at +Z looking toward the origin.
    const camZ = z2 + distance;
    const scale = focal / Math.max(camZ, 1);
    return { x: cx + x1 * scale + pan.x, y: cy2 - y2 * scale - pan.y, depth: camZ, scale };
  };
}

interface CamState { yaw: number; pitch: number; distance: number; pan: Pan }

const DEFAULT_CAM: CamState = { yaw: -0.7, pitch: 0.95, distance: 520, pan: { x: 0, y: 0 } };
const freshCam = (): CamState => ({ ...DEFAULT_CAM, pan: { x: 0, y: 0 } });

export function CivConstellation({
  nodes,
  edges,
  selectedId,
  onSelect,
}: {
  nodes: StarNode[];
  edges: StarEdge[];
  selectedId: string | null;
  onSelect: (node: StarNode) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const reduced = useMemo(prefersReducedMotion, []);

  // Camera + interaction state kept in refs so the RAF loop reads them without
  // re-subscribing; the mirror in state is only used for the control buttons.
  const cam = useRef<CamState>(freshCam());
  const target = useRef<CamState>(freshCam());
  // Pointer drag state: a left drag orbits, a middle/right/shift drag pans.
  const drag = useRef<{
    x: number; y: number; yaw: number; pitch: number; panX: number; panY: number; mode: "orbit" | "pan";
  } | null>(null);
  // Active touch points, for the two-finger pinch gesture.
  const touches = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ dist: number; distance: number } | null>(null);
  const spin = useRef(true);
  const sizeRef = useRef({ width: 0, height: 0, dpr: 1 });
  // Last computed "fit" distance, so a resize keeps the user's relative zoom.
  const fitRef = useRef(0);

  // Widest shell radius, used to auto-frame the field inside the viewport.
  const extent = useMemo(
    () => Math.max(SHELL_RADIUS[SHELL_RADIUS.length - 1], ...placedSpread(nodes, edges)),
    [nodes, edges],
  );

  const placed = useMemo(() => layout(nodes, edges), [nodes, edges]);
  const byId = useMemo(() => new Map(placed.map(n => [n.id, n])), [placed]);
  const adjacency = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const e of edges) {
      if (!map.has(e.source)) map.set(e.source, new Set());
      if (!map.has(e.target)) map.set(e.target, new Set());
      map.get(e.source)!.add(e.target);
      map.get(e.target)!.add(e.source);
    }
    return map;
  }, [edges]);

  const focus = selectedId ?? hovered;
  const focusRef = useRef<string | null>(null);
  focusRef.current = focus;
  const selectedRef = useRef<string | null>(selectedId);
  selectedRef.current = selectedId;
  const placedRef = useRef(placed);
  placedRef.current = placed;
  const byIdRef = useRef(byId);
  byIdRef.current = byId;
  const adjRef = useRef(adjacency);
  adjRef.current = adjacency;
  const edgesRef = useRef(edges);
  edgesRef.current = edges;
  const reducedRef = useRef(reduced);
  reducedRef.current = reduced;

  // Projected screen positions of the last frame, for hit testing.
  const screenRef = useRef<{ id: string; x: number; y: number; r: number }[]>([]);

  const resize = useCallback(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const rect = wrap.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;
    sizeRef.current = { width: rect.width, height: rect.height, dpr };
    // Frame the scene. With focal = 0.9*min(w,h) and camZ ≈ distance, a node at
    // radius `extent` projects to extent*focal/distance, so fitting it inside
    // ~42% of the shorter side needs distance ≈ 2.4*extent. Scaling the user's
    // current zoom by the ratio oldFit->newFit keeps their relative zoom.
    if (rect.width > 0 && rect.height > 0) {
      const fit = extent * 2.4;
      const ratio = fitRef.current > 0 ? target.current.distance / fitRef.current : 1;
      fitRef.current = fit;
      const framed = Math.max(180, Math.min(1400, fit * ratio));
      target.current.distance = framed;
      cam.current.distance = framed;
    }
  }, [extent]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const { width, height, dpr } = sizeRef.current;
    if (width === 0 || height === 0) return;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const { yaw, pitch, distance, pan } = cam.current;
    const project = makeProjector(yaw, pitch, distance, width, height, pan);
    const nodesNow = placedRef.current;
    const currentFocus = focusRef.current;
    const selected = selectedRef.current;

    // ── Deep-space backdrop ──────────────────────────────────────────────
    const bg = ctx.createRadialGradient(width / 2, height * 0.44, 0, width / 2, height * 0.44, Math.max(width, height) * 0.75);
    bg.addColorStop(0, "rgba(24, 62, 78, 0.55)");
    bg.addColorStop(0.45, "rgba(8, 20, 38, 0.5)");
    bg.addColorStop(1, "rgba(2, 6, 16, 0.9)");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);

    // Deterministic starfield so it does not crawl while orbiting.
    for (let i = 0; i < 140; i += 1) {
      const a = hashOf(`sx${i}`) * width;
      const b = hashOf(`sy${i}`) * height;
      const r = hashOf(`sr${i}`) * 1.1 + 0.25;
      ctx.globalAlpha = 0.12 + hashOf(`sa${i}`) * 0.4;
      ctx.fillStyle = "#dff3ff";
      ctx.beginPath();
      ctx.arc(a, b, r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    // ── Orbit rings, sampled in 3D so they foreshorten correctly ────────
    for (let ring = 1; ring < SHELL_RADIUS.length; ring += 1) {
      const radius = SHELL_RADIUS[ring];
      const steps = 128;
      ctx.beginPath();
      for (let i = 0; i <= steps; i += 1) {
        const a = (i / steps) * Math.PI * 2;
        const p = project({ x: Math.cos(a) * radius, y: 0, z: Math.sin(a) * radius });
        if (i === 0) ctx.moveTo(p.x, p.y);
        else ctx.lineTo(p.x, p.y);
      }
      ctx.closePath();
      ctx.strokeStyle = ring === 2 ? "rgba(185,164,255,0.16)" : "rgba(101,224,195,0.20)";
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    // ── Core glow at the origin ──────────────────────────────────────────
    const core = project({ x: 0, y: 0, z: 0 });
    const coreR = 150 * core.scale;
    const halo = ctx.createRadialGradient(core.x, core.y, 0, core.x, core.y, coreR);
    halo.addColorStop(0, "rgba(158,245,221,0.35)");
    halo.addColorStop(0.5, "rgba(79,211,180,0.10)");
    halo.addColorStop(1, "rgba(79,211,180,0)");
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(core.x, core.y, coreR, 0, Math.PI * 2);
    ctx.fill();

    // ── Links in 3D, dimmed when unrelated to the focused node ──────────
    const allEdges = edgesRef.current;
    for (const e of allEdges) {
      const a = byIdRef.current.get(e.source);
      const b = byIdRef.current.get(e.target);
      if (!a || !b) continue;
      const related = !currentFocus || e.source === currentFocus || e.target === currentFocus;
      const pa = project(a.pos);
      const pb = project(b.pos);
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.lineTo(pb.x, pb.y);
      ctx.strokeStyle = related
        ? "rgba(101, 224, 195, 0.55)"
        : "rgba(145, 214, 229, 0.10)";
      ctx.lineWidth = related ? 1.6 : 0.8;
      ctx.stroke();
      if (e.directed && related) {
        // Arrowhead in screen space, oriented along the projected segment.
        const angle = Math.atan2(pb.y - pa.y, pb.x - pa.x);
        ctx.save();
        ctx.translate(pb.x, pb.y);
        ctx.rotate(angle);
        ctx.beginPath();
        ctx.moveTo(-7, -3.4);
        ctx.lineTo(0, 0);
        ctx.lineTo(-7, 3.4);
        ctx.closePath();
        ctx.fillStyle = "rgba(101, 224, 195, 0.8)";
        ctx.fill();
        ctx.restore();
      }
    }

    // ── Stars, painter-sorted back to front ─────────────────────────────
    const projected = nodesNow.map(node => {
      const p = project(node.pos);
      return { node, ...p, screen: radiusOf(node.kind) * Math.max(p.scale, 0.25) };
    });
    projected.sort((a, b) => b.depth - a.depth);

    const screen: { id: string; x: number; y: number; r: number }[] = [];
    for (const item of projected) {
      const { node, x, y, screen: r } = item;
      const related = !currentFocus
        || node.id === currentFocus
        || (adjRef.current.get(currentFocus)?.has(node.id) ?? false);
      const isSelected = node.id === selected;
      const alpha = currentFocus && !related ? 0.18 : 1;
      const [cr, cg, cb] = colorOf(node.kind);

      // Bloom.
      const glow = ctx.createRadialGradient(x, y, 0, x, y, r * 4.2);
      glow.addColorStop(0, `rgba(${cr},${cg},${cb},${0.55 * alpha})`);
      glow.addColorStop(1, `rgba(${cr},${cg},${cb},0)`);
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(x, y, r * 4.2, 0, Math.PI * 2);
      ctx.fill();

      // Core.
      ctx.globalAlpha = alpha;
      ctx.fillStyle = `rgb(${cr},${cg},${cb})`;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();

      // Selection halo.
      if (isSelected) {
        ctx.strokeStyle = "rgba(255,255,255,0.9)";
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        ctx.arc(x, y, r + 4, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;

      // Label, flipped below the star when it is behind the disc center.
      if (r > 3.2 || isSelected) {
        ctx.font = `${Math.max(10, Math.min(14, r * 0.85))}px ui-sans-serif, system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.fillStyle = `rgba(228,240,245,${0.82 * alpha})`;
        ctx.strokeStyle = "rgba(2,6,16,0.9)";
        ctx.lineWidth = 3;
        const text = node.label.length > 20 ? `${node.label.slice(0, 19)}…` : node.label;
        const below = y > height * 0.55;
        ctx.strokeText(text, x, below ? y + r + 13 : y - r - 7);
        ctx.fillText(text, x, below ? y + r + 13 : y - r - 7);
      }

      screen.push({ id: node.id, x, y, r: Math.max(r * 4, 10) });
    }
    screenRef.current = screen;
  }, []);

  // ── Animation loop ──────────────────────────────────────────────────
  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;
      // Ease the camera toward the target so drags feel damped.
      const t = target.current;
      const c = cam.current;
      const ease = 1 - Math.pow(0.0015, dt);
      c.yaw += (t.yaw - c.yaw) * ease;
      c.pitch += (t.pitch - c.pitch) * ease;
      c.distance += (t.distance - c.distance) * ease;
      c.pan.x += (t.pan.x - c.pan.x) * ease;
      c.pan.y += (t.pan.y - c.pan.y) * ease;
      if (spin.current && !drag.current && !reducedRef.current) {
        t.yaw += dt * 0.12;
        c.yaw = t.yaw;
      }
      draw();
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [draw]);

  useEffect(() => {
    resize();
    const wrap = wrapRef.current;
    if (!wrap) return;
    const observer = new ResizeObserver(resize);
    observer.observe(wrap);
    return () => observer.disconnect();
  }, [resize]);

  // Stop the idle spin while the user is interacting.
  useEffect(() => {
    const onInteract = () => { spin.current = false; };
    window.addEventListener("pointerdown", onInteract, { once: true });
    window.addEventListener("wheel", onInteract, { once: true, passive: true });
    return () => {
      window.removeEventListener("pointerdown", onInteract);
      window.removeEventListener("wheel", onInteract);
    };
  }, []);

  const hitTest = useCallback((clientX: number, clientY: number): string | null => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    let best: { id: string; dist: number } | null = null;
    for (const point of screenRef.current) {
      const dist = Math.hypot(point.x - x, point.y - y);
      if (dist <= point.r && (!best || dist < best.dist)) best = { id: point.id, dist };
    }
    return best?.id ?? null;
  }, []);

  const onPointerDown = (event: React.PointerEvent) => {
    (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
    if (event.pointerType === "touch") {
      touches.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
      // Two fingers down starts a pinch; record the current spread and distance.
      if (touches.current.size === 2) {
        const [a, b] = [...touches.current.values()];
        pinch.current = { dist: Math.hypot(a.x - b.x, a.y - b.y), distance: target.current.distance };
        drag.current = null;
      }
      return;
    }
    // Right/middle button, or shift held, pans; plain left drag orbits.
    const mode: "orbit" | "pan" = event.button !== 0 || event.shiftKey ? "pan" : "orbit";
    drag.current = {
      x: event.clientX,
      y: event.clientY,
      yaw: target.current.yaw,
      pitch: target.current.pitch,
      panX: target.current.pan.x,
      panY: target.current.pan.y,
      mode,
    };
  };

  const onPointerMove = (event: React.PointerEvent) => {
    if (event.pointerType === "touch") {
      if (!touches.current.has(event.pointerId)) return;
      touches.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
      const p = pinch.current;
      if (p && touches.current.size === 2) {
        const [a, b] = [...touches.current.values()];
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        if (p.dist > 0) {
          // Spreading fingers apart zooms in (distance decreases).
          target.current.distance = Math.max(
            180,
            Math.min(1400, p.distance * (p.dist / Math.max(dist, 1))),
          );
        }
      }
      return;
    }
    const d = drag.current;
    if (!d) {
      setHovered(hitTest(event.clientX, event.clientY));
      return;
    }
    if (d.mode === "pan") {
      // Screen-space pan, scaled by the current zoom so it tracks the cursor 1:1.
      const k = cam.current.distance / 400;
      target.current.pan.x = d.panX + (event.clientX - d.x) * k;
      target.current.pan.y = d.panY - (event.clientY - d.y) * k;
    } else {
      target.current.yaw = d.yaw + (event.clientX - d.x) * 0.006;
      // Clamp pitch so the disc never flips past the poles.
      const pitch = d.pitch + (event.clientY - d.y) * 0.005;
      target.current.pitch = Math.max(-1.35, Math.min(1.35, pitch));
    }
  };

  const onPointerUp = (event: React.PointerEvent) => {
    if (event.pointerType === "touch") {
      touches.current.delete(event.pointerId);
      if (touches.current.size < 2) pinch.current = null;
      return;
    }
    const d = drag.current;
    drag.current = null;
    if (!d || d.mode !== "orbit") return;
    // A click (little movement) selects; a drag just orbits.
    const moved = Math.hypot(event.clientX - d.x, event.clientY - d.y);
    if (moved < 4) {
      const id = hitTest(event.clientX, event.clientY);
      if (id) {
        const node = byIdRef.current.get(id);
        if (node) onSelect(node);
      }
    }
  };

  // React registers `wheel` passively, so preventDefault needs a native
  // listener — otherwise zooming also scrolls the whole page.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onNativeWheel = (event: WheelEvent) => {
      event.preventDefault();
      target.current.distance = Math.max(
        180,
        Math.min(1400, target.current.distance * (event.deltaY > 0 ? 1.1 : 0.9)),
      );
      spin.current = false;
    };
    canvas.addEventListener("wheel", onNativeWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onNativeWheel);
  }, []);

  const reset = () => {
    target.current = freshCam();
    cam.current = freshCam();
    // Re-derive the fit so the reset lands on the framed distance, not 520.
    fitRef.current = extent * 2.4;
    const framed = Math.max(180, Math.min(1400, fitRef.current));
    target.current.distance = framed;
    cam.current.distance = framed;
    spin.current = !reduced;
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    const step = 0.16;
    if (event.key === "ArrowLeft") target.current.yaw -= step;
    else if (event.key === "ArrowRight") target.current.yaw += step;
    else if (event.key === "ArrowUp") target.current.pitch = Math.min(1.35, target.current.pitch + step);
    else if (event.key === "ArrowDown") target.current.pitch = Math.max(-1.35, target.current.pitch - step);
    else if (event.key === "Enter" || event.key === " ") {
      if (selectedId) {
        const node = byId.get(selectedId);
        if (node) onSelect(node);
      }
      return;
    } else return;
    event.preventDefault();
    spin.current = false;
  };

  const activeKinds = useMemo(() => {
    const s = new Set<string>();
    for (const n of nodes) {
      if (n.kind) s.add(n.kind);
    }
    return s.size > 0 ? Array.from(s) : (Object.keys(KIND_COLOR) as StarKind[]);
  }, [nodes]);

  return (
    <div className="civ-constellation civ-constellation-3d" ref={wrapRef}>
      <canvas
        ref={canvasRef}
        className="civ-constellation-canvas"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => { drag.current = null; setHovered(null); }}
        onContextMenu={e => e.preventDefault()}
        onKeyDown={onKeyDown}
        role="application"
        tabIndex={0}
        aria-label={`Constelação 3D com ${placed.length} nós. Arraste para orbitar, botão direito ou Shift+arrastar para deslocar, roda ou pinça para aproximar, setas do teclado para girar.`}
      />

      <div className="civ-constellation-hint">
        Arraste = orbitar · Scroll = zoom · Shift/Botão direito = mover · Clique = selecionar
      </div>

      <div className="civ-constellation-legend">
        {activeKinds.map(kind => {
          const [r, g, b] = colorOf(kind);
          return (
            <span key={kind} className="civ-legend-item">
              <i style={{ background: `rgb(${r},${g},${b})` }} />
              {kind}
            </span>
          );
        })}
      </div>

      <div className="civ-constellation-controls">
        <button type="button" onClick={() => { target.current.distance = Math.max(180, target.current.distance / 1.2); spin.current = false; }} aria-label="Aproximar">+</button>
        <button type="button" onClick={() => { target.current.distance = Math.min(1400, target.current.distance * 1.2); spin.current = false; }} aria-label="Afastar">−</button>
        <button type="button" onClick={reset} aria-label="Reenquadrar">⌖</button>
      </div>
    </div>
  );
}
