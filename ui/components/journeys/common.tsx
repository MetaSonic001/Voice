"use client";

// Small pieces shared by the /journeys tabs: labels, badges, problem lists and a confirm dialog.
import { AlertTriangle, ChevronDown, CircleAlert, Languages } from "lucide-react";
import type { ReactNode } from "react";

import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Language, Problem } from "@/lib/api";
import { cn } from "@/lib/utils";

/** The languages a sentence can be translated into: every language but English (the source). */
export const targets = (languages: Language[]) => languages.filter((l) => !l.source);

export function langName(languages: Language[], code: string): string {
  return languages.find((l) => l.code === code)?.name ?? code;
}

/** A dropdown with a checkbox per language. Languages a release added (not spoken yet) are marked. */
export function LanguagePicker({ languages, value, onChange, size = "sm" }: {
  languages: Language[]; value: string[]; onChange: (codes: string[]) => void; size?: "sm" | "default";
}) {
  const all = targets(languages);
  const label = value.length === all.length ? "All languages" : value.length ? value.map((c) => langName(languages, c)).join(", ") : "No language";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size={size} className="max-w-full min-w-0 justify-between">
          <Languages /> <span className="truncate">{label}</span> <ChevronDown className="opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64">
        <DropdownMenuLabel>Translate into</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {all.map((l) => (
          <DropdownMenuCheckboxItem key={l.code} checked={value.includes(l.code)} onSelect={(e) => e.preventDefault()}
                                    onCheckedChange={(on) => onChange(on ? all.map((x) => x.code).filter((c) => c === l.code || value.includes(c))
                                                                          : value.filter((c) => c !== l.code))}>
            {l.name}{!l.spoken && <span className="ml-auto text-xs text-muted-foreground">not spoken yet</span>}
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** "payment_failed_debited" -> "Payment failed debited" */
export function human(key: string): string {
  const s = key.replace(/[._]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function Problems({ items, className }: { items: Problem[]; className?: string }) {
  if (!items.length) return null;
  return (
    <ul className={cn("space-y-1 text-xs", className)}>
      {items.map((p, i) => (
        <li key={i} className={cn("flex gap-1.5 break-words", p.level === "error" ? "text-destructive" : "text-warning")}>
          {p.level === "error" ? <CircleAlert className="mt-0.5 size-3.5 shrink-0" /> : <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />}
          <span className="min-w-0">{p.text}</span>
        </li>
      ))}
    </ul>
  );
}

/** A button that asks first (alert-dialog), for anything that throws work away or changes what callers hear. */
export function Confirm({ trigger, title, text, action, onConfirm, destructive = false, children }: {
  trigger: ReactNode; title: string; text: string; action: string; onConfirm: () => void; destructive?: boolean; children?: ReactNode;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>{trigger}</AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{text}</AlertDialogDescription>
        </AlertDialogHeader>
        {children}
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm} className={cn(destructive && "bg-destructive text-white hover:bg-destructive/90")}>
            {action}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function when(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "";
}
