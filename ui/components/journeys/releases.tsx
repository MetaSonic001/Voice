"use client";

// Releases tab: review the draft, prepare translations, publish to a share of calls (canary) or to all, compare the
// canary with the main release on the same calls, then promote, change the share, stop it or roll back.
import { Bot, History, Rocket, RotateCcw, Square, Trash2, TrendingUp } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Confirm, human, LanguagePicker, Problems, targets, when } from "@/components/journeys/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { journeys, type ArmStats, type Change, type Editor, type Releases } from "@/lib/api";

type Props = { editor: Editor; author: string; onEditor: (e: Editor) => void; onOpen: (mid: string) => void };

const releaseName = (id: number | null) => (id ? `Release ${id}` : "As shipped (git)");

export function ReleasesTab({ editor, author, onEditor, onOpen }: Props) {
  const [status, setStatus] = useState<Releases | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setStatus(await journeys.releases()); } catch (e) { toast.error((e as Error).message); }
  }, []);
  useEffect(() => void load(), [load, editor.rollout.changed_at]);

  async function run<T>(what: () => Promise<T>, ok: string): Promise<T | null> {
    setBusy(true);
    try {
      const out = await what();
      toast.success(ok);
      return out;
    } catch (e) {
      toast.error((e as Error).message);
      return null;
    } finally {
      setBusy(false);
    }
  }
  async function rollout(action: "promote" | "share" | "stop" | "live", ok: string, extra = {}) {
    const out = await run(() => journeys.rollout(action, author, extra), ok);
    if (out) {
      setStatus(out);
      onEditor(await journeys.get());
    }
  }

  const r = editor.rollout;
  return (
    <div className="space-y-4">
      <DraftCard editor={editor} author={author} busy={busy} run={run} onEditor={onEditor} onOpen={onOpen} />
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base"><TrendingUp className="size-4" /> Live now</CardTitle>
          <CardDescription>
            {releaseName(r.stable)} answers {r.canary ? `${100 - r.share}% of calls` : "every call"}.
            {r.canary ? ` ${releaseName(r.canary)} answers the other ${r.share}% (canary, since ${when(r.since)}).` : ""}
            {r.changed_by ? ` Last change by ${r.changed_by}, ${when(r.changed_at)}.` : ""}
          </CardDescription>
        </CardHeader>
        {r.canary && (
          <CardContent className="space-y-3">
            {status?.stats && <Comparison stats={status.stats} />}
            <div className="flex flex-wrap items-center gap-2">
              <Confirm trigger={<Button disabled={busy}><Rocket /> Promote to all calls</Button>} title={`Make ${releaseName(r.canary)} live for every call?`}
                       text="Every new call will use it. Calls already in progress finish on the release they started with." action="Promote"
                       onConfirm={() => void rollout("promote", "Promoted to all calls.")} />
              <Select value={String(r.share)} onValueChange={(v) => void rollout("share", `Canary now on ${v}% of calls.`, { share: Number(v) })}>
                <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
                <SelectContent>{editor.canary_shares.map((s) => <SelectItem key={s} value={String(s)}>Canary on {s}%</SelectItem>)}</SelectContent>
              </Select>
              <Confirm trigger={<Button variant="outline" disabled={busy}><Square /> Stop canary</Button>} title="Stop the canary?" destructive
                       text={`New calls go back to ${releaseName(r.stable)}. The canary release is kept and can be made live again later.`} action="Stop"
                       onConfirm={() => void rollout("stop", "Canary stopped.")} />
            </div>
          </CardContent>
        )}
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base"><History className="size-4" /> History</CardTitle>
          <CardDescription>Every published release, newest first. Making an older one live is how you roll back.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader><TableRow><TableHead>Release</TableHead><TableHead>Published</TableHead><TableHead className="min-w-56">Notes</TableHead>
                <TableHead>Changes</TableHead><TableHead /></TableRow></TableHeader>
              <TableBody>
                {[...(status?.releases ?? []), { id: 0, notes: "The catalogue in brain/content/ as the code ships it.", author: "", published_at: "", changes: 0 }].map((x) => {
                  const isLive = x.id === r.stable || x.id === r.canary;
                  return (
                    <TableRow key={x.id}>
                      <TableCell className="font-medium whitespace-nowrap">{releaseName(x.id)}{x.id === r.stable && <Badge className="ml-2" variant="secondary">live</Badge>}
                        {x.id === r.canary && <Badge className="ml-2" variant="outline">canary</Badge>}</TableCell>
                      <TableCell className="text-xs whitespace-nowrap">{when(x.published_at)}{x.author ? ` · ${x.author}` : ""}</TableCell>
                      <TableCell className="text-sm break-words">{x.notes}</TableCell>
                      <TableCell>{x.id ? x.changes : ""}</TableCell>
                      <TableCell>
                        {!isLive && (
                          <Confirm trigger={<Button size="sm" variant="outline" disabled={busy}><RotateCcw /> Make live</Button>}
                                   title={`Make ${releaseName(x.id)} live for every call?`} action="Make live"
                                   text={`It replaces what callers hear now${r.canary ? " and stops the running canary" : ""}. Calls already in progress keep their release.`}
                                   onConfirm={() => void rollout("live", `${releaseName(x.id)} is live.`, { release: x.id })} />
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          {status?.log.length ? (
            <div className="space-y-1">
              <p className="text-sm font-medium">What changed when</p>
              <ul className="space-y-0.5 text-xs text-muted-foreground">
                {status.log.map((e) => <li key={e.id} className="break-words">{when(e.at)} · {e.action}{e.who ? ` · ${e.who}` : ""}{e.notes ? ` · “${e.notes}”` : ""}</li>)}
              </ul>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

function DraftCard({ editor, author, busy, run, onEditor, onOpen }: Props & {
  busy: boolean; run: <T>(what: () => Promise<T>, ok: string) => Promise<T | null>;
}) {
  const [notes, setNotes] = useState("");
  const [share, setShare] = useState("5");
  const [langs, setLangs] = useState(targets(editor.languages).map((l) => l.code));
  const errors = editor.problems.filter((p) => p.level === "error");
  const canaryRunning = Boolean(editor.rollout.canary);
  const blocked = !editor.changes.length ? "Nothing to publish yet." : errors.length ? "Fix the problems above first."
    : canaryRunning ? "A canary is running: promote or stop it first." : !author.trim() ? "Write your name at the top first." : "";

  async function translate() {
    const out = await run(() => journeys.translate(author, { languages: langs }), "Translations written.");
    if (out) {
      onEditor(out.editor);
      if (out.failed.length) toast.warning(`${out.failed.length} could not be translated: ${out.failed[0].text}`);
    }
  }
  async function publish() {
    const out = await run(() => journeys.publish(Number(share), notes, author), Number(share) === 100 ? "Published to every call." : `Published to ${share}% of calls.`);
    if (out) { onEditor(out); setNotes(""); }
  }
  async function discard() {
    const out = await run(() => journeys.discard(), "Draft thrown away.");
    if (out) onEditor(out);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Draft</CardTitle>
        <CardDescription>
          {editor.changes.length ? `${editor.changes.length} change(s) not live yet` : "No changes yet"}
          {editor.draft.author ? `, last saved by ${editor.draft.author} ${when(editor.draft.updated_at)}` : ""}.
          Nothing here reaches a caller until it is published.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {editor.changes.length > 0 && <ChangeList changes={editor.changes} onOpen={onOpen} />}
        <Problems items={editor.problems} />
        <div className="flex flex-wrap items-center gap-2">
          <LanguagePicker languages={editor.languages} value={langs} onChange={setLangs} size="default" />
          <Button variant="outline" disabled={busy || !editor.needs_translation.length || !langs.length} onClick={() => void translate()}>
            <Bot /> Prepare translations{editor.needs_translation.length ? ` (${editor.needs_translation.length})` : ""}
          </Button>
          {editor.changes.length > 0 && (
            <Confirm trigger={<Button variant="ghost" disabled={busy}><Trash2 /> Discard draft</Button>} title="Throw the draft away?" destructive
                     text="Every unpublished change is lost. What is live doesn't change." action="Discard" onConfirm={() => void discard()} />
          )}
        </div>
        <div className="space-y-2 rounded-md border p-3">
          <p className="text-sm font-medium">Publish</p>
          <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} placeholder="What changed and why (kept in the history)" />
          <div className="flex flex-wrap items-center gap-2">
            <Select value={share} onValueChange={setShare}>
              <SelectTrigger className="w-full sm:w-64"><SelectValue /></SelectTrigger>
              <SelectContent>
                {editor.canary_shares.map((s) => <SelectItem key={s} value={String(s)}>Canary: {s}% of new calls</SelectItem>)}
                <SelectItem value="100">Every call (no canary)</SelectItem>
              </SelectContent>
            </Select>
            <Confirm trigger={<Button disabled={busy || Boolean(blocked)}><Rocket /> Publish</Button>}
                     title={Number(share) === 100 ? "Publish to every call?" : `Publish to ${share}% of calls?`} action="Publish"
                     text={Number(share) === 100 ? "Every new call uses it at once, with no comparison first. A canary is safer, especially for prompt changes."
                       : `About ${share} in 100 new calls will use it; the rest keep the current release. Compare them below, then promote or stop it.`}
                     onConfirm={() => void publish()} />
            {blocked && <span className="text-xs text-muted-foreground">{blocked}</span>}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function ChangeList({ changes, onOpen }: { changes: Change[]; onOpen: (mid: string) => void }) {
  return (
    <ul className="divide-y rounded-md border text-sm">
      {changes.map((c, i) => (
        <li key={i} className="space-y-1 p-2">
          <p className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{c.area === "message" ? "Sentence" : c.area === "prompt" ? "Prompt" : c.area === "setting" ? "Setting" : c.area === "language" ? "Language" : "Layout"}</Badge>
            {c.area === "message" ? <button className="text-xs underline-offset-2 hover:underline" onClick={() => onOpen(c.id)}><code>{c.id}</code></button>
              : <span className="text-xs">{c.area === "tree" ? "Domains, journeys or steps were reorganised"
                : c.area === "language" ? `${String(c.before)}: ${String(c.after)}` : human(c.id)}</span>}
            {c.translation_only && <span className="text-xs text-muted-foreground">translation only</span>}
          </p>
          {c.area === "message" && !c.translation_only && (
            <p className="text-xs break-words"><span className="text-muted-foreground line-through">{String(c.before)}</span><br />{String(c.after)}</p>
          )}
          {c.area === "setting" && (
            <p className="text-xs break-words text-muted-foreground">{JSON.stringify(c.before)} → {JSON.stringify(c.after)}</p>
          )}
        </li>
      ))}
    </ul>
  );
}

function Comparison({ stats }: { stats: NonNullable<Releases["stats"]> }) {
  const pct = (a: ArmStats, n: number) => (a.turns ? `${Math.round((n / a.turns) * 100)}%` : "–");
  const rows: [string, (a: ArmStats) => string][] = [
    ["Calls", (a) => String(a.calls)],
    ["Turns", (a) => String(a.turns)],
    ["Finished (answered, done, ticket)", (a) => pct(a, a.done)],
    ["Couldn't help (refused, error)", (a) => pct(a, a.failed)],
    ["Handed to a person", (a) => pct(a, a.handoff)],
    ["Median time to first words", (a) => (a.p50_ms ? `${Math.round(a.p50_ms)} ms` : "–")],
    ["Judge score (1-5)", (a) => (a.score ? `${a.score.toFixed(2)} (${a.judged} calls)` : "–")],
  ];
  const few = stats.canary.calls < 100;
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <Table>
          <TableHeader><TableRow><TableHead>Since {when(stats.since)}</TableHead><TableHead>Main</TableHead><TableHead>Canary ({stats.share}%)</TableHead></TableRow></TableHeader>
          <TableBody>{rows.map(([label, f]) => (
            <TableRow key={label}><TableCell className="text-sm">{label}</TableCell><TableCell>{f(stats.stable)}</TableCell><TableCell>{f(stats.canary)}</TableCell></TableRow>
          ))}</TableBody>
        </Table>
      </div>
      <p className="text-xs text-muted-foreground">
        Same time window, calls split at random, so the two columns are comparable.{" "}
        {few ? "With fewer than about 100 canary calls, differences are mostly chance: wait before deciding." : ""}
      </p>
    </div>
  );
}
