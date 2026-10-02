import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  RotateCcw,
  Sliders,
  Play,
  Pause,
  Compass,
} from "lucide-react";
import "./ObsidianGraphView.css";

export interface GraphNode {
  id: string;
  kind: string;
  label: string;
  group?: string;
  status?: string;
  score?: number;
  degree?: number;
  meta?: Record<string, any>;
  // Physics simulation state (world coordinates)
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  directed?: boolean;
}

export interface GroupConfig {
  name: string;
  color: string;
  count: number;
  enabled: boolean;
}

const DEFAULT_GROUP_COLORS: Record<string, string> = {
  "Obsidian Notes": "#ef4444",      // Obsidian Red
  "GraphRAG Knowledge": "#10b981",  // Emerald Green
  "Canonical DB": "#38bdf8",        // Cyan
  "Graphify Code AST": "#f59e0b",   // Amber Orange
  "Memory Providers": "#ec4899",    // Pink
  "Scopes & Nexus": "#a855f7",      // Purple
  "Hindsight: World Facts": "#0ea5e9",    // Sky Blue
  "Hindsight: Experiences": "#f43f5e",    // Rose
  "Hindsight: Observations": "#10b981",   // Emerald
  "Hindsight: Mental Models": "#8b5cf6",  // Violet
  "World Facts": "#0ea5e9",
  "Experiences": "#f43f5e",
  "Observations": "#10b981",
  "Mental Models": "#8b5cf6",
  "Code: Memory": "#10b981",        // Emerald
  "Code: Skills": "#f59e0b",        // Amber
  "Code: Execution": "#ef4444",     // Red
  "Code: WebUI": "#3b82f6",         // Blue
  "Code: Observability": "#8b5cf6", // Purple
  "Code: Tasks": "#ec4899",         // Pink
  "Code: Capabilities": "#06b6d4",  // Cyan
  "Code: Core": "#94a3b8",          // Slate
  "Agent Mesh": "#06b6d4",          // Electric Cyan
  "Agent Capabilities": "#ec4899",  // Vibrant Pink
  "Agent Events": "#f59e0b",        // Amber
  "General": "#94a3b8",             // Slate Neutral
};

function getGroupColor(group: string): string {
  if (DEFAULT_GROUP_COLORS[group]) return DEFAULT_GROUP_COLORS[group];
  const PALETTE = ["#f43f5e", "#8b5cf6", "#06b6d4", "#10b981", "#f59e0b", "#ec4899", "#3b82f6", "#14b8a6", "#a855f7"];
  let hash = 0;
  for (let i = 0; i < group.length; i++) {
    hash = (hash << 5) - hash + group.charCodeAt(i);
    hash |= 0;
  }
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

interface ObsidianGraphViewProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  view?: string;
  className?: string;
}

export function ObsidianGraphView({
  nodes,
  edges,
  selectedId,
  onSelect,
  className = "",
}: ObsidianGraphViewProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // --- Forces & Physics Settings ---
  const [centerForce, setCenterForce] = useState(0.28);
  const [repelForce, setRepelForce] = useState(240); // Generous default spacing!
  const [linkForce, setLinkForce] = useState(0.35);
  const [linkDistance, setLinkDistance] = useState(90); // Long spring links!
  const [isAnimated, setIsAnimated] = useState(true);

  // --- Display Settings ---
  const [showArrows, setShowArrows] = useState(false);
  const [textFadeThreshold, setTextFadeThreshold] = useState(0.85);
  const [nodeScale, setNodeScale] = useState(1.0);
  const [linkThickness, setLinkThickness] = useState(0.9);

  // --- UI Floating Panel State ---
  const [isPanelCollapsed, setIsPanelCollapsed] = useState(false);
  const [groupsOpen, setGroupsOpen] = useState(true);
  const [displayOpen, setDisplayOpen] = useState(true);
  const [forcesOpen, setForcesOpen] = useState(true);

  // Groups toggle state
  const [groupFilters, setGroupFilters] = useState<Record<string, boolean>>({});

  // Camera viewport transform: pan (x, y) & zoom scale
  const cameraRef = useRef({ x: 0, y: 0, zoom: 0.85 });
  const [zoomLevel, setZoomLevel] = useState(0.85);

  // Hover & Drag state
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const dragRef = useRef<{
    isDragging: boolean;
    node: GraphNode | null;
    startX: number;
    startY: number;
    camStartX: number;
    camStartY: number;
    moved: boolean;
  }>({
    isDragging: false,
    node: null,
    startX: 0,
    startY: 0,
    camStartX: 0,
    camStartY: 0,
    moved: false,
  });

  // Physics simulation data
  const simNodesRef = useRef<Map<string, GraphNode>>(new Map());
  const edgesRef = useRef<Array<{ source: GraphNode; target: GraphNode; kind: string }>>([]);

  // Compute available groups from nodes
  const groupsList = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const n of nodes) {
      const g = n.group || "General";
      counts[g] = (counts[g] || 0) + 1;
    }
    return Object.keys(counts).map(g => ({
      name: g,
      color: getGroupColor(g),
      count: counts[g],
      enabled: groupFilters[g] !== false,
    }));
  }, [nodes, groupFilters]);

  // Toggle single group visibility
  const toggleGroup = (groupName: string) => {
    setGroupFilters(prev => ({
      ...prev,
      [groupName]: prev[groupName] === false ? true : false,
    }));
  };

  // Filter visible nodes based on active groups
  const activeNodes = useMemo(() => {
    return nodes.filter(n => {
      const g = n.group || "General";
      return groupFilters[g] !== false;
    });
  }, [nodes, groupFilters]);

  const activeNodeIds = useMemo(() => {
    return new Set(activeNodes.map(n => n.id));
  }, [activeNodes]);

  // Synchronize simulation nodes without resetting positions on small changes
  useEffect(() => {
    const map = simNodesRef.current;
    const newMap = new Map<string, GraphNode>();

    const initialRadius = Math.min(window.innerWidth || 1000, 900) * 0.42;

    activeNodes.forEach((n, idx) => {
      const existing = map.get(n.id);
      if (existing) {
        existing.label = n.label;
        existing.group = n.group;
        existing.kind = n.kind;
        existing.degree = n.degree;
        newMap.set(n.id, existing);
      } else {
        // Place new node on distributed golden-ratio spiral
        const angle = idx * 2.39996; // Golden angle
        const r = Math.sqrt((idx + 1) / Math.max(activeNodes.length, 1)) * initialRadius;
        const x = Math.cos(angle) * r + (Math.random() - 0.5) * 40;
        const y = Math.sin(angle) * r + (Math.random() - 0.5) * 40;
        newMap.set(n.id, {
          ...n,
          x,
          y,
          vx: (Math.random() - 0.5) * 2,
          vy: (Math.random() - 0.5) * 2,
        });
      }
    });

    simNodesRef.current = newMap;

    // Filter and bind edges
    const boundEdges: Array<{ source: GraphNode; target: GraphNode; kind: string }> = [];
    for (const e of edges) {
      if (activeNodeIds.has(e.source) && activeNodeIds.has(e.target)) {
        const sNode = newMap.get(e.source);
        const tNode = newMap.get(e.target);
        if (sNode && tNode) {
          boundEdges.push({ source: sNode, target: tNode, kind: e.kind });
        }
      }
    }
    edgesRef.current = boundEdges;
  }, [activeNodes, edges, activeNodeIds]);

  // Reset Camera View
  const resetCamera = useCallback(() => {
    cameraRef.current = { x: 0, y: 0, zoom: 0.85 };
    setZoomLevel(0.85);
  }, []);

  // Adjacency graph for hover spotlight
  const neighborsMap = useMemo(() => {
    const adj = new Map<string, Set<string>>();
    for (const e of edges) {
      if (!adj.has(e.source)) adj.set(e.source, new Set());
      if (!adj.has(e.target)) adj.set(e.target, new Set());
      adj.get(e.source)!.add(e.target);
      adj.get(e.target)!.add(e.source);
    }
    return adj;
  }, [edges]);

  // Main Render & Physics Loop
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let animId: number;

    const render = () => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const rect = canvas.getBoundingClientRect();
      const cssWidth = rect.width;
      const cssHeight = rect.height;

      if (cssWidth === 0 || cssHeight === 0) {
        animId = requestAnimationFrame(render);
        return;
      }

      const dpr = Math.min(window.devicePixelRatio || 1, 3);
      const nodeList = Array.from(simNodesRef.current.values());
      const edgeList = edgesRef.current;

      // 1. Physics Step with Numerical Stability (Force & Speed Clamping)
      if (isAnimated && nodeList.length > 0) {
        const len = nodeList.length;

        // Repulsion (Coulomb) - Pairwise with distance cut-off
        const kRepel = repelForce * 65;
        const maxDistSq = 480 * 480;
        const MAX_FORCE = 30.0;
        const MAX_SPEED = 18.0;

        for (let i = 0; i < len; i++) {
          const na = nodeList[i];
          if (na === dragRef.current.node) continue;

          for (let j = i + 1; j < len; j++) {
            const nb = nodeList[j];
            const dx = (na.x || 0) - (nb.x || 0);
            const dy = (na.y || 0) - (nb.y || 0);
            const distSq = dx * dx + dy * dy + 100;

            if (distSq < maxDistSq) {
              const dist = Math.sqrt(distSq);
              const force = Math.min(kRepel / distSq, MAX_FORCE);
              const fx = (dx / dist) * force;
              const fy = (dy / dist) * force;

              na.vx = (na.vx || 0) + fx;
              na.vy = (na.vy || 0) + fy;
              if (nb !== dragRef.current.node) {
                nb.vx = (nb.vx || 0) - fx;
                nb.vy = (nb.vy || 0) - fy;
              }
            }
          }
        }

        // Spring Attraction along edges (Hooke's law with Force Cap)
        const targetLen = linkDistance;
        const kSpring = linkForce * 0.035;

        for (let i = 0; i < edgeList.length; i++) {
          const e = edgeList[i];
          const s = e.source;
          const t = e.target;
          const dx = (s.x || 0) - (t.x || 0);
          const dy = (s.y || 0) - (t.y || 0);
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const delta = dist - targetLen;
          const rawForce = delta * kSpring;
          const force = Math.max(Math.min(rawForce, MAX_FORCE), -MAX_FORCE);

          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;

          if (s !== dragRef.current.node) {
            s.vx = (s.vx || 0) - fx;
            s.vy = (s.vy || 0) - fy;
          }
          if (t !== dragRef.current.node) {
            t.vx = (t.vx || 0) + fx;
            t.vy = (t.vy || 0) + fy;
          }
        }

        // Center Gravity pull & Clamping
        const kCenter = centerForce * 0.003;
        for (let i = 0; i < len; i++) {
          const n = nodeList[i];
          if (n === dragRef.current.node) continue;
          const x = n.x || 0;
          const y = n.y || 0;
          const d = Math.sqrt(x * x + y * y) || 1;
          n.vx = (n.vx || 0) - (x / d) * (d * kCenter);
          n.vy = (n.vy || 0) - (y / d) * (d * kCenter);

          // Damping
          n.vx = (n.vx || 0) * 0.82;
          n.vy = (n.vy || 0) * 0.82;

          // Velocity clamping: prevents nodes from flying away to infinity
          const speed = Math.sqrt((n.vx || 0) * (n.vx || 0) + (n.vy || 0) * (n.vy || 0));
          if (speed > MAX_SPEED) {
            n.vx = ((n.vx || 0) / speed) * MAX_SPEED;
            n.vy = ((n.vy || 0) / speed) * MAX_SPEED;
          }

          // Safe finite check
          if (!Number.isFinite(n.x) || !Number.isFinite(n.y)) {
            n.x = (Math.random() - 0.5) * 200;
            n.y = (Math.random() - 0.5) * 200;
            n.vx = 0;
            n.vy = 0;
          } else {
            n.x = (n.x || 0) + n.vx;
            n.y = (n.y || 0) + n.vy;
          }
        }
      }

      // 2. Clear Screen & Prepare Camera with DPR Transform
      if (typeof ctx.setTransform === "function") {
        ctx.setTransform(1, 0, 0, 1, 0, 0);
      }
      ctx.fillStyle = "#0d0e15"; // Deep obsidian background
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      if (typeof ctx.scale === "function") {
        ctx.scale(dpr, dpr);
      }

      ctx.save();
      const { x: camX, y: camY, zoom } = cameraRef.current;
      // Center of canvas as camera origin in CSS pixels (matches screenToWorld 1:1)
      ctx.translate(cssWidth / 2 + camX, cssHeight / 2 + camY);
      ctx.scale(zoom, zoom);

      // Active spotlight set
      const hoveredId = hoveredNodeId || selectedId;
      const isSpotlightActive = !!hoveredId;
      const spotlightNeighbors = hoveredId ? neighborsMap.get(hoveredId) : null;

      // 3. Render Edges
      for (let i = 0; i < edgeList.length; i++) {
        const e = edgeList[i];
        const s = e.source;
        const t = e.target;
        if (s.x === undefined || t.x === undefined) continue;

        const isConnected =
          isSpotlightActive &&
          (s.id === hoveredId || t.id === hoveredId);

        let strokeStyle = "rgba(148, 163, 184, 0.16)";
        let lineWidth = linkThickness;

        if (isSpotlightActive) {
          if (isConnected) {
            strokeStyle = "rgba(192, 132, 252, 0.75)";
            lineWidth = linkThickness * 1.8;
          } else {
            strokeStyle = "rgba(148, 163, 184, 0.04)";
          }
        }

        ctx.strokeStyle = strokeStyle;
        ctx.lineWidth = lineWidth;
        ctx.beginPath();
        ctx.moveTo(s.x, s.y || 0);
        ctx.lineTo(t.x, t.y || 0);
        ctx.stroke();

        // Optional Arrows
        if (showArrows && isConnected) {
          const midX = (s.x + t.x) / 2;
          const midY = ((s.y || 0) + (t.y || 0)) / 2;
          const angle = Math.atan2((t.y || 0) - (s.y || 0), t.x - s.x);
          const arrowSize = 6 * linkThickness;

          ctx.fillStyle = strokeStyle;
          ctx.beginPath();
          ctx.moveTo(midX, midY);
          ctx.lineTo(
            midX - arrowSize * Math.cos(angle - Math.PI / 6),
            midY - arrowSize * Math.sin(angle - Math.PI / 6)
          );
          ctx.lineTo(
            midX - arrowSize * Math.cos(angle + Math.PI / 6),
            midY - arrowSize * Math.sin(angle + Math.PI / 6)
          );
          ctx.closePath();
          ctx.fill();
        }
      }

      // 4. Render Nodes
      const shouldDrawLabels = zoom >= textFadeThreshold;

      for (let i = 0; i < nodeList.length; i++) {
        const n = nodeList[i];
        if (n.x === undefined || n.y === undefined) continue;

        const isHovered = n.id === hoveredId;
        const isNeighbor =
          isSpotlightActive && spotlightNeighbors?.has(n.id);
        const isDimmed = isSpotlightActive && !isHovered && !isNeighbor;

        const groupColor = getGroupColor(n.group || "General");

        // Base Radius based on degree / kind
        const baseRadius =
          (n.kind === "fabric"
            ? 10
            : n.kind === "projection" || n.kind === "store"
            ? 8.5
            : 4.5 + Math.min(Math.sqrt(n.degree || 0) * 1.8, 6)) * nodeScale;

        const drawRadius = isHovered ? baseRadius * 1.5 : baseRadius;

        ctx.save();
        ctx.globalAlpha = isDimmed ? 0.15 : 1.0;

        // Outer glow on hover or hubs
        if (isHovered || n.kind === "fabric") {
          const gradient = ctx.createRadialGradient(
            n.x,
            n.y,
            drawRadius * 0.5,
            n.x,
            n.y,
            drawRadius * 3.5
          );
          gradient.addColorStop(0, groupColor);
          gradient.addColorStop(1, "transparent");
          ctx.fillStyle = gradient;
          ctx.beginPath();
          ctx.arc(n.x, n.y, drawRadius * 3.5, 0, Math.PI * 2);
          ctx.fill();
        }

        // Star core
        ctx.fillStyle = groupColor;
        ctx.beginPath();
        ctx.arc(n.x, n.y, drawRadius, 0, Math.PI * 2);
        ctx.fill();

        // White nucleus
        ctx.fillStyle = "#ffffff";
        ctx.beginPath();
        ctx.arc(n.x, n.y, drawRadius * 0.45, 0, Math.PI * 2);
        ctx.fill();

        // 5. Node Label (LOD fade or spotlight)
        if (isHovered || isNeighbor || shouldDrawLabels) {
          ctx.font = `${isHovered ? "bold 12px" : "10px"} Inter, system-ui, sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";

          const text = n.label || n.id;
          const textY = n.y + drawRadius + 12;

          // Background pill for label legibility
          const textMetrics = ctx.measureText(text);
          const bgPadding = 5;
          ctx.fillStyle = "rgba(13, 14, 21, 0.88)";
          ctx.fillRect(
            n.x - textMetrics.width / 2 - bgPadding,
            textY - 7,
            textMetrics.width + bgPadding * 2,
            14
          );

          ctx.fillStyle = isHovered ? "#ffffff" : isDimmed ? "rgba(226, 232, 240, 0.3)" : "#e2e8f0";
          ctx.fillText(text, n.x, textY);
        }

        ctx.restore();
      }

      ctx.restore();

      animId = requestAnimationFrame(render);
    };

    animId = requestAnimationFrame(render);
    return () => cancelAnimationFrame(animId);
  }, [
    isAnimated,
    centerForce,
    repelForce,
    linkForce,
    linkDistance,
    showArrows,
    textFadeThreshold,
    nodeScale,
    linkThickness,
    hoveredNodeId,
    selectedId,
    neighborsMap,
  ]);

  // Resize Observer
  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const resize = () => {
      const rect = container.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 3);
      canvas.width = Math.round(rect.width * dpr);
      canvas.height = Math.round(rect.height * dpr);
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
    };

    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  // Screen to World coordinates transform
  const screenToWorld = useCallback((screenX: number, screenY: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    const clientX = screenX - rect.left;
    const clientY = screenY - rect.top;
    const { x: camX, y: camY, zoom } = cameraRef.current;
    const worldX = (clientX - rect.width / 2 - camX) / zoom;
    const worldY = (clientY - rect.height / 2 - camY) / zoom;
    return { x: worldX, y: worldY };
  }, []);

  // Precise Hit Testing Helper
  const findNodeAt = useCallback(
    (wx: number, wy: number): GraphNode | null => {
      const nodeList = Array.from(simNodesRef.current.values());
      const zoom = Math.max(cameraRef.current.zoom, 0.1);
      let closestNode: GraphNode | null = null;
      let closestDistSq = Infinity;

      for (let i = 0; i < nodeList.length; i++) {
        const n = nodeList[i];
        if (n.x === undefined || n.y === undefined) continue;
        const dx = n.x - wx;
        const dy = n.y - wy;
        const distSq = dx * dx + dy * dy;

        const baseRadius =
          (n.kind === "fabric"
            ? 10
            : n.kind === "projection" || n.kind === "store"
            ? 8.5
            : 4.5 + Math.min(Math.sqrt(n.degree || 0) * 1.8, 6)) * nodeScale;

        // Allow 12px comfort hit margin in screen pixels, converted to world coordinates
        const hitMargin = 12 / zoom;
        const hitRadius = baseRadius + hitMargin;

        if (distSq <= hitRadius * hitRadius && distSq < closestDistSq) {
          closestDistSq = distSq;
          closestNode = n;
        }
      }
      return closestNode;
    },
    [nodeScale]
  );

  // Pointer Interaction Handlers
  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (typeof e.currentTarget.setPointerCapture === "function") {
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        // Ignored in non-browser or mock environments
      }
    }
    const { x: wx, y: wy } = screenToWorld(e.clientX, e.clientY);
    const hitNode = findNodeAt(wx, wy);

    dragRef.current = {
      isDragging: true,
      node: hitNode,
      startX: e.clientX,
      startY: e.clientY,
      camStartX: cameraRef.current.x,
      camStartY: cameraRef.current.y,
      moved: false,
    };
  };

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;

    if (drag.isDragging) {
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
        drag.moved = true;
      }

      if (drag.node) {
        // Dragging single node
        const { x: wx, y: wy } = screenToWorld(e.clientX, e.clientY);
        drag.node.x = wx;
        drag.node.y = wy;
        drag.node.vx = 0;
        drag.node.vy = 0;
      } else {
        // Panning camera
        cameraRef.current.x = drag.camStartX + dx;
        cameraRef.current.y = drag.camStartY + dy;
      }
    } else {
      // Hover hit test
      const { x: wx, y: wy } = screenToWorld(e.clientX, e.clientY);
      const hitNode = findNodeAt(wx, wy);
      setHoveredNodeId(hitNode ? hitNode.id : null);
    }
  };

  const onPointerUp = (_e: React.PointerEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    if (drag.isDragging) {
      if (!drag.moved) {
        // It was a click!
        if (drag.node) {
          onSelect(drag.node.id);
        } else {
          onSelect(null);
        }
      }
      drag.isDragging = false;
      drag.node = null;
    }
  };

  // Wheel Zoom
  const onWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.15 : 0.87;
    const nextZoom = Math.min(Math.max(0.1, cameraRef.current.zoom * factor), 5.0);

    const { x: wx, y: wy } = screenToWorld(e.clientX, e.clientY);
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const clientX = e.clientX - rect.left - rect.width / 2;
    const clientY = e.clientY - rect.top - rect.height / 2;

    cameraRef.current.x = clientX - wx * nextZoom;
    cameraRef.current.y = clientY - wy * nextZoom;
    cameraRef.current.zoom = nextZoom;
    setZoomLevel(nextZoom);
  };

  return (
    <div
      ref={containerRef}
      className={`obs-graph-container ${className}`.trim()}
      role="application"
      aria-label="Obsidian Force-Directed Memory Graph"
    >
      <canvas
        ref={canvasRef}
        className="obs-graph-canvas"
        style={{
          cursor: dragRef.current.isDragging ? "grabbing" : hoveredNodeId ? "pointer" : "grab",
        }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
        onWheel={onWheel}
        onContextMenu={e => e.preventDefault()}
        tabIndex={0}
      />

      {/* Floating Obsidian-Style Controls Panel (Left) */}
      <div className={`obs-control-panel ${isPanelCollapsed ? "is-collapsed" : ""}`}>
        <div className="obs-panel-header">
          <button
            type="button"
            className="obs-panel-toggle"
            onClick={() => setIsPanelCollapsed(!isPanelCollapsed)}
            aria-label={isPanelCollapsed ? "Expandir painel" : "Recolher painel"}
          >
            {isPanelCollapsed ? <Sliders size={16} /> : <Compass size={16} />}
            <span>Graph view</span>
            {isPanelCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
          </button>
          {!isPanelCollapsed && (
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <span style={{ fontSize: "0.7rem", color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}>
                {Math.round(zoomLevel * 100)}%
              </span>
              <button
                type="button"
                className="obs-btn-icon"
                onClick={resetCamera}
                title="Centralizar Câmera"
                aria-label="Centralizar Câmera"
              >
                <RotateCcw size={13} />
              </button>
            </div>
          )}
        </div>

        {!isPanelCollapsed && (
          <div className="obs-panel-body">
            {/* Section 1: Groups */}
            <div className="obs-accordion-section">
              <button
                type="button"
                className="obs-section-header"
                onClick={() => setGroupsOpen(!groupsOpen)}
              >
                {groupsOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <span>Groups</span>
              </button>
              {groupsOpen && (
                <div className="obs-group-list">
                  {groupsList.map(grp => (
                    <div
                      key={grp.name}
                      className={`obs-group-item ${!grp.enabled ? "is-disabled" : ""}`}
                      onClick={() => toggleGroup(grp.name)}
                    >
                      <span
                        className="obs-group-dot"
                        style={{ background: grp.color }}
                      />
                      <span className="obs-group-name">{grp.name}</span>
                      <span className="obs-group-count">{grp.count}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Section 2: Display */}
            <div className="obs-accordion-section">
              <button
                type="button"
                className="obs-section-header"
                onClick={() => setDisplayOpen(!displayOpen)}
              >
                {displayOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <span>Display</span>
              </button>
              {displayOpen && (
                <div className="obs-settings-list">
                  <div className="obs-setting-row">
                    <label>Arrows</label>
                    <input
                      type="checkbox"
                      checked={showArrows}
                      onChange={e => setShowArrows(e.target.checked)}
                      className="obs-toggle-checkbox"
                    />
                  </div>

                  <div className="obs-setting-row">
                    <label>Text fade threshold</label>
                    <input
                      type="range"
                      min="0.3"
                      max="2.0"
                      step="0.05"
                      value={textFadeThreshold}
                      onChange={e => setTextFadeThreshold(parseFloat(e.target.value))}
                      className="obs-slider"
                    />
                  </div>

                  <div className="obs-setting-row">
                    <label>Node size</label>
                    <input
                      type="range"
                      min="0.5"
                      max="2.5"
                      step="0.1"
                      value={nodeScale}
                      onChange={e => setNodeScale(parseFloat(e.target.value))}
                      className="obs-slider"
                    />
                  </div>

                  <div className="obs-setting-row">
                    <label>Link thickness</label>
                    <input
                      type="range"
                      min="0.4"
                      max="2.5"
                      step="0.1"
                      value={linkThickness}
                      onChange={e => setLinkThickness(parseFloat(e.target.value))}
                      className="obs-slider"
                    />
                  </div>

                  <div className="obs-setting-row-btn">
                    <button
                      type="button"
                      className={`obs-animate-btn ${isAnimated ? "is-active" : ""}`}
                      onClick={() => setIsAnimated(!isAnimated)}
                    >
                      {isAnimated ? <Pause size={13} /> : <Play size={13} />}
                      <span>{isAnimated ? "Pausar Física" : "Executar Física"}</span>
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* Section 3: Forces (Distanciamento & Spacing) */}
            <div className="obs-accordion-section">
              <button
                type="button"
                className="obs-section-header"
                onClick={() => setForcesOpen(!forcesOpen)}
              >
                {forcesOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <span>Forces</span>
              </button>
              {forcesOpen && (
                <div className="obs-settings-list">
                  <div className="obs-setting-row">
                    <div className="obs-label-group">
                      <label>Center force</label>
                      <span>{Math.round(centerForce * 100)}%</span>
                    </div>
                    <input
                      type="range"
                      min="0.02"
                      max="0.8"
                      step="0.02"
                      value={centerForce}
                      onChange={e => setCenterForce(parseFloat(e.target.value))}
                      className="obs-slider"
                    />
                  </div>

                  <div className="obs-setting-row">
                    <div className="obs-label-group">
                      <label>Repel force (Distanciamento)</label>
                      <span>{repelForce}</span>
                    </div>
                    <input
                      type="range"
                      min="60"
                      max="700"
                      step="10"
                      value={repelForce}
                      onChange={e => setRepelForce(parseInt(e.target.value, 10))}
                      className="obs-slider"
                    />
                  </div>

                  <div className="obs-setting-row">
                    <div className="obs-label-group">
                      <label>Link force</label>
                      <span>{Math.round(linkForce * 100)}%</span>
                    </div>
                    <input
                      type="range"
                      min="0.05"
                      max="0.9"
                      step="0.05"
                      value={linkForce}
                      onChange={e => setLinkForce(parseFloat(e.target.value))}
                      className="obs-slider"
                    />
                  </div>

                  <div className="obs-setting-row">
                    <div className="obs-label-group">
                      <label>Link distance (Comprimento)</label>
                      <span>{linkDistance}px</span>
                    </div>
                    <input
                      type="range"
                      min="30"
                      max="280"
                      step="5"
                      value={linkDistance}
                      onChange={e => setLinkDistance(parseInt(e.target.value, 10))}
                      className="obs-slider"
                    />
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Floating HUD Hint at bottom */}
      <div className="obs-hud-hint">
        Scroll = zoom · Arraste = mover câmera · Arraste nó = mover elemento · Clique = inspecionar
      </div>
    </div>
  );
}
