import { HttpAgent, type Message, type Tool } from "@ag-ui/client";
import { Button, Spinner, Text, Textarea } from "@fluentui/react-components";
import { SendRegular, SparkleRegular } from "@fluentui/react-icons";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Audience, League } from "../api";
import { usePlayback, useViewer } from "../store";
import type { PageControls } from "./MatchView";

/**
 * Chat with the match assistant over AG-UI (POST /api/chat). Every message carries the page state, most
 * importantly `as_of`: how far this viewer has watched. The server binds the assistant's data tools to it, so the
 * chat can't spoil what's coming.
 *
 * The assistant can also act on the page through frontend tools. AG-UI forwards those calls here; we run them,
 * send the result back, and let the assistant finish its answer.
 */
const FRONTEND_TOOLS: Tool[] = [
  {
    name: "seek_to",
    description: "Replay the match on the viewer's screen from just before an event. Use when the viewer asks to "
      + "see, show, rewatch or replay something.",
    parameters: { type: "object", properties: { seq: { type: "integer", description: "The event's seq" } },
                  required: ["seq"] },
  },
  {
    name: "focus_player",
    description: "Highlight one player on the pitch and follow them in the insights.",
    parameters: { type: "object", properties: { player_id: { type: "string" } }, required: ["player_id"] },
  },
  {
    name: "set_view",
    description: "Change how insights are written for the viewer.",
    parameters: { type: "object", properties: {
      audience: { type: "string", enum: ["broadcast", "analyst", "casual", "player"] },
      language: { type: "string", description: "BCP 47 tag, e.g. en, es, fr, pt, de" },
    } },
  },
];
const FRONTEND = new Set(FRONTEND_TOOLS.map((t) => t.name));
const MAX_ROUNDS = 4;

type Note = { id: string; text: string };

export function Chat({ matchId, league, controls }: { matchId: string; league: League; controls: PageControls }) {
  const { t } = useTranslation();
  const userId = useViewer((s) => s.userId);
  const agent = useMemo(() => new HttpAgent({ url: "/api/chat", threadId: `${userId}-${matchId}-${Date.now()}` }),
                        [userId, matchId]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState<{ id: string; text: string } | null>(null);
  const [notes, setNotes] = useState<Record<string, Note>>({}); // tool call id -> what happened, for display
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight }); }, [messages, streaming, notes]);
  useEffect(() => () => agent.abortRun(), [agent]);

  function pageState() {
    const v = useViewer.getState();
    return { match_id: matchId, as_of: usePlayback.getState().appliedSeq, audience: v.audience,
             language: v.language, club_id: v.clubId, player_id: v.playerId };
  }

  function runFrontendTool(name: string, args: Record<string, unknown>): string {
    const v = useViewer.getState();
    if (name === "seek_to") {
      const clock = controls.seekSeq(Number(args.seq));
      return clock ? t("chat.actions.seek", { clock }) : "That moment isn't available to replay.";
    }
    if (name === "focus_player") {
      const pid = String(args.player_id);
      if (!league.players[pid]) return `Unknown player ${pid}`;
      controls.focusPlayer(pid);
      return t("chat.actions.focus", { player: league.players[pid].name });
    }
    if (name === "set_view") {
      if (typeof args.audience === "string") v.setAudience(args.audience as Audience);
      if (typeof args.language === "string") v.setLanguage(args.language);
      return t("chat.actions.view");
    }
    return `Unknown tool ${name}`;
  }

  /** Frontend tool calls the assistant made that haven't been answered yet. */
  function pendingCalls(): { id: string; name: string; args: Record<string, unknown> }[] {
    const answered = new Set(agent.messages.filter((m) => m.role === "tool").map((m) => (m as { toolCallId: string }).toolCallId));
    return agent.messages.flatMap((m) => (m.role === "assistant" ? m.toolCalls ?? [] : []))
      .filter((c) => FRONTEND.has(c.function.name) && !answered.has(c.id))
      .map((c) => {
        let args: Record<string, unknown> = {};
        try { args = JSON.parse(c.function.arguments || "{}"); } catch { /* keep empty */ }
        return { id: c.id, name: c.function.name, args };
      });
  }

  async function send(text: string) {
    if (!text.trim() || busy) return;
    setError(null);
    setInput("");
    setBusy(true);
    agent.addMessage({ id: crypto.randomUUID(), role: "user", content: text.trim() });
    setMessages([...agent.messages]);
    try {
      for (let round = 0; round < MAX_ROUNDS; round++) {
        agent.setState(pageState());
        await agent.runAgent({ tools: FRONTEND_TOOLS }, {
          onTextMessageContentEvent: ({ event, textMessageBuffer }) => {
            setStreaming({ id: event.messageId, text: textMessageBuffer });
          },
          onToolCallEndEvent: ({ event, toolCallName }) => {
            if (!FRONTEND.has(toolCallName)) {
              setNotes((n) => ({ ...n, [event.toolCallId]: { id: event.toolCallId,
                                                              text: t("chat.usedTool", { tool: toolCallName.replace(/_/g, " ") }) } }));
            }
          },
        });
        setStreaming(null);
        const calls = pendingCalls();
        if (!calls.length) break;
        for (const c of calls) {
          const result = runFrontendTool(c.name, c.args);
          setNotes((n) => ({ ...n, [c.id]: { id: c.id, text: result } }));
          agent.addMessage({ id: crypto.randomUUID(), role: "tool", toolCallId: c.id, content: result });
        }
        setMessages([...agent.messages]);
      }
    } catch (e) {
      console.error(e);
      setError(t("chat.error"));
    } finally {
      setStreaming(null);
      setMessages([...agent.messages]);
      setBusy(false);
    }
  }

  const suggestions = t("chat.suggestions", { returnObjects: true }) as string[];
  const shown = messages.filter((m) => m.role === "user" || m.role === "assistant");

  return (
    <div className="card chat-card">
      <div className="card-head">
        <h2><SparkleRegular className="chat-icon" /> {t("chat.title")}</h2>
      </div>
      <div className="chat-log" ref={scroller} aria-live="polite">
        {!shown.length && (
          <div className="chat-intro">
            <Text className="muted">{t("chat.intro")}</Text>
            <div className="suggestions">
              {suggestions.map((s) => (
                <Button key={s} size="small" shape="circular" onClick={() => send(s)} disabled={busy}>{s}</Button>
              ))}
            </div>
          </div>
        )}
        {shown.map((m) => {
          const text = typeof m.content === "string" ? m.content : "";
          const calls = m.role === "assistant" ? m.toolCalls ?? [] : [];
          return (
            <div key={m.id} className={`msg ${m.role}`}>
              {calls.map((c) => notes[c.id] && <div key={c.id} className="tool-note">{notes[c.id].text}</div>)}
              {text && streaming?.id !== m.id && <div className="bubble">{text}</div>}
            </div>
          );
        })}
        {streaming && <div className="msg assistant"><div className="bubble">{streaming.text}</div></div>}
        {busy && !streaming && <Spinner size="tiny" label={t("chat.thinking")} labelPosition="after" />}
        {error && <Text className="chat-error">{error}</Text>}
      </div>
      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); void send(input); }}>
        <Textarea value={input} onChange={(_, d) => setInput(d.value)} placeholder={t("chat.placeholder")}
                  resize="none" rows={2} disabled={busy}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(input); } }} />
        <Button type="submit" appearance="primary" icon={<SendRegular />} disabled={busy || !input.trim()}
                aria-label={t("chat.send")} />
      </form>
    </div>
  );
}
