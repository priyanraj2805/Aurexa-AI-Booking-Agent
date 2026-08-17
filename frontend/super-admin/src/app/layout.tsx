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
    <html lang="en" data-ui="classic">
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var m=localStorage.getItem("super_admin_ui_mode");if(m==="admin"){document.documentElement.setAttribute("data-ui","admin")}}catch(e){}`,
          }}
        />
      </head>
      <body className="min-h-screen bg-slate-950 text-slate-100 antialiased">
        {children}
      </body>
    </html>
  );
}
