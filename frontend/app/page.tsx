"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, sendMessage } from "@/lib/api";

interface Message {
  role: "user" | "assistant";
  content: string;
  toolTrajectory?: string[];
  circuitBreakerTripped?: boolean;
}

const SESSION_STORAGE_KEY = "aetheris_session_id";

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollAnchorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setSessionId(sessionStorage.getItem(SESSION_STORAGE_KEY));
  }, []);

  useEffect(() => {
    scrollAnchorRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  async function handleSend() {
    const trimmed = input.trim();
    if (!trimmed || isLoading) return;

    setMessages((prev) => [...prev, { role: "user", content: trimmed }]);
    setInput("");
    setIsLoading(true);
    setError(null);

    try {
      const response = await sendMessage(trimmed, sessionId);
      setSessionId(response.session_id);
      sessionStorage.setItem(SESSION_STORAGE_KEY, response.session_id);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: response.reply,
          toolTrajectory: response.tool_trajectory,
          circuitBreakerTripped: response.circuit_breaker_tripped,
        },
      ]);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? `Request failed (${err.status}): ${err.message}`
          : err instanceof Error
            ? err.message
            : "Something went wrong.";
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }

  function handleNewConversation() {
    setMessages([]);
    setSessionId(null);
    setError(null);
    sessionStorage.removeItem(SESSION_STORAGE_KEY);
  }

  return (
    <div className="mx-auto flex h-dvh w-full max-w-2xl flex-col">
      <header className="flex items-center justify-between border-b border-black/10 px-4 py-3 dark:border-white/10">
        <div>
          <h1 className="text-lg font-semibold">Aetheris</h1>
          <p className="text-xs text-black/50 dark:text-white/50">
            Multi-tenant agentic issue-resolution engine
          </p>
        </div>
        <button
          onClick={handleNewConversation}
          className="rounded-md border border-black/10 px-3 py-1.5 text-xs font-medium hover:bg-black/5 dark:border-white/10 dark:hover:bg-white/10"
        >
          New conversation
        </button>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-6">
        {messages.length === 0 && (
          <p className="mt-8 text-center text-sm text-black/40 dark:text-white/40">
            Ask about an order, a refund, or anything else — try &ldquo;Where
            is my order A-2002?&rdquo;
          </p>
        )}

        {messages.map((message, index) => (
          <div
            key={index}
            className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div className="max-w-[80%]">
              <div
                className={`rounded-2xl px-4 py-2 text-sm whitespace-pre-wrap ${
                  message.role === "user"
                    ? "bg-blue-600 text-white"
                    : "bg-black/5 dark:bg-white/10"
                }`}
              >
                {message.content}
              </div>
              {message.role === "assistant" &&
                message.toolTrajectory &&
                message.toolTrajectory.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {message.toolTrajectory.map((tool, toolIndex) => (
                      <span
                        key={toolIndex}
                        className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300"
                      >
                        🔧 {tool}
                      </span>
                    ))}
                    {message.circuitBreakerTripped && (
                      <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
                        ⚠️ escalated to human
                      </span>
                    )}
                  </div>
                )}
            </div>
          </div>
        ))}

        {isLoading && (
          <div className="flex justify-start">
            <div className="rounded-2xl bg-black/5 px-4 py-2 text-sm text-black/50 dark:bg-white/10 dark:text-white/50">
              Aetheris is thinking…
            </div>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            {error}
          </div>
        )}

        <div ref={scrollAnchorRef} />
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void handleSend();
        }}
        className="flex gap-2 border-t border-black/10 p-3 dark:border-white/10"
      >
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Type a message…"
          disabled={isLoading}
          className="flex-1 rounded-full border border-black/10 bg-transparent px-4 py-2 text-sm outline-none focus:border-blue-500 disabled:opacity-50 dark:border-white/10"
        />
        <button
          type="submit"
          disabled={isLoading || !input.trim()}
          className="rounded-full bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          Send
        </button>
      </form>
    </div>
  );
}
