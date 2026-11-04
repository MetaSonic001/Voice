import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AU Bank Assistant - Test Console",
  description: "Test the AU SFB voice agent by text, push-to-talk or a live call.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
