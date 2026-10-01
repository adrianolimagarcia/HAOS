import { useEffect, useMemo, useReducer } from "react";
import type { MissionEvent } from "../../lib/civilization-contracts";
import { reconstructReplay, replayPrefixLength } from "./replay";

export interface MissionReplayProps {
  events: readonly MissionEvent[];
  missionId?: string;
}

interface TransportState {
  position: number;
  playing: boolean;
  speed: number;
}

type TransportAction =
  | { type: "seek"; position: number }
  | { type: "play" }
  | { type: "pause" }
  | { type: "speed"; speed: number }
  | { type: "tick"; length: number };

function transportReducer(state: TransportState, action: TransportAction): TransportState {
  switch (action.type) {
    case "seek": return { ...state, position: action.position, playing: false };
    case "play": return { ...state, playing: true };
    case "pause": return { ...state, playing: false };
    case "speed": return { ...state, speed: action.speed };
    case "tick": {
      const position = replayPrefixLength(action.length, state.position + 1);
      return { ...state, position, playing: position < action.length };
    }
  }
}

export function MissionReplay(props: MissionReplayProps) {
  // A mission switch must not carry a different mission's playhead or active timer.
  return <ReplayControls key={props.missionId ?? "mission"} events={props.events} />;
}

function ReplayControls({ events }: Pick<MissionReplayProps, "events">) {
  const [transport, dispatch] = useReducer(transportReducer, { position: 0, playing: false, speed: 1 });
  const position = replayPrefixLength(events.length, transport.position);
  const playing = transport.playing && position < events.length;
  const historical = useMemo(() => reconstructReplay(events, position), [events, position]);
  const visibleEvents = events.slice(0, position);
  const nodeStates = Object.entries(historical.nodeStatuses);

  useEffect(() => {
    if (!playing) return;
    // Playback advances array prefixes, not wall-clock timestamps (which may tie or regress).
    const timer = window.setInterval(() => dispatch({ type: "tick", length: events.length }), 1000 / transport.speed);
    return () => window.clearInterval(timer);
  }, [playing, transport.speed, events.length]);

  const seek = (next: number) => dispatch({ type: "seek", position: replayPrefixLength(events.length, next) });

  return (
    <section aria-label="Mission replay" className="civ-glass" style={{ padding: 18, marginTop: 16 }}>
      <h3 className="font-semibold">Mission replay</h3>
      <div className="flex flex-wrap items-center gap-2">
        <button className="civ-btn-ghost" type="button" onClick={() => seek(0)} disabled={position === 0}>Start</button>
        <button className="civ-btn-ghost" type="button" onClick={() => seek(position - 1)} disabled={position === 0}>Previous</button>
        <button className="civ-btn-ghost" type="button" onClick={() => dispatch({ type: "play" })} disabled={playing || position === events.length}>Play</button>
        <button className="civ-btn-ghost" type="button" onClick={() => dispatch({ type: "pause" })} disabled={!playing}>Pause</button>
        <button className="civ-btn-ghost" type="button" onClick={() => seek(position + 1)} disabled={position === events.length}>Next</button>
        <button className="civ-btn-ghost" type="button" onClick={() => seek(events.length)} disabled={position === events.length}>End</button>
        <label className="flex items-center gap-2">
          Speed
          <select aria-label="Replay speed" value={transport.speed} onChange={(event) => dispatch({ type: "speed", speed: Number(event.target.value) })}>
            {[1, 2, 10].map((speed) => <option key={speed} value={speed}>{speed}×</option>)}
          </select>
        </label>
      </div>
      <label className="block">
        Event prefix: <output aria-live="polite">{position} / {events.length}</output>
        <input aria-label="Replay event prefix" className="w-full" type="range" min={0} max={events.length} step={1}
          value={position} disabled={events.length === 0} onChange={(event) => seek(Number(event.target.value))} />
      </label>
      <p>Historical mission status: {historical.missionStatus ?? "Unavailable"}</p>
      {nodeStates.length === 0
        ? <p>Historical node state unavailable: no explicit node status in this event prefix.</p>
        : <div><p>Only explicitly recorded node states are shown; other historical node state is unavailable.</p>
          <ul aria-label="Historical node states">{nodeStates.map(([id, status]) => <li key={id}>{id}: {status}</li>)}</ul>
        </div>}
      {events.length === 0 && <p>No mission events available.</p>}
      <ol aria-label="Replayed events" className="space-y-2">
        {visibleEvents.map((event, index) => <li key={index}>
          <span>#{index + 1} · {event.time} · {event.type} · {event.agent_id}</span>
          <p>{event.description}</p>
          {event.status && <span>Status: {event.status}</span>}
        </li>)}
      </ol>
    </section>
  );
}
