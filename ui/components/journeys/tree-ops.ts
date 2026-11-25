// Pure changes to the journey tree (domain > journey > step > message ids). The page sends the whole new tree to the
// brain, which checks it and keeps every message placed exactly once. Nothing here deletes a message: deleting a
// domain, journey or step always moves what it holds somewhere else first.
import type { Domain, Journey } from "@/lib/api";

const copy = (tree: Domain[]): Domain[] => structuredClone(tree);

function slug(name: string, taken: Set<string>): string {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "item";
  let id = base;
  for (let n = 2; taken.has(id); n++) id = `${base}_${n}`;
  return id;
}

function ids(tree: Domain[]): Set<string> {
  return new Set(tree.flatMap((d) => [d.id, ...d.journeys.map((j) => j.id)]));
}

export function findJourney(tree: Domain[], journeyId: string): { domain: Domain; journey: Journey } | null {
  for (const domain of tree) for (const journey of domain.journeys) if (journey.id === journeyId) return { domain, journey };
  return null;
}

export function addDomain(tree: Domain[], name: string): Domain[] {
  return [...copy(tree), { id: slug(name, ids(tree)), name, journeys: [] }];
}

export function renameDomain(tree: Domain[], id: string, name: string): Domain[] {
  return copy(tree).map((d) => (d.id === id ? { ...d, name } : d));
}

/** Remove a domain; its journeys move to `moveTo`. */
export function deleteDomain(tree: Domain[], id: string, moveTo: string): Domain[] {
  const out = copy(tree);
  const gone = out.find((d) => d.id === id);
  const target = out.find((d) => d.id === moveTo);
  if (!gone || !target || gone === target) return out;
  target.journeys.push(...gone.journeys);
  return out.filter((d) => d.id !== id);
}

export function moveDomain(tree: Domain[], id: string, by: -1 | 1): Domain[] {
  const out = copy(tree);
  const i = out.findIndex((d) => d.id === id);
  const j = i + by;
  if (i < 0 || j < 0 || j >= out.length) return out;
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}

export function addJourney(tree: Domain[], domainId: string, name: string): { tree: Domain[]; id: string } {
  const out = copy(tree);
  const id = slug(name, ids(tree));
  out.find((d) => d.id === domainId)?.journeys.push({ id, name, steps: [{ name: "Step 1", messages: [] }] });
  return { tree: out, id };
}

export function renameJourney(tree: Domain[], id: string, name: string): Domain[] {
  const out = copy(tree);
  const found = findJourney(out, id);
  if (found) found.journey.name = name;
  return out;
}

/** Move a journey (with its steps) to another domain. */
export function moveJourney(tree: Domain[], id: string, toDomain: string): Domain[] {
  const out = copy(tree);
  const found = findJourney(out, id);
  const target = out.find((d) => d.id === toDomain);
  if (!found || !target || found.domain === target) return out;
  found.domain.journeys = found.domain.journeys.filter((j) => j.id !== id);
  target.journeys.push(found.journey);
  return out;
}

/** Remove a journey; its steps join `moveTo`. */
export function deleteJourney(tree: Domain[], id: string, moveTo: string): Domain[] {
  const out = copy(tree);
  const gone = findJourney(out, id);
  const target = findJourney(out, moveTo);
  if (!gone || !target || gone.journey === target.journey) return out;
  target.journey.steps.push(...gone.journey.steps);
  gone.domain.journeys = gone.domain.journeys.filter((j) => j.id !== id);
  return out;
}

export function addStep(tree: Domain[], journeyId: string, name: string): Domain[] {
  const out = copy(tree);
  findJourney(out, journeyId)?.journey.steps.push({ name, messages: [] });
  return out;
}

export function renameStep(tree: Domain[], journeyId: string, index: number, name: string): Domain[] {
  const out = copy(tree);
  const step = findJourney(out, journeyId)?.journey.steps[index];
  if (step) step.name = name;
  return out;
}

/** Remove a step; its messages move to the step before it (or after it, for the first step). */
export function deleteStep(tree: Domain[], journeyId: string, index: number): Domain[] {
  const out = copy(tree);
  const journey = findJourney(out, journeyId)?.journey;
  if (!journey || journey.steps.length < 2) return out;
  const [gone] = journey.steps.splice(index, 1);
  journey.steps[Math.max(0, index - 1)].messages.push(...gone.messages);
  return out;
}

/** Move one message to a step: `to` is "journeyId:stepIndex". */
export function moveMessage(tree: Domain[], mid: string, to: string): Domain[] {
  const out = copy(tree);
  const [journeyId, index] = [to.slice(0, to.lastIndexOf(":")), Number(to.slice(to.lastIndexOf(":") + 1))];
  const target = findJourney(out, journeyId)?.journey.steps[index];
  if (!target) return out;
  for (const d of out) for (const j of d.journeys) for (const s of j.steps) s.messages = s.messages.filter((m) => m !== mid);
  target.messages.push(mid);
  return out;
}
