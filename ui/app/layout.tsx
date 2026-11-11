import type { Metadata } from "next";

import { AlertWatcher, AppHeader } from "@/components/app-shell";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import "./globals.css";

export const metadata: Metadata = {
  title: "AU Bank Voice Agent",
  description: "Test the AU SFB voice agent, manage its knowledge base and audit every call.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // suppressHydrationWarning: browser extensions (e.g. a locator / dev-tools extension) add attributes to <html> before
    // React loads. It only ignores attribute differences on this one tag, not in the page.
    <html lang="en" className="h-full" suppressHydrationWarning>
      <body className="min-h-full">
        <TooltipProvider delayDuration={300}>
          <AppHeader />
          {children}
          <AlertWatcher />
          <Toaster position="top-right" richColors closeButton />
        </TooltipProvider>
      </body>
    </html>
  );
}
