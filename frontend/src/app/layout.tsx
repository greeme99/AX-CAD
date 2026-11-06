import type { Metadata } from "next";
import "./globals.css";
import CopilotProvider from "@/components/copilot/CopilotProvider";

export const metadata: Metadata = {
  title: "AX-CAD",
  description: "AX-CAD DXF viewer",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" data-theme="light">
      <body className="antialiased">
        <CopilotProvider>{children}</CopilotProvider>
      </body>
    </html>
  );
}
