import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DotStark Super Admin",
  description: "Super Admin control plane for DotStark AI Booking Assistant",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-slate-950 text-slate-100 antialiased">
        {children}
      </body>
    </html>
  );
}
