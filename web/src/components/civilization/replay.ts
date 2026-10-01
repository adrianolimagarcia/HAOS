import type { MissionEvent } from "../../lib/civilization-contracts";

export interface ReplayState {
  nodeStatuses: Readonly<Record<string, string>>;
  missionStatus: string | null;
}

export function initialReplayState(): ReplayState {
  return { nodeStatuses: Object.create(null) as Record<string, string>, missionStatus: null };
}

/** Historical state is evidence-based: event names and agent IDs are not node IDs. */
export function replayEventReducer(state: ReplayState, event: MissionEvent): ReplayState {
  const hasNodeStatus = typeof event.node_id === "string" && event.node_id.length > 0
    && typeof event.status === "string" && event.status.length > 0;
  const hasMissionStatus = typeof event.mission_status === "string" && event.mission_status.length > 0;
  return {
    nodeStatuses: hasNodeStatus
      ? { ...state.nodeStatuses, [event.node_id!]: event.status }
      : state.nodeStatuses,
    missionStatus: hasMissionStatus ? event.mission_status! : state.missionStatus,
  };
}

export function replayPrefixLength(length: number, requested: number): number {
  return Math.min(length, Math.max(0, Number.isFinite(requested) ? Math.trunc(requested) : 0));
}

/** The received array defines history; seq and time are informational, never sorting keys. */
export function reconstructReplay(events: readonly MissionEvent[], prefixLength: number): ReplayState {
  return events.slice(0, replayPrefixLength(events.length, prefixLength))
    .reduce(replayEventReducer, initialReplayState());
}
