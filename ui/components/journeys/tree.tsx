"use client";

// The left panel of /journeys: domains and their journeys. In "Organise" mode the product manager adds, renames,
// reorders, moves and deletes domains and journeys (deleting always moves the contents somewhere else first).
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, FolderPlus, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useState } from "react";

import { Confirm } from "@/components/journeys/common";
import * as ops from "@/components/journeys/tree-ops";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { Domain } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  tree: Domain[];
  selected: string;
  onSelect: (journeyId: string) => void;
  organise: boolean;
  onTree: (tree: Domain[], ok: string) => void;
  flagged: Set<string>; // message ids with an unpublished change or a problem
  query: string;
  onQuery: (q: string) => void;
};

const count = (d: Domain) => d.journeys.reduce((n, j) => n + j.steps.reduce((m, s) => m + s.messages.length, 0), 0);

export function TreePanel({ tree, selected, onSelect, organise, onTree, flagged, query, onQuery }: Props) {
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<{ id: string; name: string } | null>(null);
  const [newDomain, setNewDomain] = useState("");

  const toggle = (id: string) => setClosed((s) => {
    const next = new Set(s);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  function rename(kind: "domain" | "journey") {
    if (!editing?.name.trim()) return setEditing(null);
    onTree(kind === "domain" ? ops.renameDomain(tree, editing.id, editing.name.trim()) : ops.renameJourney(tree, editing.id, editing.name.trim()),
           "Renamed.");
    setEditing(null);
  }

  return (
    <div className="space-y-3">
      <div className="relative">
        <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
        <Input value={query} onChange={(e) => onQuery(e.target.value)} placeholder="Search every sentence" className="pl-8" />
      </div>
      <nav className="space-y-1">
        {tree.map((d, di) => {
          const open = !closed.has(d.id);
          const changed = d.journeys.some((j) => j.steps.some((s) => s.messages.some((m) => flagged.has(m))));
          return (
            <div key={d.id} className="rounded-md">
              <div className="flex min-w-0 items-center gap-1">
                <button onClick={() => toggle(d.id)} className="flex min-w-0 flex-1 items-center gap-1 rounded-md px-1.5 py-1 text-left text-sm font-medium hover:bg-accent">
                  {open ? <ChevronDown className="size-4 shrink-0" /> : <ChevronRight className="size-4 shrink-0" />}
                  {editing?.id === d.id ? (
                    <Input autoFocus value={editing.name} onClick={(e) => e.stopPropagation()} onChange={(e) => setEditing({ id: d.id, name: e.target.value })}
                           onKeyDown={(e) => e.key === "Enter" && rename("domain")} onBlur={() => rename("domain")} className="h-7" />
                  ) : <span className="min-w-0 truncate">{d.name}</span>}
                  {changed && <span className="size-1.5 shrink-0 rounded-full bg-warning" title="Has unpublished changes" />}
                  <span className="ml-auto shrink-0 text-xs font-normal text-muted-foreground">{count(d)}</span>
                </button>
                {organise && (
                  <span className="flex shrink-0">
                    <Button size="icon" variant="ghost" className="size-7" title="Rename" onClick={() => setEditing({ id: d.id, name: d.name })}><Pencil /></Button>
                    <Button size="icon" variant="ghost" className="size-7" title="Move up" disabled={di === 0} onClick={() => onTree(ops.moveDomain(tree, d.id, -1), "Moved.")}><ArrowUp /></Button>
                    <Button size="icon" variant="ghost" className="size-7" title="Move down" disabled={di === tree.length - 1} onClick={() => onTree(ops.moveDomain(tree, d.id, 1), "Moved.")}><ArrowDown /></Button>
                    <DeleteDomain tree={tree} domain={d} onTree={onTree} />
                  </span>
                )}
              </div>
              {open && (
                <div className="ml-4 space-y-0.5 border-l pl-2">
                  {d.journeys.map((j) => {
                    const active = j.id === selected;
                    const dot = j.steps.some((s) => s.messages.some((m) => flagged.has(m)));
                    return (
                      <div key={j.id} className="flex min-w-0 items-center gap-1">
                        {editing?.id === j.id ? (
                          <Input autoFocus value={editing.name} onChange={(e) => setEditing({ id: j.id, name: e.target.value })}
                                 onKeyDown={(e) => e.key === "Enter" && rename("journey")} onBlur={() => rename("journey")} className="h-7" />
                        ) : (
                          <button onClick={() => onSelect(j.id)}
                                  className={cn("flex min-w-0 flex-1 items-center gap-1.5 rounded-md px-2 py-1 text-left text-sm",
                                                active ? "bg-secondary font-medium" : "text-muted-foreground hover:bg-accent hover:text-foreground")}>
                            <span className="min-w-0 truncate">{j.name}</span>
                            {dot && <span className="size-1.5 shrink-0 rounded-full bg-warning" />}
                          </button>
                        )}
                        {organise && editing?.id !== j.id && (
                          <Button size="icon" variant="ghost" className="size-7 shrink-0" title="Rename" onClick={() => setEditing({ id: j.id, name: j.name })}><Pencil /></Button>
                        )}
                      </div>
                    );
                  })}
                  {organise && <AddJourney onAdd={(name) => {
                    const { tree: next, id } = ops.addJourney(tree, d.id, name);
                    onTree(next, "Journey added.");
                    onSelect(id);
                  }} />}
                  {!d.journeys.length && !organise && <p className="px-2 py-1 text-xs text-muted-foreground">Empty</p>}
                </div>
              )}
            </div>
          );
        })}
      </nav>
      {organise && (
        <div className="flex gap-2">
          <Input value={newDomain} onChange={(e) => setNewDomain(e.target.value)} placeholder="New domain name" />
          <Button variant="outline" disabled={!newDomain.trim()} onClick={() => { onTree(ops.addDomain(tree, newDomain.trim()), "Domain added."); setNewDomain(""); }}>
            <FolderPlus /> Add
          </Button>
        </div>
      )}
    </div>
  );
}

function AddJourney({ onAdd }: { onAdd: (name: string) => void }) {
  const [name, setName] = useState("");
  return (
    <div className="flex gap-1 py-1">
      <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="New journey" className="h-7 text-xs" />
      <Button size="icon" variant="outline" className="size-7 shrink-0" title="Add journey" disabled={!name.trim()}
              onClick={() => { onAdd(name.trim()); setName(""); }}><Plus /></Button>
    </div>
  );
}

function DeleteDomain({ tree, domain, onTree }: { tree: Domain[]; domain: Domain; onTree: Props["onTree"] }) {
  const others = tree.filter((d) => d.id !== domain.id);
  const [moveTo, setMoveTo] = useState(others[0]?.id ?? "");
  if (!others.length) return null;
  return (
    <Confirm trigger={<Button size="icon" variant="ghost" className="size-7" title="Delete domain"><Trash2 /></Button>}
             title={`Delete "${domain.name}"?`} action="Move and delete" destructive
             text={domain.journeys.length ? `Its ${domain.journeys.length} journey(s) and every sentence in them move to the domain you pick. Nothing the agent says is lost.`
                                         : "It is empty."}
             onConfirm={() => onTree(ops.deleteDomain(tree, domain.id, moveTo), `"${domain.name}" deleted; its journeys moved.`)}>
      {domain.journeys.length > 0 && (
        <Select value={moveTo} onValueChange={setMoveTo}>
          <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
          <SelectContent>{others.map((d) => <SelectItem key={d.id} value={d.id}>Move to: {d.name}</SelectItem>)}</SelectContent>
        </Select>
      )}
    </Confirm>
  );
}
