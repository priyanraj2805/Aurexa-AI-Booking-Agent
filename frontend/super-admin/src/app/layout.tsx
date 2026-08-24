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
    <html lang="en" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem("super_admin_theme");if(t==="light"){document.documentElement.setAttribute("data-theme","light")}else{document.documentElement.removeAttribute("data-theme")}}catch(e){}`,
          }}
        />
      </head>
      <body className="min-h-screen antialiased">
        {children}
      </body>
    </html>
  );
}
