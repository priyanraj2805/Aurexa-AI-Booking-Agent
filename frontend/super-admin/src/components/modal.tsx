"use client";

import { ReactNode, useState } from "react";

export function Modal({
  title,
  onClose,
  children,
  confirmOnClose,
  confirmMessage,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  confirmOnClose?: boolean;
  confirmMessage?: string;
}) {
  const [showConfirm, setShowConfirm] = useState(false);

  const handleClose = () => {
    if (confirmOnClose) {
      setShowConfirm(true);
    } else {
      onClose();
    }
  };

  const handleConfirmClose = () => {
    setShowConfirm(false);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4"
      onClick={handleClose}
    >
      <div
        className="w-full max-w-md rounded-lg border border-slate-700 bg-slate-900 p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button
            onClick={handleClose}
            className="text-slate-400 hover:text-slate-100"
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="mt-4">{children}</div>
      </div>

      {showConfirm && (
        <ConfirmDialog
          title="Discard changes?"
          message={
            <span>
              {confirmMessage || "You have unsaved changes. Are you sure you want to close this form?"}
            </span>
          }
          confirmLabel="Discard"
          danger
          onConfirm={handleConfirmClose}
          onCancel={() => setShowConfirm(false)}
        />
      )}
    </div>
  );
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal title={title} onClose={onCancel}>
      <div className="text-sm text-slate-300">{message}</div>
      <div className="mt-5 flex justify-end gap-2">
        <button
          onClick={onCancel}
          disabled={busy}
          className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          onClick={onConfirm}
          disabled={busy}
          className={`rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50 ${
            danger ? "bg-red-600 hover:bg-red-700" : "bg-indigo-600 hover:bg-indigo-700"
          }`}
        >
          {busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
