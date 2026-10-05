import { UserProfile, CalcResponse } from "./types";

export class ApiClient {
  private baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
  }

  async fetchUser(id: string): Promise<UserProfile> {
    return { id, name: "Alice", active: true };
  }

  async submitCalc(value: number): Promise<CalcResponse> {
    return { result: value * 2, status: "completed" };
  }
}

export function createDefaultClient(): ApiClient {
  return new ApiClient("https://api.internal");
}
