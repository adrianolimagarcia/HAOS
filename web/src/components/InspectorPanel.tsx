import { useState, useEffect } from "react";
import { Cpu, Copy, Check } from "lucide-react";
import { api } from "@/lib/api";
import { copyTextToClipboard } from "@/lib/clipboard";

export interface LiveLogItem {
  id: string;
  time: string;
  tag: string;
  message: string;
}

interface InspectorPanelProps {
  sessionId?: string;
  profile?: string;
  cost?: number;
  skillsCount?: number;
  liveLogs?: LiveLogItem[];
  className?: string;
}

export function InspectorPanel({
  sessionId = "chat_9f3c7a2d9b1e",
  profile,
  cost = 0.0,
  skillsCount = 12,
  liveLogs = [],
  className = "",
}: InspectorPanelProps) {
  const [activeModel, setActiveModel] = useState<string>("local · deepseek-v4-flash");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let isMounted = true;
    if (typeof api?.getModelInfo === "function") {
      api
        .getModelInfo(profile)
        .then((res) => {
          if (!isMounted) return;
          if (res?.model) {
            const prov = res.provider ? `${res.provider} · ` : "local · ";
            const modelName = String(res.model).split("/").pop() || String(res.model);
            setActiveModel(`${prov}${modelName}`);
          }
        })
        .catch(() => {
          // Fallback mantém valor default
        });
    }
    return () => {
      isMounted = false;
    };
  }, [profile]);

  const copySessionId = () => {
    if (!sessionId) return;
    void copyTextToClipboard(sessionId);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const defaultLogs: LiveLogItem[] = [
    {
      id: "1",
      time: "10:42:11",
      tag: `[${activeModel.split("·").pop()?.trim() || "model"}]`,
      message: "Loading model...",
    },
    {
      id: "2",
      time: "10:42:13",
      tag: `[${activeModel.split("·").pop()?.trim() || "model"}]`,
      message: "Model loaded successfully",
    },
    {
      id: "3",
      time: "10:42:14",
      tag: "[tool]",
      message: "read_file(/etc/hosts)",
    },
  ];

  const logsToRender = liveLogs.length > 0 ? liveLogs : defaultLogs;

  return (
    <aside
      className={`flex flex-col gap-5 p-5 w-72 shrink-0 rounded-2xl border border-white/80 bg-white/75 backdrop-blur-xl shadow-[0_10px_30px_-15px_rgba(15,23,42,0.12)] text-[#1c1c1e] ${className}`}
      aria-label="Inspector"
    >
      {/* Title */}
      <div className="font-semibold text-base tracking-tight text-[#1c1c1e]">
        Inspector
      </div>

      {/* SECTION: MODEL */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-1.5 text-[0.72rem] font-bold tracking-wider text-[#8e8e93] uppercase">
          <Cpu className="h-3.5 w-3.5 text-[#0a84ff]" />
          <span>MODEL</span>
        </div>
        <div className="text-sm font-medium text-[#1c1c1e] pl-0.5">
          {activeModel}
        </div>
      </div>

      {/* SECTION: SESSION ID */}
      <div className="flex flex-col gap-1.5">
        <div className="text-[0.72rem] font-bold tracking-wider text-[#8e8e93] uppercase">
          SESSION ID
        </div>
        <div
          onClick={copySessionId}
          className="group flex items-center justify-between cursor-pointer rounded-lg px-2 py-1 -ml-2 hover:bg-black/5 transition-colors"
          title="Clique para copiar o Session ID"
        >
          <span className="font-mono text-sm text-[#1c1c1e] truncate">
            {sessionId}
          </span>
          <button
            type="button"
            className="text-[#8e8e93] group-hover:text-[#1c1c1e] transition-colors p-1"
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-emerald-600" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </button>
        </div>
      </div>

      {/* SECTION: COST */}
      <div className="flex flex-col gap-1.5">
        <div className="text-[0.72rem] font-bold tracking-wider text-[#8e8e93] uppercase">
          COST
        </div>
        <div className="text-sm font-semibold text-emerald-600">
          ${cost.toFixed(2)}
        </div>
      </div>

      {/* SECTION: SKILLS */}
      <div className="flex flex-col gap-1.5">
        <div className="text-[0.72rem] font-bold tracking-wider text-[#8e8e93] uppercase">
          SKILLS
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center justify-center rounded-full bg-black/5 px-2.5 py-0.5 text-xs font-semibold text-[#1c1c1e]">
            {skillsCount}
          </span>
          <span className="inline-flex items-center justify-center rounded-full bg-black/5 px-2.5 py-0.5 text-xs font-semibold text-[#1c1c1e]">
            {skillsCount}
          </span>
        </div>
      </div>

      {/* SECTION: LIVE LOG */}
      <div className="flex flex-col gap-2 pt-1 border-t border-black/5 flex-1 min-h-0">
        <div className="text-[0.72rem] font-bold tracking-wider text-[#8e8e93] uppercase">
          LIVE LOG
        </div>
        <div className="flex flex-col gap-1.5 text-xs font-mono text-[#3a3a3c] overflow-y-auto pr-1">
          {logsToRender.map((log) => (
            <div key={log.id} className="leading-tight flex flex-col gap-0.5">
              <span className="text-[0.68rem] text-[#8e8e93]">
                {log.time} <span className="text-[#0a84ff]">{log.tag}</span>
              </span>
              <span className="text-[#1c1c1e] break-all">{log.message}</span>
            </div>
          ))}
        </div>
      </div>
    </aside>
  );
}
