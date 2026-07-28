"use client";

import { useState, useEffect, useCallback } from "react";
import { api, uploadFile } from "@/lib/api";

// ── Types matching backend response schemas ──────

export interface Stats {
  total_bookings: number;
  active_sessions: number;
  confirmed_sessions: number;
  cancelled_sessions: number;
  has_schedule: boolean;
  bookings_this_week: number;
}

export interface BookingItem {
  id: string;
  customer_name: string;
  customer_email: string;
  customer_phone: string | null;
  slot_start: string | null;
  slot_end: string | null;
  created_at: string;
}

export interface ConversationItem {
  id: string;
  session_status: string;
  current_intent: string | null;
  collected_data: Record<string, unknown>;
  missing_fields: string[];
  created_at: string;
  updated_at: string;
  channel: string;
}

export interface MessageItem {
  id: string;
  role: "user" | "agent";
  content: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface PersonaConfig {
  agent_name?: string;
  greeting_message?: string;
  tone?: string;
  industry_context?: string;
  custom_instructions?: string;
}

export type FieldType = "text" | "email" | "phone" | "date" | "time" | "select" | "textarea" | "number";

export interface FieldDefinition {
  name: string;
  type: FieldType;
  label: string;
  required: boolean;
  placeholder: string;
  description: string;
  icon: string;
  options: string[];
  order: number;
}

export interface AgentConfig {
  required_fields: string[];
  field_definitions: FieldDefinition[] | null;
  custom_prompts: PersonaConfig | null;
  timezone: string;
  ui_brand_color: string;
  org_name: string;
  api_key: string;
  require_otp: boolean;
  max_booking_days_ahead: number;
  meeting_policy: {
    meeting_mode: "OFFLINE_ONLY" | "ONLINE_ONLY" | "HYBRID";
    allow_hybrid_selection: boolean;
    meeting_provider_default: "NONE" | "GOOGLE_MEET" | "MICROSOFT_TEAMS" | "ZOOM";
    available_meeting_types: ("ONLINE" | "OFFLINE")[];
    updated_at: string | null;
    updated_by: string | null;
  };
}

export interface SlotItem {
  id: string;
  start_time: string;
  end_time: string;
  is_booked: boolean;
}

export interface OnboardingStep {
  key: string;
  label: string;
  description: string;
  completed: boolean;
  link: string;
}

export interface OnboardingStatus {
  steps: OnboardingStep[];
  completed_count: number;
  total_count: number;
}

export interface ScheduleDay {
  day_of_week: number;  // 0=Mon, 6=Sun
  is_active: boolean;
  open_time: string;    // "HH:MM"
  close_time: string;
  break_start: string | null;
  break_end: string | null;
}

export interface ScheduleConfig {
  slot_duration_minutes: number;
  booking_window_days: number;
  timezone: string;
  days: ScheduleDay[];
}

export interface ExceptionItem {
  id?: string;
  exception_date: string;   // "YYYY-MM-DD"
  exception_type: "CLOSED" | "CUSTOM_HOURS";
  custom_open: string | null;
  custom_close: string | null;
  reason: string | null;
}

export interface SlotPreviewItem {
  date: string;
  available_times: string[];
}

export type HealthStatus = "ok" | "degraded" | "down";

export interface DependencyHealthCheck {
  status: HealthStatus;
  latency_ms: number | null;
  error: string | null;
}

export interface DependencyHealth {
  status: HealthStatus;
  request_id: string | null;
  trace_id: string | null;
  checks: {
    database: DependencyHealthCheck;
    llm: DependencyHealthCheck;
    email: DependencyHealthCheck;
  };
}

export type MeetingProviderType = "NONE" | "GOOGLE_MEET" | "MICROSOFT_TEAMS" | "ZOOM";

export interface MeetingProvidersConfig {
  google_meet_connected: boolean;
  microsoft_teams_connected: boolean;
  zoom_connected: boolean;
  active_meeting_provider: MeetingProviderType;
}

export interface SetActiveMeetingProviderPayload {
  provider: MeetingProviderType;
}

export interface SetActiveMeetingProviderResponse {
  status: string;
  active_meeting_provider: MeetingProviderType;
}

export interface MeetingProviderConnectResponse {
  auth_url: string;
}

// ── Generic fetch hook ───────────────────────────

function useApi<T>(path: string, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    if (!path) { setLoading(false); return; }
    setLoading(true);
    setError(null);
    try {
      const result = await api.get<T>(path);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);

  useEffect(() => {
    refetch();
  }, [refetch]);

  return { data, loading, error, refetch, setData };
}

// ── Exported hooks ───────────────────────────────

export function useStats() {
  return useApi<Stats>("/dashboard/stats");
}

export function useRecentBookings(limit = 20) {
  return useApi<BookingItem[]>(`/dashboard/recent-bookings?limit=${limit}`);
}

export function useConversations(limit = 200) {
  return useApi<ConversationItem[]>(`/dashboard/conversations?limit=${limit}`);
}

export function useAgentConfig() {
  return useApi<AgentConfig>("/dashboard/agent-config");
}

export function useSchedule() {
  return useApi<ScheduleConfig>("/dashboard/schedule");
}

export function useExceptions(year?: number, month?: number) {
  const qs = [year && `year=${year}`, month && `month=${month}`].filter(Boolean).join("&");
  return useApi<ExceptionItem[]>(`/dashboard/schedule/exceptions${qs ? `?${qs}` : ""}`, [year, month]);
}

export function useSlotPreview(startDate: string, days = 14) {
  return useApi<SlotPreviewItem[]>(
    startDate ? `/dashboard/schedule/preview?start_date=${startDate}&days=${days}` : "",
    [startDate, days]
  );
}

export function useOnboardingStatus() {
  return useApi<OnboardingStatus>("/dashboard/onboarding-status");
}

export function useDependencyHealth() {
  return useApi<DependencyHealth>("/dashboard/dependency-health");
}

export function useTranscript(sessionId: string | null) {
  return useApi<MessageItem[]>(
    sessionId ? `/dashboard/conversations/${sessionId}/messages` : "",
    [sessionId],
  );
}

export function useMeetingProvidersConfig() {
  return useApi<MeetingProvidersConfig>("/dashboard/meeting-providers/config");
}

export async function setActiveMeetingProvider(
  payload: SetActiveMeetingProviderPayload,
) {
  return api.put<SetActiveMeetingProviderResponse>(
    "/dashboard/meeting-providers/active-provider",
    payload,
  );
}

export async function connectGoogleMeet() {
  return api.post<MeetingProviderConnectResponse>(
    "/dashboard/meeting-providers/google-meet/connect",
  );
}

export async function disconnectGoogleMeet() {
  return api.post<{ status: string }>(
    "/dashboard/meeting-providers/google-meet/disconnect",
  );
}

export async function connectMicrosoftTeams() {
  return api.post<MeetingProviderConnectResponse>(
    "/dashboard/meeting-providers/microsoft-teams/connect",
  );
}

export async function disconnectMicrosoftTeams() {
  return api.post<{ status: string }>(
    "/dashboard/meeting-providers/microsoft-teams/disconnect",
  );
}

export async function connectZoom() {
  return api.post<MeetingProviderConnectResponse>(
    "/dashboard/meeting-providers/zoom/connect",
  );
}

export async function disconnectZoom() {
  return api.post<{ status: string }>("/dashboard/meeting-providers/zoom/disconnect");
}

// ── Human Handoff API ────────────────────────────

export interface TakeoverResponse {
  success: boolean;
  conversation_id: string;
  message: string;
  previous_messages: {
    id: string;
    role: string;
    content: string;
    sender_type: string;
    human_agent_id: string | null;
    human_agent_name: string | null;
    created_at: string;
  }[];
}

export interface ReleaseResponse {
  success: boolean;
  conversation_id: string;
  message: string;
}

export interface SendHumanMessageResponse {
  success: boolean;
  message: {
    id: string;
    content: string;
    sender_type: string;
    admin_name: string;
    created_at: string;
  };
}

export async function takeoverConversation(orgId: string, conversationId: string) {
  return api.post<TakeoverResponse>(
    `/organizations/${orgId}/conversations/${conversationId}/takeover`
  );
}

export async function releaseConversation(orgId: string, conversationId: string) {
  return api.post<ReleaseResponse>(
    `/organizations/${orgId}/conversations/${conversationId}/release`
  );
}

export async function sendHumanMessage(orgId: string, conversationId: string, content: string) {
  return api.post<SendHumanMessageResponse>(
    `/organizations/${orgId}/conversations/${conversationId}/send-human-message`,
    { content }
  );
}

// ── Knowledge Base (Vectorless RAG) Types & Hooks ──────────────────────────

export const MAX_DOCS_PER_ORG = 10;

export interface DocumentListItem {
  id: string;
  filename: string;
  pages: number;
  status: string;
  uploaded_at?: string | null;
}

export interface DocumentUploadResponse {
  id: string;
  filename: string;
  pages: number;
  status: string;
}

export interface CrawlResponse {
  crawl_id: string;
  status: string;
  message?: string;
}

export interface CrawlStatusResponse {
  crawl_id: string;
  status: string;
  pages_crawled?: number | null;
}

function useApiNullable<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    if (!path) { setData(null); setLoading(false); return; }
    setLoading(true);
    setError(null);
    try {
      const result = await api.get<T>(path);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);

  useEffect(() => { refetch(); }, [refetch]);

  return { data, loading, error, refetch, setData };
}

export function useDocuments() {
  return useApiNullable<DocumentListItem[]>("/documents/documents");
}

export async function uploadPdfDocument(file: File): Promise<DocumentUploadResponse> {
  return uploadFile<DocumentUploadResponse>("/documents/documents/upload", file, "file");
}

export async function deletePdfDocument(docId: string): Promise<void> {
  await api.del(`/documents/documents/${docId}`);
}

export async function startWebsiteCrawl(url: string): Promise<CrawlResponse> {
  return api.post<CrawlResponse>("/documents/website/crawl", { url });
}

export function useCrawlStatus(crawlId: string | null) {
  return useApiNullable<CrawlStatusResponse>(
    crawlId ? `/documents/website/crawl/status/${crawlId}` : null,
    [crawlId]
  );
}
