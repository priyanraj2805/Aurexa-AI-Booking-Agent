"use client";

import { useState, useRef } from "react";
import { Loader2, User, Mail, Building2, Calendar, Upload, Image as ImageIcon, Trash2, Link as LinkIcon } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/lib/auth-context";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { api, uploadFile } from "@/lib/api";
import { PaymentsConnectCard } from "@/components/payments-connect-card";

export default function SettingsPage() {
  const { user, updateProfile, refresh } = useAuth();
  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [savingProfile, setSavingProfile] = useState(false);
  const [avatarUploading, setAvatarUploading] = useState(false);
  const [avatarError, setAvatarError] = useState<string | null>(null);
  const [avatarUrlInput, setAvatarUrlInput] = useState(user?.avatar_url || "");
  const avatarInputRef = useRef<HTMLInputElement>(null);

  const initials = user?.full_name
    ? user.full_name
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2)
    : "DS";

  const handleAvatarFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setAvatarError("Please select an image file");
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      setAvatarError("Image must be under 2MB");
      return;
    }
    setAvatarUploading(true);
    setAvatarError(null);
    try {
      await uploadFile("/account/avatar", file);
      toast.success("Avatar updated");
      await refresh();
    } catch (err) {
      setAvatarError(err instanceof Error ? err.message : "Upload failed");
      toast.error(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setAvatarUploading(false);
      if (avatarInputRef.current) avatarInputRef.current.value = "";
    }
  };

  const handleAvatarUrlSave = async () => {
    const url = avatarUrlInput.trim();
    if (!url) {
      setAvatarError("Please enter an image URL");
      return;
    }
    try {
      new URL(url);
    } catch {
      setAvatarError("Invalid URL");
      return;
    }
    setAvatarUploading(true);
    setAvatarError(null);
    try {
      await api.put("/account/avatar-url", { avatar_url: url });
      toast.success("Avatar URL saved");
      await refresh();
    } catch (err) {
      setAvatarError(err instanceof Error ? err.message : "Failed to save URL");
    } finally {
      setAvatarUploading(false);
    }
  };

  const handleAvatarRemove = async () => {
    setAvatarUploading(true);
    setAvatarError(null);
    try {
      await api.del("/account/avatar");
      setAvatarUrlInput("");
      toast.success("Avatar removed");
      await refresh();
    } catch (err) {
      setAvatarError(err instanceof Error ? err.message : "Failed to remove");
    } finally {
      setAvatarUploading(false);
    }
  };

  const handleProfileSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingProfile(true);
    try {
      await updateProfile(fullName.trim());
      toast.success("Profile updated");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not update profile");
    } finally {
      setSavingProfile(false);
    }
  };

  const handlePasswordChange = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword !== confirmPassword) {
      toast.error("New passwords do not match");
      return;
    }
    if (newPassword.length < 6) {
      toast.error("New password must be at least 6 characters");
      return;
    }
    setChangingPassword(true);
    try {
      await api.post("/account/change-password", {
        current_password: currentPassword,
        new_password: newPassword,
      });
      toast.success("Password changed");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not change password");
    } finally {
      setChangingPassword(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Manage your profile.
        </p>
      </div>

      {/* ── Profile ── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <User className="size-5 text-primary" />
            Profile
          </CardTitle>
          <CardDescription>Your account details — your email is your login.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleProfileSave} className="space-y-5">
            <div className="flex items-start gap-4">
              <div className="size-16 rounded-full border-2 border-border bg-primary text-primary-foreground flex items-center justify-center overflow-hidden shrink-0">
                {user?.avatar_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={user.avatar_url} alt={user.full_name || "User"} className="size-full object-cover" />
                ) : (
                  <span className="text-sm font-semibold">{initials}</span>
                )}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium">{user?.full_name || "User"}</p>
                <p className="text-sm text-muted-foreground truncate">{user?.email}</p>
                <div className="flex flex-wrap gap-2 mt-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => avatarInputRef.current?.click()}
                    disabled={avatarUploading}
                    className="h-7 text-xs"
                  >
                    {avatarUploading ? <Loader2 className="size-3 animate-spin" /> : <Upload className="size-3" />}
                    {user?.avatar_url ? "Change" : "Upload"} avatar
                  </Button>
                  {user?.avatar_url && (
                    <Button type="button" variant="ghost" size="sm" onClick={handleAvatarRemove} disabled={avatarUploading} className="h-7 text-xs">
                      <Trash2 className="size-3" /> Remove
                    </Button>
                  )}
                </div>
                <input ref={avatarInputRef} type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" className="hidden" onChange={handleAvatarFile} />
                <div className="flex gap-2 mt-2">
                  <Input
                    value={avatarUrlInput}
                    onChange={(e) => setAvatarUrlInput(e.target.value)}
                    placeholder="Or paste image URL https://..."
                    className="h-7 text-xs flex-1"
                  />
                  <Button type="button" variant="secondary" size="sm" onClick={handleAvatarUrlSave} disabled={avatarUploading} className="h-7 text-xs">
                    <LinkIcon className="size-3" /> Save URL
                  </Button>
                </div>
                <p className="text-[11px] text-muted-foreground mt-1">PNG, JPG, WEBP, SVG • Max 2MB • Shown in user menu</p>
                {avatarError && <p className="text-xs text-red-500 mt-1">{avatarError}</p>}
              </div>
            </div>

            <Separator />

            <div className="space-y-4">
              <div>
                <Label htmlFor="fullName">Full name</Label>
                <Input
                  id="fullName"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  className="mt-1 max-w-sm"
                  required
                />
              </div>
              <div>
                <Label htmlFor="email" className="flex items-center gap-1.5">
                  <Mail className="size-3.5" /> Email address
                </Label>
                <Input id="email" value={user?.email ?? ""} readOnly className="mt-1 max-w-sm" />
                <p className="mt-1 text-xs text-muted-foreground">
                  Email is used to sign in and can&apos;t be changed here.
                </p>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <Label htmlFor="org" className="flex items-center gap-1.5">
                    <Building2 className="size-3.5" /> Organization
                  </Label>
                  <Input id="org" value={user?.org_name ?? "—"} readOnly className="mt-1" />
                </div>
                <div>
                  <Label htmlFor="memberSince" className="flex items-center gap-1.5">
                    <Calendar className="size-3.5" /> Member since
                  </Label>
                  <Input
                    id="memberSince"
                    value={
                      user?.created_at
                        ? new Date(user.created_at).toLocaleDateString(undefined, {
                            year: "numeric",
                            month: "long",
                            day: "numeric",
                          })
                        : "—"
                    }
                    readOnly
                    className="mt-1"
                  />
                </div>
              </div>
            </div>

            <Button type="submit" disabled={savingProfile || fullName.trim() === user?.full_name}>
              {savingProfile && <Loader2 className="size-4 animate-spin" />}
              {savingProfile ? "Saving…" : "Save changes"}
            </Button>
          </form>
        </CardContent>
      </Card>

      {/* ── Payments ── */}
      <PaymentsConnectCard />
    </div>
  );
}