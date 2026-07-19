export interface ChatResponse {
  session_id: string;
  reply: string;
  tool_trajectory: string[];
  circuit_breaker_tripped: boolean;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function apiUrl(): string {
  const url = process.env.NEXT_PUBLIC_API_URL;
  if (!url) {
    throw new Error(
      "NEXT_PUBLIC_API_URL is not set — see frontend/.env.example",
    );
  }
  return url.replace(/\/$/, "");
}

export async function sendMessage(
  message: string,
  sessionId: string | null,
): Promise<ChatResponse> {
  const response = await fetch(`${apiUrl()}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId ?? undefined }),
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new ApiError(detail || response.statusText, response.status);
  }

  return response.json();
}
