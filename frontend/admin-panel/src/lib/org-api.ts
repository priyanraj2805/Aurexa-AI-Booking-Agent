"use client";

import { api } from "@/lib/api";

export interface Unit {
  id: string;
  name: string;
  description?: string | null;
  is_active: boolean;
  providers_count?: number;
}

export interface Provider {
  id: string;
  full_name: string;
  role_label?: string | null;
  email?: string | null;
  phone?: string | null;
  is_active: boolean;
  units: string[];
  timezone?: string | null;
  slot_duration_minutes?: number | null;
  booking_window_days?: number | null;
}

export interface ProviderException {
  id: string;
  exception_date: string;
  exception_type: string;
  custom_open?: string | null;
  custom_close?: string | null;
  reason?: string | null;
}

export interface ProviderPreviewDay {
  date: string;
  status: string;
  available_times: string[];
}

export interface Fee {
  id: string;
  unit_id?: string | null;
  unit_name?: string | null;
  provider_id?: string | null;
  provider_name?: string | null;
  amount_paise: number;
  currency: string;
  label?: string | null;
  is_active: boolean;
}

export interface Member {
  id: string;
  email: string;
  full_name: string;
  roles: string[];
  provider_id?: string | null;
  unit_ids: string[];
  is_active: boolean;
}

export interface Invite {
  id: string;
  email: string;
  role_code: string;
  expires_at: string;
  accepted_at?: string | null;
  invite_token?: string | null;
  invite_link?: string | null;
  email_sent?: boolean;
}

export const orgApi = {
  units: () => api.get<Unit[]>("/catalog/units"),
  createUnit: (data: { name: string; description?: string }) =>
    api.post<Unit>("/catalog/units", data),
  deleteUnit: (id: string) => api.del(`/catalog/units/${id}`),
  updateUnit: (id: string, data: { name?: string; description?: string | null; is_active?: boolean }) =>
    api.patch<Unit>(`/catalog/units/${id}`, data),
  providers: () => api.get<Provider[]>("/catalog/providers"),
  createProvider: (data: Partial<Provider> & { full_name: string }) =>
    api.post<Provider>("/catalog/providers", data),
  updateProvider: (id: string, data: Partial<Provider>) =>
    api.patch<Provider>(`/catalog/providers/${id}`, data),
  deleteProvider: (id: string) => api.del(`/catalog/providers/${id}`),
  assign: (unitId: string, providerId: string) =>
    api.post(`/catalog/units/${unitId}/providers`, { provider_id: providerId }),
  unassign: (unitId: string, providerId: string) =>
    api.del(`/catalog/units/${unitId}/providers/${providerId}`),
  unitProviders: (unitId: string) => api.get<Provider[]>(`/catalog/units/${unitId}/providers`),
  members: () => api.get<Member[]>("/staff/members"),
  invites: () => api.get<Invite[]>("/staff/invites"),
  invite: (data: { email: string; role_code: string; unit_ids?: string[]; provider_id?: string | null }) =>
    api.post<Invite>("/staff/invites", data),
  accept: (token: string, full_name: string, password: string) =>
    api.post("/staff/accept", { token, full_name, password }),
  scope: () => api.get<{ org_type: string; roles: string[]; provider_id: string | null; unit_ids: string[] }>("/staff/me/scope"),
  providerSchedule: (id: string) =>
    api.get<{ day_of_week: number; is_active: boolean; open_time?: string | null; close_time?: string | null; break_start?: string | null; break_end?: string | null; is_override?: boolean }[]>(`/catalog/providers/${id}/schedule`),
  saveProviderSchedule: (id: string, days: { day_of_week: number; is_active: boolean; open_time?: string | null; close_time?: string | null; break_start?: string | null; break_end?: string | null }[]) =>
    api.put(`/catalog/providers/${id}/schedule`, days),
  providerExceptions: (id: string) => api.get<ProviderException[]>(`/catalog/providers/${id}/exceptions`),
  createProviderException: (id: string, data: { exception_date: string; exception_type: string; custom_open?: string | null; custom_close?: string | null; reason?: string | null }) =>
    api.post<ProviderException>(`/catalog/providers/${id}/exceptions`, data),
  deleteProviderException: (id: string, excId: string) => api.del(`/catalog/providers/${id}/exceptions/${excId}`),
  previewProviderSchedule: (id: string, date: string, days = 7) =>
    api.get<ProviderPreviewDay[]>(`/catalog/providers/${id}/schedule/preview?date=${encodeURIComponent(date)}&days=${days}`),
  fees: (unitId?: string, providerId?: string) => {
    const qs = new URLSearchParams();
    if (unitId) qs.append("unit_id", unitId);
    if (providerId) qs.append("provider_id", providerId);
    const q = qs.toString();
    return api.get<Fee[]>(`/catalog/fees${q ? `?${q}` : ""}`);
  },
  createFee: (data: { unit_id?: string | null; provider_id?: string | null; amount_paise: number; currency?: string; label?: string | null }) =>
    api.post<Fee>("/catalog/fees", data),
  updateFee: (id: string, data: { amount_paise?: number; label?: string | null; is_active?: boolean }) =>
    api.patch<Fee>(`/catalog/fees/${id}`, data),
  deleteFee: (id: string) => api.del(`/catalog/fees/${id}`),
};
