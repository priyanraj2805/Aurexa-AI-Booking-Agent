import React from "react";

export function LoaderPulseSquare({ className = "" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 50 50" width="32" height="32" fill="none" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <defs>
        <linearGradient id="pulseGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="currentColor" stopOpacity="1" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0.4" />
        </linearGradient>
      </defs>
      <g stroke="url(#pulseGrad)">
        <rect x="12" y="12" width="26" height="26" rx="4">
          <animate attributeName="rx" values="4; 13; 4" dur="2s" repeatCount="indefinite" calcMode="spline" keySplines="0.4 0 0.2 1; 0.4 0 0.2 1" />
          <animateTransform attributeName="transform" type="rotate" values="0 25 25; 90 25 25; 180 25 25; 270 25 25; 360 25 25" dur="3s" repeatCount="indefinite" keyTimes="0; 0.25; 0.5; 0.75; 1" calcMode="spline" keySplines="0.8 0 0.2 1; 0.8 0 0.2 1; 0.8 0 0.2 1; 0.8 0 0.2 1" />
          <animate attributeName="stroke-dasharray" values="20 84; 84 20; 20 84" dur="2s" repeatCount="indefinite" calcMode="spline" keySplines="0.4 0 0.2 1; 0.4 0 0.2 1" />
        </rect>
        <circle cx="25" cy="25" r="3" fill="currentColor" stroke="none">
          <animate attributeName="opacity" values="0; 1; 0" dur="2s" repeatCount="indefinite" calcMode="spline" keySplines="0.4 0 0.2 1; 0.4 0 0.2 1" />
          <animate attributeName="r" values="2; 5; 2" dur="2s" repeatCount="indefinite" calcMode="spline" keySplines="0.4 0 0.2 1; 0.4 0 0.2 1" />
        </circle>
      </g>
    </svg>
  );
}
