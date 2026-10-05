export interface UserProfile {
  id: string;
  name: string;
  active: boolean;
}

export type CalcStatus = "pending" | "completed" | "failed";

export interface CalcResponse {
  result: number;
  status: CalcStatus;
}
