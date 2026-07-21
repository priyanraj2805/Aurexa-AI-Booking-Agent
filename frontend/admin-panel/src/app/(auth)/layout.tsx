import { ReactNode } from "react";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "1rem",
        position: "relative",
        overflow: "hidden",
        backgroundColor: "var(--background, #0a0a0a)",
      }}
    >
      {/* Subtle blue glow top-left */}
      <div
        style={{
          position: "absolute",
          top: "-10%",
          left: "-10%",
          width: "40%",
          height: "40%",
          background: "rgba(59, 130, 246, 0.08)",
          borderRadius: "50%",
          filter: "blur(120px)",
          pointerEvents: "none",
        }}
      />
      {/* Subtle blue glow bottom-right */}
      <div
        style={{
          position: "absolute",
          bottom: "-10%",
          right: "-10%",
          width: "40%",
          height: "40%",
          background: "rgba(59, 130, 246, 0.04)",
          borderRadius: "50%",
          filter: "blur(120px)",
          pointerEvents: "none",
        }}
      />

      <div style={{ width: "100%", maxWidth: "28rem", position: "relative", zIndex: 10 }}>
        {children}
      </div>
    </div>
  );
}
