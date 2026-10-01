export interface AvailableModel {
  id: string;
  name: string;
  provider: string;
  context_length?: number | null;
  cost_per_1m_input_usd?: number | null;
  cost_per_1m_output_usd?: number | null;
}
export interface MissionEvent {
  time: number; type: string; agent_id: string; description: string; status: string;
  seq?: number; node_id?: string; mission_status?: string;
}
