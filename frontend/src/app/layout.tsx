import type { Metadata } from "next";
import "./globals.css";
import CopilotProvider from "@/components/copilot/CopilotProvider";
import { I18nProvider } from "@/lib/i18n";

export const metadata: Metadata = {
  title: "AX-CAD",
  description: "AX-CAD DXF viewer",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" data-theme="light">
      <body className="antialiased">
        <I18nProvider>
          <CopilotProvider>{children}</CopilotProvider>
        </I18nProvider>
      </body>
    </html>
  );
}
