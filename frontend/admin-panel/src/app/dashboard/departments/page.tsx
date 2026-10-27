"use client";

import { useEffect, useState } from "react";
import { orgApi, Unit, Provider, Fee } from "@/lib/org-api";
import { useAuth } from "@/lib/auth-context";
import { Pagination } from "@/components/pagination";
import { DoctorHoursEditor } from "@/components/doctor-hours-editor";
import { toast } from "sonner";

const paiseToRs = (p?: number | null) => (p ? String(p / 100) : "");

// Pagination: 10 per page, list window shows ~5 with vertical scroll
const PAGE_SIZE = 10;

export default function DepartmentsPage() {
  const { isDoctor } = useAuth();
  const [units, setUnits] = useState<Unit[]>([]);
  const [name, setName] = useState("");
  const [createUnitOpen, setCreateUnitOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [doctors, setDoctors] = useState<Provider[]>([]);
  const [allDoctors, setAllDoctors] = useState<Provider[]>([]);
  const [addId, setAddId] = useState("");
  const [addMemberOpen, setAddMemberOpen] = useState(false);
  const [docSearch, setDocSearch] = useState("");
  const [newDocName, setNewDocName] = useState("");
  const [newDocEmail, setNewDocEmail] = useState("");
  const [newDocPhone, setNewDocPhone] = useState("");
  const [newDocRole, setNewDocRole] = useState("");
  const [newDocOpen, setNewDocOpen] = useState(false);
  const [newDocSaving, setNewDocSaving] = useState(false);
  const [newDocInvite, setNewDocInvite] = useState("");
  const [newDocLink, setNewDocLink] = useState("");
  // Per-doctor edit (name/email/phone later) + login invite
  const [editOpenId, setEditOpenId] = useState("");
  const [editName, setEditName] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [editPhone, setEditPhone] = useState("");
  const [editSaving, setEditSaving] = useState(false);
  const [inviteForId, setInviteForId] = useState("");
  const [inviteToken, setInviteToken] = useState("");
  const [inviteLink, setInviteLink] = useState("");
  const [inviteBusy, setInviteBusy] = useState(false);
  // Per-department short description (shown to patients in picker)
  const [descEditId, setDescEditId] = useState("");
  const [descText, setDescText] = useState("");
  const [descSaving, setDescSaving] = useState(false);
  const [unitPage, setUnitPage] = useState(1);
  const [docPage, setDocPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [docsLoading, setDocsLoading] = useState(false);
  // Per-doctor Hours editor (inside department — no separate pages)
  const [hoursOpenId, setHoursOpenId] = useState("");
  const [hoursTick, setHoursTick] = useState(0);
  // Fees: department-level + per-doctor (₹ amounts, stored as paise)
  const [deptFees, setDeptFees] = useState<Fee[]>([]);
  const [feeOpenDept, setFeeOpenDept] = useState(false);
  const [deptAmount, setDeptAmount] = useState("");
  const [feeOpenId, setFeeOpenId] = useState("");
  const [docAmount, setDocAmount] = useState("");
  const [docFeeId, setDocFeeId] = useState("");
  const [feeSaving, setFeeSaving] = useState(false);

  const closeMemberPanels = () => {
    setHoursOpenId("");
    setFeeOpenId("");
    setEditOpenId("");
    setInviteForId("");
    setInviteToken("");
    setInviteLink("");
  };

  const loadUnits = async (keepSelection = true) => {
    try {
      const u = await orgApi.units();
      setUnits(u);
      if (!keepSelection || !selectedId) {
        if (u.length && !selectedId) setSelectedId(u[0].id);
      }
    } catch (e) {
      // Doctors are blocked here by design — no error noise for them.
      if (!isDoctor) toast.error(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  const loadDoctors = async (unitId: string) => {
    if (!unitId) return;
    setDocsLoading(true);
    try {
      const [inDept, all, fees] = await Promise.all([
        orgApi.unitProviders(unitId).catch(() => [] as Provider[]),
        orgApi.providers().catch(() => [] as Provider[]),
        orgApi.fees(unitId).catch(() => [] as Fee[]),
      ]);
      setDoctors(inDept);
      setAllDoctors(all);
      setDeptFees(fees);
      const deptFee = fees.find((f) => !f.provider_id);
      setDeptAmount(paiseToRs(deptFee?.amount_paise));
      setFeeOpenDept(false);
      setFeeOpenId("");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to load team");
    } finally {
      setDocsLoading(false);
    }
  };

  const saveDeptFee = async () => {
    if (!selectedId) return;
    const paise = Math.round(Number(deptAmount) * 100);
    if (!deptAmount || !(paise > 0)) {
      toast.error("Enter a valid fee amount");
      return;
    }
    setFeeSaving(true);
    try {
      const existing = deptFees.find((f) => !f.provider_id);
      if (existing) {
        await orgApi.updateFee(existing.id, { amount_paise: paise });
      } else {
        await orgApi.createFee({ unit_id: selectedId, amount_paise: paise, currency: "INR", label: "Standard visit" });
      }
      toast.success("Department fee saved");
      setFeeOpenDept(false);
      await loadDoctors(selectedId);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setFeeSaving(false);
    }
  };

  const openDocFee = async (p: Provider) => {
    if (feeOpenId === p.id) {
      setFeeOpenId("");
      return;
    }
    closeMemberPanels();
    setFeeOpenId(p.id);
    setDocFeeId("");
    try {
      const fees = await orgApi.fees(undefined, p.id);
      const mine = fees.find((f) => f.provider_id === p.id && !f.unit_id)
        ?? fees.find((f) => f.provider_id === p.id);
      setDocAmount(paiseToRs(mine?.amount_paise));
      setDocFeeId(mine?.id || "");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to load fee");
      setFeeOpenId((current) => current === p.id ? "" : current);
    }
  };

  const saveDocFee = async (p: Provider) => {
    const paise = Math.round(Number(docAmount) * 100);
    if (!docAmount || !(paise > 0)) {
      toast.error("Enter a valid fee amount");
      return;
    }
    setFeeSaving(true);
    try {
      if (docFeeId) {
        await orgApi.updateFee(docFeeId, { amount_paise: paise });
      } else {
        await orgApi.createFee({ provider_id: p.id, amount_paise: paise, currency: "INR", label: "Consultation" });
      }
      toast.success(`Fee saved for ${p.full_name}`);
      setFeeOpenId("");
      await loadDoctors(selectedId);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setFeeSaving(false);
    }
  };

  useEffect(() => {
    loadUnits(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selectedId) {
      setDocSearch("");
      setAddId("");
      setAddMemberOpen(false);
      setDocPage(1);
      closeMemberPanels();
      setNewDocOpen(false);
      setNewDocName("");
      setNewDocEmail("");
      setNewDocPhone("");
      setNewDocRole("");
      setNewDocInvite("");
      setNewDocLink("");
      setFeeOpenDept(false);
      setFeeOpenId("");
      loadDoctors(selectedId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  useEffect(() => {
    setUnitPage(1);
  }, [search]);

  useEffect(() => {
    setDocPage(1);
  }, [docSearch]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      const u = await orgApi.createUnit({ name: name.trim() });
      setName("");
      setCreateUnitOpen(false);
      toast.success("Department added");
      await loadUnits(false);
      setSelectedId(u.id);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Create failed");
    }
  };

  const remove = async (id: string) => {
    if (!confirm("Delete this department?")) return;
    try {
      await orgApi.deleteUnit(id);
      toast.success("Deleted");
      if (selectedId === id) setSelectedId("");
      await loadUnits(false);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Delete failed");
    }
  };

  const startDescEdit = (u: Unit) => {
    setDescEditId(u.id);
    setDescText(u.description || "");
  };

  const saveDesc = async (u: Unit) => {
    setDescSaving(true);
    try {
      await orgApi.updateUnit(u.id, { description: descText.trim() || null });
      toast.success(`Description saved for ${u.name}`);
      setDescEditId("");
      await loadUnits(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setDescSaving(false);
    }
  };

  const addDoctor = async () => {
    if (!addId || !selectedId) return;
    try {
      await orgApi.assign(selectedId, addId);
      toast.success("Member added to department");
      setAddId("");
      setAddMemberOpen(false);
      await loadDoctors(selectedId);
      await loadUnits(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Assign failed");
    }
  };

  const createAndAddDoctor = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newDocName.trim() || !selectedId) return;
    const email = newDocEmail.trim().toLowerCase();
    if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      toast.error("Enter a valid email or leave it empty");
      return;
    }
    setNewDocSaving(true);
    setNewDocInvite("");
    try {
      const p = await orgApi.createProvider({
        full_name: newDocName.trim(),
        role_label: newDocRole.trim() || undefined,
        email: email || undefined,
        phone: newDocPhone.trim() || undefined,
      });
      await orgApi.assign(selectedId, p.id);
      // Doctor login invite right here — emailed automatically + link shown.
      if (email) {
        try {
          const inv = await orgApi.invite({ email, role_code: "staff", provider_id: p.id });
          const link = inv.invite_link || inv.invite_token || "";
          setNewDocInvite(inv.invite_token || "");
          setNewDocLink(link);
          if (link) {
            try {
              await navigator.clipboard.writeText(link);
            } catch {
              // clipboard may be blocked — link stays visible below
            }
          }
          toast.success(
            inv.email_sent
              ? `Invite emailed to ${email} + link copied`
              : `${p.full_name} added + invite link ready — share it`
          );
        } catch (invErr) {
      toast.success(`${p.full_name} added to ${selected?.name || "department"}`);
          toast.error(invErr instanceof Error ? invErr.message : "Invite failed — create it from Staff");
        }
      } else {
        toast.success(`${p.full_name} added to ${selected?.name}`);
      }
      setNewDocName("");
      setNewDocEmail("");
      setNewDocPhone("");
      setNewDocRole("");
      if (!email) setNewDocOpen(false);
      await loadDoctors(selectedId);
      await loadUnits(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Create failed");
    } finally {
      setNewDocSaving(false);
    }
  };

  const removeDoctor = async (pid: string) => {
    closeMemberPanels();
    const member = doctors.find((doctor) => doctor.id === pid);
    const memberName = member?.full_name || "this team member";
    const departmentName = selected?.name || "this department";
    if (!confirm(`Remove ${memberName} from ${departmentName}?`)) return;

    try {
      await orgApi.unassign(selectedId, pid);
      toast.success("Removed from department");
      await loadDoctors(selectedId);
      await loadUnits(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Remove failed");
    }
  };

  const startEditDoc = (p: Provider) => {
    if (editOpenId === p.id) {
      setEditOpenId("");
      return;
    }
    closeMemberPanels();
    setEditOpenId(p.id);
    setEditName(p.full_name);
    setEditEmail(p.email || "");
    setEditPhone(p.phone || "");
  };

  const saveEditDoc = async (p: Provider) => {
    if (!editName.trim()) {
      toast.error("Name is required");
      return;
    }
    if (editEmail.trim() && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(editEmail.trim())) {
      toast.error("Enter a valid email or leave it empty");
      return;
    }
    setEditSaving(true);
    try {
      await orgApi.updateProvider(p.id, {
        full_name: editName.trim(),
        email: editEmail.trim() || null,
        phone: editPhone.trim() || null,
      } as Partial<Provider>);
      toast.success("Member updated");
      setEditOpenId("");
      await loadDoctors(selectedId);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Update failed");
    } finally {
      setEditSaving(false);
    }
  };

  const inviteDoctor = async (p: Provider) => {
    const email = (p.email || "").trim().toLowerCase();
    if (!email) {
      toast.error("Add an email via Edit first, then send the invite");
      startEditDoc(p);
      return;
    }
    if (inviteForId === p.id) {
      setInviteForId("");
      setInviteToken("");
      setInviteLink("");
      return;
    }
    closeMemberPanels();
    setInviteForId(p.id);
    setInviteToken("");
    setInviteLink("");
    setInviteBusy(true);
    try {
      const inv = await orgApi.invite({ email, role_code: "staff", provider_id: p.id });
      const link = inv.invite_link || inv.invite_token || "";
      setInviteToken(inv.invite_token || "");
      setInviteLink(link);
      if (link) {
        try {
          await navigator.clipboard.writeText(link);
        } catch {
          // visible below
        }
      }
      toast.success(
        inv.email_sent ? `Invite emailed to ${email}` : `Invite ready for ${p.full_name} — share the link`
      );
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Invite failed");
      setInviteForId((current) => current === p.id ? "" : current);
    } finally {
      setInviteBusy(false);
    }
  };

  const toggleHours = (p: Provider) => {
    if (hoursOpenId === p.id) {
      setHoursOpenId("");
      return;
    }
    closeMemberPanels();
    setHoursTick((t) => t + 1);
    setHoursOpenId(p.id);
  };

  const handleHoursSaved = async () => {
    setHoursOpenId("");
    await loadDoctors(selectedId);
  };

  if (loading) return <p className="text-sm text-muted-foreground">Loading departments…</p>;
  if (isDoctor) {
    return (
      <div className="border border-border rounded-xl p-8 bg-card text-center">
        <p className="font-semibold">Not available for team logins</p>
        <p className="text-sm text-muted-foreground mt-1">Departments are managed by your organization. You see only your own bookings.</p>
      </div>
    );
  }

  const filtered = units.filter((u) => u.name.toLowerCase().includes(search.toLowerCase()));
  const selected = units.find((u) => u.id === selectedId);
  const inDeptIds = new Set(doctors.map((d) => d.id));
  const addable = allDoctors.filter((d) => !inDeptIds.has(d.id));
  const filteredDocs = doctors.filter((d) => d.full_name.toLowerCase().includes(docSearch.toLowerCase()));
  const unitPageSafe = Math.min(unitPage, Math.max(1, Math.ceil(filtered.length / PAGE_SIZE)));
  const unitSlice = filtered.slice((unitPageSafe - 1) * PAGE_SIZE, unitPageSafe * PAGE_SIZE);
  const docPageSafe = Math.min(docPage, Math.max(1, Math.ceil(filteredDocs.length / PAGE_SIZE)));
  const docSlice = filteredDocs.slice((docPageSafe - 1) * PAGE_SIZE, docPageSafe * PAGE_SIZE);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Departments</h1>
          <p className="text-sm text-muted-foreground">Organize your team into departments. Visitors select a department first, then a team member.</p>
      </div>

      {!units.length && !search ? (
        <div className="border border-border rounded-xl p-4 bg-card grid gap-3 content-start">
          <p className="text-sm text-muted-foreground">No departments yet. Add one to get started.</p>
          {createUnitOpen ? (
            <form onSubmit={create} className="flex flex-col gap-2 sm:flex-row">
              <input
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="New department name…"
                aria-label="New department name"
                className="min-w-0 flex-1 px-3 py-2 bg-background border border-border rounded-lg text-sm outline-none focus:border-primary"
              />
              <button type="submit" className="shrink-0 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground">Add</button>
              <button
                type="button"
                onClick={() => {
                  setCreateUnitOpen(false);
                  setName("");
                }}
                className="shrink-0 rounded-lg border border-border px-4 py-2 text-sm font-medium hover:bg-muted"
              >
                Cancel
              </button>
            </form>
          ) : (
            <button
              type="button"
              onClick={() => setCreateUnitOpen(true)}
              className="justify-self-start rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground"
            >
              Add department
            </button>
          )}
        </div>
      ) : (
        <>
        {!!search && (
          <p className="text-xs text-muted-foreground -mt-2">
            {filtered.length} result{filtered.length === 1 ? "" : "s"} for “{search}”{" "}
            <button onClick={() => setSearch("")} className="text-primary hover:underline">
              Clear
            </button>
          </p>
        )}
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 items-stretch">
          {/* Left: departments outer card with inner cards — like doctors panel */}
          <div className="border border-border rounded-xl p-3 sm:p-4 bg-card flex flex-col gap-3 h-full min-h-0 xl:min-h-[560px] min-w-0 overflow-hidden">
            <div className="flex items-center justify-between gap-2">
              <h2 className="font-semibold">All departments</h2>
              <div className="flex items-center gap-2 shrink-0">
                <span className="text-[11px] px-2 py-0.5 rounded-full bg-secondary whitespace-nowrap">
                  {filtered.length} total
                </span>
                {selected && (
                  <button
                    onClick={() => remove(selected.id)}
                    aria-label={`Delete ${selected.name} department`}
                    title={`Delete ${selected.name} department`}
                    className="text-xs text-destructive hover:underline whitespace-nowrap"
                  >
                    Delete department
                  </button>
                )}
              </div>
            </div>
            {/* Keep department creation hidden until requested. */}
            <div className="flex min-w-0 flex-wrap items-start gap-2">
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search departments…"
                aria-label="Search departments"
                className="min-w-0 flex-1 basis-40 px-3 py-2 bg-background border border-border rounded-lg text-sm outline-none focus:border-primary"
              />
              {!createUnitOpen && (
                <button
                  type="button"
                  onClick={() => setCreateUnitOpen(true)}
                  className="shrink-0 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground"
                >
                  Add department
                </button>
              )}
              {createUnitOpen ? (
                <form onSubmit={create} className="flex w-full min-w-0 flex-wrap gap-2">
                  <input
                    autoFocus
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="New department name…"
                    aria-label="New department name"
                    className="min-w-0 flex-1 basis-40 px-3 py-2 bg-background border border-border rounded-lg text-sm outline-none focus:border-primary"
                  />
                  <button
                    type="submit"
                    className="shrink-0 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground"
                  >
                    Add
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setCreateUnitOpen(false);
                      setName("");
                    }}
                    className="shrink-0 rounded-lg border border-border px-4 py-2 text-sm font-medium hover:bg-muted"
                  >
                    Cancel
                  </button>
                </form>
              ) : null}
            </div>
            <div className="grid gap-3 content-start flex-1 min-h-0 max-h-[320px] md:max-h-[420px] overflow-y-auto custom-scrollbar pr-1">
              {unitSlice.map((u) => (
                <button
                  key={u.id}
                  onClick={() => setSelectedId(u.id)}
                  className={`h-fit w-full min-w-0 self-start text-left border rounded-xl p-4 bg-card transition-colors ${
                    selectedId === u.id ? "border-primary ring-1 ring-primary/30" : "border-border hover:border-primary/40"
                  }`}
                >
                <div className="flex items-center justify-between gap-2 min-w-0">
                  <p className="font-medium truncate min-w-0">{u.name}</p>
                  <span className="text-[11px] px-2 py-0.5 rounded-full bg-secondary whitespace-nowrap shrink-0">
                    {u.providers_count ?? 0} members
                  </span>
                </div>
                {selectedId === u.id && (
                  <div className="mt-1.5" onClick={(e) => e.stopPropagation()}>
                    {descEditId === u.id ? (
                      <div className="flex flex-col gap-1.5">
                        <textarea
                          value={descText}
                          onChange={(e) => setDescText(e.target.value)}
                          placeholder="Short description, e.g. Heart care, ECG, echo…"
                          rows={2}
                          autoFocus
                          className="w-full px-2.5 py-1.5 bg-background border border-border rounded-lg text-xs outline-none focus:border-primary resize-none"
                        />
                        <div className="flex gap-2">
                          <button onClick={() => saveDesc(u)} disabled={descSaving} className="px-2.5 py-1 rounded-md bg-primary text-primary-foreground text-xs font-semibold disabled:opacity-50">
                            {descSaving ? "Saving…" : "Save"}
                          </button>
                          <button onClick={() => setDescEditId("")} className="text-xs text-muted-foreground hover:underline">
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-xs text-muted-foreground">
                          {u.description || <span className="italic">No description yet — add one so visitors know what this department offers.</span>}
                        </p>
                        <button onClick={() => startDescEdit(u)} className="text-xs text-primary hover:underline whitespace-nowrap shrink-0">
                          {u.description ? "Edit" : "Add"}
                        </button>
                      </div>
                    )}
                  </div>
                )}
                <p className="text-xs text-muted-foreground mt-1">
                  {selectedId === u.id ? "Viewing team →" : "Click to view team"}
                </p>
                </button>
              ))}
              {!filtered.length && (
                <p className="text-sm text-muted-foreground">No department matches “{search}”.</p>
              )}
            </div>
            <Pagination page={unitPageSafe} pageSize={PAGE_SIZE} total={filtered.length} onPageChange={setUnitPage} />
          </div>

          {/* Right: doctors in selected department */}
          <div className="border border-border rounded-xl p-3 sm:p-4 bg-card flex flex-col gap-3 h-full min-h-0 xl:min-h-[560px] min-w-0 overflow-hidden">
            {!selected ? (
              <p className="text-sm text-muted-foreground">Select a department.</p>
            ) : (
              <>
                <div className="flex items-center justify-between gap-2">
                  <h2 className="font-semibold truncate min-w-0">{selected.name} — team</h2>
                    <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={() => {
                        closeMemberPanels();
                        setFeeOpenDept((v) => !v);
                      }}
                      className="text-xs font-semibold px-2.5 py-1.5 rounded-lg border border-border hover:bg-accent whitespace-nowrap"
                    >
                      {feeOpenDept ? "Close" : `Fee${deptFees.find((f) => !f.provider_id) ? `: ₹${paiseToRs(deptFees.find((f) => !f.provider_id)?.amount_paise)}` : ""}`}
                    </button>
                  </div>
                </div>
                {/* Inline form for creating and assigning a new team member. */}
                {newDocOpen && (
                  <form onSubmit={createAndAddDoctor} className="grid gap-2 border border-primary/30 bg-primary/5 rounded-lg p-2">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-medium">Create a new team member</p>
                      <button
                        type="button"
                        onClick={() => setNewDocOpen(false)}
                        className="text-xs text-muted-foreground hover:text-foreground"
                      >
                        Back
                      </button>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <input
                        value={newDocName}
                        onChange={(e) => setNewDocName(e.target.value)}
                        placeholder="Name, e.g. Priya Sharma"
                        aria-label="New member name"
                        autoFocus
                        className="flex-1 min-w-[160px] px-3 py-2 bg-background border border-border rounded-lg text-sm outline-none focus:border-primary"
                      />
                      <input
                        value={newDocRole}
                        onChange={(e) => setNewDocRole(e.target.value)}
                        placeholder="Role, e.g. Doctor, Stylist"
                        aria-label="Member role"
                        className="flex-1 min-w-[140px] px-3 py-2 bg-background border border-border rounded-lg text-sm outline-none focus:border-primary"
                      />
                      <input
                        value={newDocEmail}
                        onChange={(e) => setNewDocEmail(e.target.value)}
                        placeholder="Email for login (optional)"
                        type="email"
                        aria-label="Email for login"
                        className="flex-1 min-w-[160px] px-3 py-2 bg-background border border-border rounded-lg text-sm outline-none focus:border-primary"
                      />
                      <input
                        value={newDocPhone}
                        onChange={(e) => setNewDocPhone(e.target.value)}
                        placeholder="Phone (optional)"
                        aria-label="Phone"
                        className="flex-1 min-w-[140px] px-3 py-2 bg-background border border-border rounded-lg text-sm outline-none focus:border-primary"
                      />
                      <button disabled={newDocSaving} className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-semibold whitespace-nowrap shrink-0 disabled:opacity-50">
                        {newDocSaving ? "…" : "Create"}
                      </button>
                    </div>
                    {(newDocLink || newDocInvite) && (
                      <div className="grid min-w-0 max-w-full gap-1.5 overflow-hidden bg-background border border-border rounded-lg px-3 py-2">
                        <p className="text-[11px] text-muted-foreground">Login link — send it to the doctor (email already sent when configured):</p>
                        <div className="flex min-w-0 items-start gap-2">
                          <code className="block min-w-0 flex-1 break-all text-xs">{newDocLink || newDocInvite}</code>
                          <button
                            type="button"
                            onClick={async () => {
                              try {
                                await navigator.clipboard.writeText(newDocLink || newDocInvite);
                                toast.success("Invite link copied");
                              } catch {
                                toast.error("Copy manually");
                              }
                            }}
                            className="text-xs font-semibold text-primary hover:underline shrink-0"
                          >
                            Copy link
                          </button>
                        </div>
                      </div>
                    )}
                  </form>
                )}
                {/* One add action reveals options for existing or new team members. */}
                <div className="flex min-w-0 flex-wrap items-start gap-2">
                  <input
                    value={docSearch}
                    onChange={(e) => setDocSearch(e.target.value)}
                    placeholder="Search team…"
                    aria-label="Search team in department"
                    className="min-w-0 flex-1 basis-40 px-3 py-2 bg-background border border-border rounded-lg text-sm outline-none focus:border-primary"
                  />
                  {!addMemberOpen && !newDocOpen && (
                    <button
                      type="button"
                      onClick={() => setAddMemberOpen(true)}
                      className="shrink-0 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground"
                    >
                      Add member
                    </button>
                  )}
                  {addMemberOpen && (
                    <div className="flex w-full min-w-0 flex-wrap gap-2">
                      {!!addable.length && (
                        <>
                          <select value={addId} onChange={(e) => setAddId(e.target.value)} aria-label="Add team member" className="min-w-0 flex-1 basis-40 px-3 py-2 bg-background border border-border rounded-lg text-sm truncate">
                            <option value="">Select existing member…</option>
                            {addable.map((d) => (
                              <option key={d.id} value={d.id}>{d.full_name}</option>
                            ))}
                          </select>
                          <button onClick={addDoctor} disabled={!addId} className="shrink-0 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50">
                            Add
                          </button>
                        </>
                      )}
                      <button
                        type="button"
                        onClick={() => {
                          setAddMemberOpen(false);
                          setAddId("");
                          setNewDocOpen(true);
                        }}
                        className="shrink-0 rounded-lg border border-primary/40 px-4 py-2 text-sm font-medium text-primary hover:bg-primary/10"
                      >
                        Create new member
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setAddMemberOpen(false);
                          setAddId("");
                        }}
                        className="shrink-0 rounded-lg border border-border px-4 py-2 text-sm font-medium hover:bg-muted"
                      >
                        Cancel
                      </button>
                    </div>
                  )}
              </div>
              {/* Department fee editor */}
              {feeOpenDept && (
                <div className="flex flex-col min-[560px]:flex-row gap-2 border border-border bg-secondary/30 rounded-lg p-2">
                  <input
                    value={deptAmount}
                    onChange={(e) => setDeptAmount(e.target.value)}
                    placeholder="Fee in ₹, e.g. 500"
                    inputMode="decimal"
                    aria-label="Department fee in rupees"
                    className="flex-1 min-w-0 px-3 py-2 bg-background border border-border rounded-lg text-sm outline-none focus:border-primary"
                  />
                  <button onClick={saveDeptFee} disabled={feeSaving} className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-semibold whitespace-nowrap shrink-0 disabled:opacity-50">
                    {feeSaving ? "Saving…" : "Save fee"}
                  </button>
                </div>
              )}
                <p className="text-[11px] text-muted-foreground">
                Department fee applies to all members unless a member sets their own fee.
              </p>
                {docsLoading ? (
                  <p className="text-sm text-muted-foreground">Loading doctors…</p>
                ) : !doctors.length ? (
                  <p className="text-sm text-muted-foreground">No team members in {selected.name} yet. Add one above.</p>
                ) : (
                  <div className="grid gap-2 content-start flex-1 min-h-0 max-h-[320px] md:max-h-[420px] overflow-y-auto custom-scrollbar pr-1">
                    {docSlice.map((d) => (
                      <div key={d.id} className="border border-border rounded-lg p-3 text-sm min-w-0">
                        <div className="flex items-center justify-between gap-2">
                          <div className="min-w-0">
                            <p className="font-medium truncate">{d.full_name}</p>
                            <p className="text-xs text-muted-foreground truncate">
                              {d.timezone || "Org default time"} · {d.slot_duration_minutes || 60} min
                            </p>
                            {d.email || d.phone ? (
                              <p className="text-xs text-muted-foreground truncate">
                                {[d.email, d.phone].filter(Boolean).join(" · ")}
                              </p>
                            ) : (
                              <p className="text-[11px] text-amber-500/90">No email yet — Edit to add login</p>
                            )}
                          </div>
                          <div className="flex flex-wrap justify-end gap-x-2 gap-y-1 shrink-0">
                            <button onClick={() => toggleHours(d)} className="text-xs text-primary hover:underline whitespace-nowrap">
                              {hoursOpenId === d.id ? "Close" : "Hours"}
                            </button>
                            <button onClick={() => openDocFee(d)} className="text-xs text-primary hover:underline whitespace-nowrap">
                              {feeOpenId === d.id ? "Close" : "Fee"}
                            </button>
                            <button onClick={() => startEditDoc(d)} className="text-xs text-primary hover:underline whitespace-nowrap">
                              {editOpenId === d.id ? "Close" : "Edit"}
                            </button>
                            <button onClick={() => inviteDoctor(d)} disabled={inviteBusy} className="text-xs text-primary hover:underline whitespace-nowrap disabled:opacity-50">
                              {inviteForId === d.id ? "Hide link" : "Invite"}
                            </button>
                            <button onClick={() => removeDoctor(d.id)} className="text-xs text-destructive hover:underline whitespace-nowrap">
                              Remove
                            </button>
                          </div>
                        </div>
                        {editOpenId === d.id && (
                          <div className="mt-2 grid gap-2 border border-border rounded-lg p-2">
                            <div className="flex flex-wrap gap-2">
                              <input
                                value={editName}
                                onChange={(e) => setEditName(e.target.value)}
                                placeholder="Team member name"
                                aria-label="Team member name"
                                className="flex-1 min-w-[140px] px-3 py-2 bg-background border border-border rounded-lg text-sm outline-none focus:border-primary"
                              />
                              <input
                                value={editEmail}
                                onChange={(e) => setEditEmail(e.target.value)}
                                placeholder="Email for login"
                                type="email"
                                aria-label="Team member email"
                                className="flex-1 min-w-[140px] px-3 py-2 bg-background border border-border rounded-lg text-sm outline-none focus:border-primary"
                              />
                              <input
                                value={editPhone}
                                onChange={(e) => setEditPhone(e.target.value)}
                                placeholder="Phone"
                                aria-label="Phone"
                                className="flex-1 min-w-[120px] px-3 py-2 bg-background border border-border rounded-lg text-sm outline-none focus:border-primary"
                              />
                              <button onClick={() => saveEditDoc(d)} disabled={editSaving} className="px-3 py-2 rounded-lg bg-primary text-primary-foreground text-xs font-semibold whitespace-nowrap shrink-0 disabled:opacity-50">
                                {editSaving ? "…" : "Save"}
                              </button>
                            </div>
                            <p className="text-[11px] text-muted-foreground">You can add the email later too — then send the login link via Invite.</p>
                          </div>
                        )}
                        {inviteForId === d.id && (inviteLink || inviteToken) && (
                          <div className="mt-2 grid min-w-0 max-w-full gap-1 overflow-hidden bg-background border border-border rounded-lg px-3 py-2">
                            <p className="text-[11px] text-muted-foreground">Login link — send it to them:</p>
                            <div className="flex min-w-0 items-start gap-2">
                              <code className="block min-w-0 flex-1 break-all text-xs">{inviteLink || inviteToken}</code>
                              <button
                                type="button"
                                onClick={async () => {
                                  try {
                                    await navigator.clipboard.writeText(inviteLink || inviteToken);
                                    toast.success("Invite link copied");
                                  } catch {
                                    toast.error("Copy manually");
                                  }
                                }}
                                className="text-xs font-semibold text-primary hover:underline shrink-0"
                              >
                                Copy link
                              </button>
                            </div>
                          </div>
                        )}
                        {feeOpenId === d.id && (
                          <div className="mt-2 flex flex-col min-[560px]:flex-row gap-2">
                            <input
                              value={docAmount}
                              onChange={(e) => setDocAmount(e.target.value)}
                              placeholder="Fee in ₹, e.g. 500 (empty = dept fee)"
                              inputMode="decimal"
                              aria-label="Team member fee in rupees"
                              className="flex-1 min-w-0 px-3 py-2 bg-background border border-border rounded-lg text-sm outline-none focus:border-primary"
                            />
                            <button onClick={() => saveDocFee(d)} disabled={feeSaving} className="px-3 py-2 rounded-lg bg-primary text-primary-foreground text-xs font-semibold whitespace-nowrap shrink-0 disabled:opacity-50">
                              {feeSaving ? "Saving…" : "Save fee"}
                            </button>
                          </div>
                        )}
                        {hoursOpenId === d.id && (
                          <DoctorHoursEditor
                            key={`${d.id}-${hoursTick}`}
                            provider={d}
                            onSaved={handleHoursSaved}
                          />
                        )}
                      </div>
                    ))}
                    {!filteredDocs.length && (
                      <p className="text-sm text-muted-foreground">Nobody matches “{docSearch}”.</p>
                    )}
                  </div>
                )}
                <Pagination page={docPageSafe} pageSize={PAGE_SIZE} total={filteredDocs.length} onPageChange={setDocPage} />
              </>
            )}
          </div>
        </div>
        </>
      )}
    </div>
  );
}
