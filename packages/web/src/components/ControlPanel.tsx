import {
  Badge,
  Button,
  Dialog,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Field,
  Input,
  ProgressBar,
  Select,
  Spinner,
  Text,
  ToggleButton,
} from "@fluentui/react-components";
import { Checkmark12Filled } from "@fluentui/react-icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useViewer } from "../store";

// The control room: a tester tool (English only) for starting and stopping match streams, finding dramatic
// matches and regenerating season files, so testing needs no terminals beyond `footballfoundry-agents serve`.

interface ControlInfo {
  enabled: boolean;
  protected?: boolean;
  authorized?: boolean;
  target?: "local" | "eventhub";
  eventhub?: boolean;
  max_streams?: number;
  speeds?: number[];
  local_only?: boolean;
  scenarios?: { file: string; name: string; description: string }[];
  stories?: Record<string, string>;
  clubs?: { club_id: string; name: string; short_name: string }[];
  fixtures?: { match_id: string; matchday: number; home: string; away: string }[];
  season?: { status: string; error?: string; out?: string };
}

interface Job {
  job_id: string;
  stream_id: string;
  label: string;
  match_ids: string[];
  speed: number;
  target: string;
  scenario: string | null;
  total: number;
  sent: number;
  clock: string;
  status: "running" | "finished" | "stopped" | "failed";
  error: string | null;
  has_truth: boolean;
}

interface Story {
  match_id: string;
  matchday: number;
  home: string;
  away: string;
  score: [number, number];
  drama: number;
  stories: Record<string, string>;
}

type Mode = "fixture" | "matchday" | "oneoff";

const KEY_STORE = "ff.controlKey";

function storedKey(): string {
  try {
    return sessionStorage.getItem(KEY_STORE) ?? "";
  } catch {
    return "";
  }
}

async function call<T>(key: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", ...(key ? { "x-control-key": key } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { error?: string }).error ?? `${res.status}`);
  return data as T;
}

function speedLabel(s: number): string {
  return s === 0 ? "Instant" : s === 1 ? "Real time" : `${s}×`;
}

function watch(matchId: string) {
  useViewer.getState().setMatch(matchId);
  window.location.href = "/";
}

const STATUS_COLOR = { running: "brand", finished: "success", stopped: "warning", failed: "danger" } as const;

export function ControlPanel() {
  const [key, setKey] = useState(storedKey);
  const [keyDraft, setKeyDraft] = useState("");
  const [info, setInfo] = useState<ControlInfo | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [mode, setMode] = useState<Mode>("fixture");
  const [matchId, setMatchId] = useState("M0015");
  const [matchday, setMatchday] = useState("1");
  const [home, setHome] = useState("T01");
  const [away, setAway] = useState("T02");
  const [speed, setSpeed] = useState(30);
  const [halftime, setHalftime] = useState("");
  const [scenario, setScenario] = useState("");
  const [target, setTarget] = useState<string | null>(null);

  const [wanted, setWanted] = useState<string[]>([]);
  const [stories, setStories] = useState<Story[] | null>(null);
  const [finding, setFinding] = useState(false);
  const [truth, setTruth] = useState<unknown>(null);

  const load = useCallback(() => {
    call<ControlInfo>(key, "/api/control").then(setInfo, (e: Error) => setError(e.message));
  }, [key]);
  useEffect(load, [load]);

  const ready = info?.enabled && info.authorized;
  useEffect(() => {
    if (!ready) return;
    let last = "";
    const poll = () => call<Job[]>(key, "/api/control/streams").then((js) => {
      const text = JSON.stringify(js);
      if (text !== last) {  // re-render only when something changed, not every 2 seconds
        last = text;
        setJobs(js);
      }
    }, () => undefined);
    poll();
    const timer = setInterval(poll, 2000);
    return () => clearInterval(timer);
  }, [ready, key]);

  // Season generation runs in the background; refresh its status while it does.
  useEffect(() => {
    if (info?.season?.status !== "running") return;
    const timer = setTimeout(load, 3000);
    return () => clearTimeout(timer);
  }, [info, load]);

  const clubName = useMemo(() => {
    const names = new Map(info?.clubs?.map((c) => [c.club_id, c.short_name]));
    return (id: string) => names.get(id) ?? id;
  }, [info]);

  // Fixture labels and the picker's options, grouped by matchday: built once, not on every render.
  const { fixtureText, fixtureOptions } = useMemo(() => {
    const fixtures = info?.fixtures ?? [];
    const labels = new Map(fixtures.map((f) => [f.match_id, `${f.match_id} · ${clubName(f.home)} v ${clubName(f.away)}`]));
    const byMatchday = new Map<number, typeof fixtures>();
    for (const f of fixtures) byMatchday.set(f.matchday, [...(byMatchday.get(f.matchday) ?? []), f]);
    return {
      fixtureText: (id: string) => labels.get(id) ?? id,
      fixtureOptions: [...byMatchday].map(([md, fs]) => (
        <optgroup key={md} label={`Matchday ${md}`}>
          {fs.map((f) => <option key={f.match_id} value={f.match_id}>{labels.get(f.match_id)}</option>)}
        </optgroup>
      )),
    };
  }, [info, clubName]);

  const run = async (spec: Record<string, unknown>) => {
    setBusy(true);
    setError(null);
    try {
      const job = await call<Job>(key, "/api/control/streams", {
        speed,
        halftime: halftime || undefined,
        scenario: scenario || undefined,
        target: target ?? info?.target,
        ...spec,
      });
      setJobs((js) => [job, ...js.filter((j) => j.job_id !== job.job_id)]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const start = () =>
    run(mode === "fixture" ? { match_id: matchId } : mode === "matchday" ? { matchday: Number(matchday) } : { home, away });

  const stop = (job: Job) =>
    call<Job>(key, `/api/control/streams/${job.job_id}/stop`, {}).then(
      (j) => setJobs((js) => js.map((x) => (x.job_id === j.job_id ? j : x))),
      (e: Error) => setError(e.message),
    );

  const find = async () => {
    setFinding(true);
    setError(null);
    try {
      setStories(await call<Story[]>(key, "/api/control/stories", { stories: wanted, limit: 15 }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setFinding(false);
    }
  };

  const generate = () =>
    call<ControlInfo["season"]>(key, "/api/control/season", {}).then(
      (season) => setInfo((i) => (i ? { ...i, season } : i)),
      (e: Error) => setError(e.message),
    );

  const unlock = () => {
    try {
      sessionStorage.setItem(KEY_STORE, keyDraft);
    } catch {
      /* the key lasts until reload */
    }
    setKey(keyDraft);
  };

  const header = (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden />
        <div>
          <Text as="h1" size={600} weight="semibold" block>Control room</Text>
          <Text className="muted" size={200}>Stream matches into FootballFoundry for testing</Text>
        </div>
      </div>
      <Button as="a" href="/" appearance="secondary">Back to the match</Button>
    </header>
  );

  if (!info) {
    return (
      <div className="app">
        {header}
        <div className="empty">{error ? <Text>{error}</Text> : <Spinner label="Loading" />}</div>
      </div>
    );
  }
  if (!info.enabled) {
    return (
      <div className="app">
        {header}
        <div className="empty">
          <Text size={500} weight="semibold">The control room is off on this deployment</Text>
          <Text>
            Turn it on with a key: <code>azd env set CONTROL_KEY &lt;a long random string&gt;</code>, then{" "}
            <code>azd provision</code>.
          </Text>
        </div>
      </div>
    );
  }
  if (!info.authorized) {
    return (
      <div className="app">
        {header}
        <div className="empty">
          <Text size={500} weight="semibold">Enter the control key</Text>
          <div className="toolbar">
            <Input type="password" value={keyDraft} onChange={(_, d) => setKeyDraft(d.value)}
              onKeyDown={(e) => e.key === "Enter" && unlock()} aria-label="Control key" />
            <Button appearance="primary" onClick={unlock}>Unlock</Button>
          </div>
          {key && <Text className="muted">That key didn't work.</Text>}
        </div>
      </div>
    );
  }

  const running = jobs.filter((j) => j.status === "running").length;

  return (
    <div className="app control">
      {header}
      {error && <div className="card control-error"><Text>{error}</Text></div>}

      <section className="card">
        <div className="card-head">
          <Text weight="semibold" size={400}>Start a stream</Text>
          <Text className="muted" size={200}>
            Events go to {(target ?? info.target) === "eventhub" ? "Event Hubs (the whole pipeline)" : "this service directly"}
          </Text>
        </div>
        <div className="control-form">
          <Field label="What">
            <div className="segmented">
              {(["fixture", "matchday", "oneoff"] as Mode[]).map((m) => (
                <ToggleButton key={m} size="small" appearance="subtle" checked={mode === m} onClick={() => setMode(m)}>
                  {m === "fixture" ? "Fixture" : m === "matchday" ? "Matchday" : "One-off"}
                </ToggleButton>
              ))}
            </div>
          </Field>
          {mode === "fixture" && (
            <Field label="Fixture">
              <Select value={matchId} onChange={(_, d) => setMatchId(d.value)} className="control-wide">
                {fixtureOptions}
              </Select>
            </Field>
          )}
          {mode === "matchday" && (
            <Field label="Matchday (10 matches)">
              <Input type="number" min={1} max={38} value={matchday} onChange={(_, d) => setMatchday(d.value)} />
            </Field>
          )}
          {mode === "oneoff" && (
            <>
              {([["Home", home, setHome], ["Away", away, setAway]] as const).map(([label, value, set]) => (
                <Field key={label} label={label}>
                  <Select value={value} onChange={(_, d) => set(d.value)}>
                    {info.clubs?.map((c) => <option key={c.club_id} value={c.club_id}>{c.name}</option>)}
                  </Select>
                </Field>
              ))}
            </>
          )}
          <Field label="Speed">
            <Select value={String(speed)} onChange={(_, d) => setSpeed(Number(d.value))}>
              {info.speeds?.map((s) => <option key={s} value={String(s)}>{speedLabel(s)}</option>)}
            </Select>
          </Field>
          <Field label="Half-time (real seconds)" hint="Empty: 15 match minutes">
            <Input type="number" min={0} value={halftime} placeholder="auto" onChange={(_, d) => setHalftime(d.value)} />
          </Field>
          <Field label="Scenario">
            <Select value={scenario} onChange={(_, d) => setScenario(d.value)}>
              <option value="">None</option>
              {info.scenarios?.map((s) => <option key={s.file} value={s.file} title={s.description}>{s.name}</option>)}
            </Select>
          </Field>
          {info.eventhub && (
            <Field label="Send to">
              <Select value={target ?? info.target ?? "local"} onChange={(_, d) => setTarget(d.value)}>
                <option value="eventhub">Event Hubs</option>
                <option value="local">This service</option>
              </Select>
            </Field>
          )}
          <Button appearance="primary" onClick={start} disabled={busy || running >= (info.max_streams ?? 3)}
            className="control-go">
            {busy ? <Spinner size="tiny" /> : "Start"}
          </Button>
        </div>
        {scenario && (
          <Text className="muted" size={200} block>
            {info.scenarios?.find((s) => s.file === scenario)?.description} Its ground truth appears on the stream
            below.
          </Text>
        )}
      </section>

      <section className="card">
        <div className="card-head">
          <Text weight="semibold" size={400}>Streams</Text>
          <Text className="muted" size={200}>{running} of {info.max_streams} running</Text>
        </div>
        {!jobs.length ? (
          <Text className="muted">Nothing streamed from here yet.</Text>
        ) : (
          <div className="control-jobs">
            {jobs.map((j) => (
              <div key={j.job_id} className="control-job">
                <div className="control-job-main">
                  <Text weight="semibold">{j.label}</Text>
                  <Text className="muted" size={200}>
                    {speedLabel(j.speed)} · {j.target === "eventhub" ? "Event Hubs" : "local"} · stream {j.stream_id}
                    {j.scenario ? ` · scenario ${j.scenario}` : ""}
                  </Text>
                  <ProgressBar value={j.total ? j.sent / j.total : 0} thickness="large"
                    color={j.status === "failed" ? "error" : j.status === "finished" ? "success" : "brand"} />
                  {j.error && <Text size={200} className="control-err-text">{j.error}</Text>}
                </div>
                <Text className="control-clock">{j.clock}</Text>
                <Badge appearance="tint" color={STATUS_COLOR[j.status]}>{j.status}</Badge>
                <div className="toolbar">
                  {j.match_ids.length === 1 ? (
                    <Button size="small" onClick={() => watch(j.match_ids[0])}>Watch</Button>
                  ) : (
                    <Select size="small" value="" className="control-watch" aria-label="Watch a match"
                      onChange={(_, d) => d.value && watch(d.value)}>
                      <option value="" disabled>Watch…</option>
                      {j.match_ids.map((m) => <option key={m} value={m}>{fixtureText(m)}</option>)}
                    </Select>
                  )}
                  {j.has_truth && (
                    <Button size="small" onClick={() =>
                      call(key, `/api/control/streams/${j.job_id}/truth`).then(setTruth, (e: Error) => setError(e.message))}>
                      Ground truth
                    </Button>
                  )}
                  {j.status === "running" && <Button size="small" onClick={() => stop(j)}>Stop</Button>}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="card">
        <div className="card-head">
          <Text weight="semibold" size={400}>Find a match worth watching</Text>
          <Text className="muted" size={200}>Searches all 380 fixtures; pick stories, or none for the most dramatic</Text>
        </div>
        <div className="control-stories">
          {Object.entries(info.stories ?? {}).map(([tag, about]) => (
            // Selected chips are solid brand colour with a tick, so on and off differ by more than a shade.
            <ToggleButton key={tag} size="small" shape="circular" title={about} checked={wanted.includes(tag)}
              appearance={wanted.includes(tag) ? "primary" : "outline"}
              icon={wanted.includes(tag) ? <Checkmark12Filled /> : undefined}
              onClick={() => setWanted((w) => (w.includes(tag) ? w.filter((x) => x !== tag) : [...w, tag]))}>
              {tag}
            </ToggleButton>
          ))}
          <Button appearance="primary" onClick={find} disabled={finding}>
            {finding ? <Spinner size="tiny" label="Searching" /> : "Find"}
          </Button>
        </div>
        {stories && !stories.length && <Text className="muted">No match has all of those.</Text>}
        {stories?.map((s) => (
          <div key={s.match_id} className="control-story">
            <div>
              <Text weight="semibold">
                {s.match_id} · MD{s.matchday} · {s.home} {s.score[0]}-{s.score[1]} {s.away}
              </Text>
              <ul>
                {Object.entries(s.stories).map(([tag, detail]) => (
                  <li key={tag}><b>{tag}</b> {detail}</li>
                ))}
              </ul>
            </div>
            <Button size="small" onClick={() => run({ match_id: s.match_id })} disabled={busy}>Stream it</Button>
          </div>
        ))}
      </section>

      {info.local_only && (
        <section className="card">
          <div className="card-head">
            <Text weight="semibold" size={400}>Season files</Text>
            <Text className="muted" size={200}>For the MCP server, in {info.season?.out ?? "data"}</Text>
          </div>
          <div className="toolbar">
            <Button onClick={generate} disabled={info.season?.status === "running"}>
              {info.season?.status === "running" ? <Spinner size="tiny" label="Generating" /> : "Regenerate the season"}
            </Button>
            {info.season?.status === "finished" && <Text>Done.</Text>}
            {info.season?.status === "failed" && <Text className="control-err-text">{info.season.error}</Text>}
            <Text className="muted" size={200}>Overwrites the JSON files (Parquet: <code>footballfoundry season --parquet</code>).</Text>
          </div>
        </section>
      )}

      <Dialog open={truth !== null} onOpenChange={(_, d) => !d.open && setTruth(null)}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle action={<Button appearance="subtle" onClick={() => setTruth(null)}>Close</Button>}>
              Ground truth
            </DialogTitle>
            <DialogContent>
              <pre className="control-truth">{JSON.stringify(truth, null, 2)}</pre>
            </DialogContent>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </div>
  );
}
