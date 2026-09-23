"use client";
import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "sonner";
import { Eye, EyeOff } from "lucide-react";

type UserOut = { id:string; email:string; full_name:string; is_active:boolean; role_codes:string[]; provider_id?:string|null };

export default function UsersPage() {
  const [users, setUsers] = useState<UserOut[]>([]);
  const [roles, setRoles] = useState<string[]>([]);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("manager");
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    try {
      const [u, r] = await Promise.all([
        apiFetch<UserOut[]>("/members/users"),
        apiFetch<string[]>("/roles").catch(()=>[]),
      ]);
      setUsers(u);
      // roles endpoint returns RoleOut[], but we also support string[]
      if (Array.isArray(r) && r.length && typeof r[0]==="string") setRoles(r as unknown as string[]);
      else if (Array.isArray(r)) setRoles((r as unknown as {code:string}[]).map(x=>x.code));
    } catch (e:any) { toast.error(e.message || "Failed to load"); }
  };
  useEffect(()=>{ load(); },[]);

  const invite = async () => {
    if(!email || !name || !password) return toast.error("All fields required");
    setLoading(true);
    try{
      await apiFetch("/members/users", { method:"POST", body: JSON.stringify({ email, full_name:name, password, role_codes:[role] }) });
      toast.success(`User ${email} added with ${role}`);
      setEmail(""); setName(""); setPassword("");
      load();
    }catch(e:any){ toast.error(e.message); } finally{ setLoading(false); }
  };

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-semibold">Users</h1>
      <p className="text-sm text-muted-foreground">Add users who can access the dashboard. Their access is controlled by their role.</p>

      <Card>
        <CardHeader><CardTitle>Invite User</CardTitle></CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div><Label>Email</Label><Input value={email} onChange={e=>setEmail(e.target.value)} placeholder="user@company.com" /></div>
          <div><Label>Full name</Label><Input value={name} onChange={e=>setName(e.target.value)} placeholder="Pawan Sharma" /></div>
          <div>
            <Label>Password (temp)</Label>
            <div className="relative">
              <Input type={showPass ? "text" : "password"} value={password} onChange={e=>setPassword(e.target.value)} placeholder="••••••••" className="pr-9" />
              <button type="button" onClick={()=>setShowPass(v=>!v)} className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-muted-foreground hover:text-foreground">
                {showPass ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
              </button>
            </div>
          </div>
          <div>
            <Label>Role</Label>
            <select value={role} onChange={e=>setRole(e.target.value)} className="w-full h-9 rounded-md border px-3 text-sm bg-background">
              <option value="owner">Owner (all)</option>
              <option value="manager">Manager (no roles.assign)</option>
              <option value="staff">Staff (bookings+inbox)</option>
              <option value="viewer">Viewer (read-only)</option>
              {(roles||[]).filter(r=>!["owner","manager","staff","viewer"].includes(r)).map(r=> <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
          <div className="md:col-span-2"><Button onClick={invite} disabled={loading}>{loading?"Inviting...":"Add User"}</Button></div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Organization Users ({users.length})</CardTitle></CardHeader>
        <CardContent className="overflow-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-muted-foreground border-b"><th className="py-2">Name</th><th>Email</th><th>Roles</th><th>Active</th></tr></thead>
            <tbody>
              {users.map(u=> <tr key={u.id} className="border-b"><td className="py-2">{u.full_name}</td><td>{u.email}</td><td>{(u.role_codes||[]).join(", ")}</td><td>{u.is_active?"Yes":"No"}</td></tr>)}
              {users.length===0 && <tr><td colSpan={4} className="py-4 text-center text-muted-foreground">No users yet</td></tr>}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
