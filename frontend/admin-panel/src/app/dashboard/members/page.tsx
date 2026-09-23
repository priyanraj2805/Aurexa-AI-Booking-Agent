"use client";
import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "sonner";
import { Eye, EyeOff } from "lucide-react";

type MemberOut = { provider_id:string; user_id?:string|null; full_name:string; email?:string|null; phone?:string|null; role_label?:string|null; is_active:boolean; role_codes:string[] };

export default function MembersPage() {
  const [members, setMembers] = useState<MemberOut[]>([]);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [roleLabel, setRoleLabel] = useState("Stylist");
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    try { const m = await apiFetch<MemberOut[]>("/members/members"); setMembers(m); } catch(e:any){ toast.error(e.message); }
  };
  useEffect(()=>{ load(); },[]);

  const invite = async () => {
    if(!email || !name || !password) return toast.error("email, name, password required");
    setLoading(true);
    try{
      await apiFetch("/members/members", { method:"POST", body: JSON.stringify({ email, full_name:name, password, phone: phone||null, role_label: roleLabel, role_codes:["staff"] })});
      toast.success(`Member ${name} added — they can login at dotstark.tech and see own dashboard (My Bookings, own services). No Users/Members menu.`);
      setEmail(""); setName(""); setPhone(""); setPassword("");
      load();
    }catch(e:any){ toast.error(e.message); } finally{ setLoading(false); }
  };

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-semibold">Members</h1>
      <p className="text-sm text-muted-foreground">Add team members who manage their own bookings and services. They use the same login page and see only their own dashboard.</p>

      <Card>
        <CardHeader><CardTitle>Add Member</CardTitle></CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div><Label>Email (login)</Label><Input value={email} onChange={e=>setEmail(e.target.value)} placeholder="robert@salon.com" /></div>
          <div><Label>Full name</Label><Input value={name} onChange={e=>setName(e.target.value)} placeholder="Robert — Stylist" /></div>
          <div><Label>Phone</Label><Input value={phone} onChange={e=>setPhone(e.target.value)} placeholder="+91..." /></div>
          <div><Label>Role label</Label><Input value={roleLabel} onChange={e=>setRoleLabel(e.target.value)} placeholder="Stylist / Doctor / Coach" /></div>
          <div>
            <Label>Temp password</Label>
            <div className="relative">
              <Input type={showPass ? "text" : "password"} value={password} onChange={e=>setPassword(e.target.value)} placeholder="••••••••" className="pr-9" />
              <button type="button" onClick={()=>setShowPass(v=>!v)} className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-muted-foreground hover:text-foreground">
                {showPass ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
              </button>
            </div>
          </div>
          <div className="flex items-end"><Button onClick={invite} disabled={loading}>{loading?"Adding...":"Add Member"}</Button></div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Organization Members ({members.length})</CardTitle></CardHeader>
        <CardContent className="overflow-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-muted-foreground border-b"><th className="py-2">Name</th><th>Email</th><th>Phone</th><th>Role label</th><th>Login</th><th>Roles</th></tr></thead>
            <tbody>
              {members.map(m=> <tr key={m.provider_id} className="border-b"><td className="py-2">{m.full_name}</td><td>{m.email||"—"}</td><td>{m.phone||"—"}</td><td>{m.role_label||"—"}</td><td>{m.user_id ? "Yes (own dashboard)" : "No login"}</td><td>{(m.role_codes||[]).join(", ")||"—"}</td></tr>)}
              {members.length===0 && <tr><td colSpan={6} className="py-4 text-center text-muted-foreground">No members yet — add one above</td></tr>}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
