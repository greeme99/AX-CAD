import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AX-CAD",
  description: "AX-CAD DXF viewer",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" data-theme="light">
      <body className="antialiased">{children}</body>
    </html>
  );
}
