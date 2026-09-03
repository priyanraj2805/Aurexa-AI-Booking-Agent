"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { api, uploadFile } from "@/lib/api";
import { isDateRangeValid, toApiDate, toApiDateTo } from "@/components/filter-bar";

// ── Types matching backend response schemas ──────

export interface DailyCount {
  date: string;
  count: number;
}

export interface SearchBookingItem {
  id: string;
  customer_name: string;
  customer_email: string;
  customer_phone: string | null;
  slot_start: string | null;
  created_at: string;
}

export interface SearchConversationItem {
  id: string;
  session_status: string;
  channel: string;
  customer_name: string | null;
  customer_email: string | null;
  customer_phone: string | null;
  updated_at: string;
}

export interface GlobalSearchResults {
  bookings: SearchBookingItem[];
  conversations: SearchConversationItem[];
}

/**
 * Debounced global search across bookings + conversations.
 * Skips queries shorter than `minChars`; stale responses are ignored.
 */
export function useGlobalSearch(query: string, minChars = 2, debounceMs = 300) {
  const [results, setResults] = useState<GlobalSearchResults | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestQuery = useRef("");

  const trimmed = query.trim();

  useEffect(() => {
    if (trimmed.length < minChars) {
      latestQuery.current = "";
      setResults(null);
      setLoading(false);
      setError(null);
      return;
    }

    latestQuery.current = trimmed;
    setLoading(true);
    setError(null);

    const timer = setTimeout(async () => {
      try {
        const data = await api.get<GlobalSearchResults>(
          `/dashboard/search?q=${encodeURIComponent(trimmed)}&limit=5`
        );
        if (latestQuery.current !== trimmed) return; // stale response
        setResults(data);
        setError(null);
      } catch (err) {
        if (latestQuery.current !== trimmed) return;
        setError(err instanceof Error ? err.message : "Search failed");
      } finally {
        if (latestQuery.current === trimmed) setLoading(false);
      }
    }, debounceMs);

    return () => clearTimeout(timer);
  }, [trimmed, minChars, debounceMs]);

  return { results, loading, error };
}

export interface ChannelCount {
  channel: string;
  count: number;
}

export interface Stats {
  total_bookings: number;
  active_sessions: number;
  confirmed_sessions: number;
  cancelled_sessions: number;
  has_schedule: boolean;
  bookings_this_week: number;
  unresolved_alerts_total: number;
  unresolved_critical_alerts: number;
  reliability_link: string;
  bookings_by_day: DailyCount[];
  cancelled_by_day: DailyCount[];
  whatsapp_bookings_by_day?: DailyCount[];
  channel_breakdown: ChannelCount[];
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

export interface BookingPageItem extends BookingItem {
  session_id: string | null;
  session_status: string | null;
  meeting_type_snapshot?: string | null;
  meeting_link_status?: string | null;
  meeting_link_url?: string | null;
  meeting_link_provider?: string | null;
}

export interface CancelledBookingItem {
  id: string;
  customer_name: string;
  customer_email: string;
  customer_phone: string | null;
  booking_date: string | null;
  booking_time: string | null;
  cancelled_at: string | null;
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
  customer_name?: string | null;
  customer_email?: string | null;
  customer_phone?: string | null;
}

export interface MessageItem {
  id: string;
  role: "user" | "agent";
  content: string;
  sender_type?: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface PersonaConfig {
  agent_name?: string;
  greeting_message?: string;
  tone?: string;
  industry_context?: string;
  custom_instructions?: string;
  business_name?: string;
  business_tagline?: string;
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
  logo_url?: string | null;
  org_name: string;
  business_name?: string | null;
  api_key: string;
  require_otp: boolean;
  require_phone_otp: boolean;
  max_booking_days_ahead: number;
  meeting_policy: {
    meeting_mode: "OFFLINE_ONLY" | "ONLINE_ONLY" | "HYBRID";
    allow_hybrid_selection: boolean;
    meeting_provider_default: "NONE" | "GOOGLE_MEET" | "MICROSOFT_TEAMS" | "ZOOM";
    available_meeting_types: ("ONLINE" | "OFFLINE")[];
    updated_at: string | null;
    updated_by: string | null;
  };
  website_url?: string | null;
  crawl_id?: string | null;
  crawl_status?: string | null;
  crawl_started_at?: string | null;
  crawl_completed_at?: string | null;
  total_pages_crawled?: number | null;
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
  open_time: string | null;    // "HH:MM" — null for closed days
  close_time: string | null;
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

// ── Knowledge wizard types ──────────────────────

export interface BusinessProfile {
  business_name: string | null;
  business_description: string;
  products_services: string | null;
  target_customers: string | null;
  key_policies: string | null;
  answer_language_tone: string | null;
  configured?: boolean;
}

export interface SourceContextInput {
  source_description: string;
  source_category?: string;
  questions_to_answer?: string[];
}

export interface CrawlContextInput extends SourceContextInput {
  pages_that_matter?: string;
  not_covered?: string;
}

export const MAX_DOCS_PER_ORG = 10;

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

export function useStats(days?: number) {
  const path = days ? `/dashboard/stats?days=${days}` : "/dashboard/stats";
  return useApi<Stats>(path, [days]);
}

export function useRecentBookings(limit = 20) {
  return useApi<BookingItem[]>(`/dashboard/recent-bookings?limit=${limit}`);
}

export function useBookings(
  page: number,
  pageSize = 10,
  filters?: { search?: string; status?: string; fromDate?: string; toDate?: string }
) {
  const rangeOk = isDateRangeValid(filters?.fromDate || "", filters?.toDate || "");
  const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (rangeOk && filters?.search) qs.append("search", filters.search);
  if (rangeOk && filters?.status) qs.append("status", filters.status);
  if (rangeOk && filters?.fromDate) qs.append("from_date", toApiDate(filters.fromDate) || filters.fromDate);
  if (rangeOk && filters?.toDate) qs.append("to_date", toApiDateTo(filters.toDate) || filters.toDate);
  return useApi<Paginated<BookingPageItem>>(
    rangeOk ? `/dashboard/bookings?${qs.toString()}` : "",
    [page, pageSize, filters?.search, filters?.status, filters?.fromDate, filters?.toDate, rangeOk]
  );
}

export function useCancelledBookings(
  page: number,
  pageSize = 10,
  filters?: { search?: string; fromDate?: string; toDate?: string }
) {
  const rangeOk = isDateRangeValid(filters?.fromDate || "", filters?.toDate || "");
  const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (rangeOk && filters?.search) qs.append("search", filters.search);
  if (rangeOk && filters?.fromDate) qs.append("from_date", toApiDate(filters.fromDate) || filters.fromDate);
  if (rangeOk && filters?.toDate) qs.append("to_date", toApiDateTo(filters.toDate) || filters.toDate);
  return useApi<Paginated<CancelledBookingItem>>(
    rangeOk ? `/dashboard/bookings/cancelled?${qs.toString()}` : "",
    [page, pageSize, filters?.search, filters?.fromDate, filters?.toDate, rangeOk]
  );
}
export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export function useConversations(
  page: number,
  pageSize = 10,
  filters?: {
    search?: string;
    status?: string[];
    channel?: string[];
    fromDate?: string;
    toDate?: string;
    sortBy?: string;
    sortOrder?: "asc" | "desc";
  }
) {
  const rangeOk = isDateRangeValid(filters?.fromDate || "", filters?.toDate || "");
  const qs = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (filters?.search) qs.append("search", filters.search);
  if (filters?.status?.length) filters.status.forEach((s) => qs.append("status", s));
  if (filters?.channel?.length) filters.channel.forEach((c) => qs.append("channel", c));
  if (rangeOk && filters?.fromDate) qs.append("from_date", toApiDate(filters.fromDate) || filters.fromDate);
  if (rangeOk && filters?.toDate) qs.append("to_date", toApiDateTo(filters.toDate) || filters.toDate);
  if (filters?.sortBy) qs.append("sort_by", filters.sortBy);
  if (filters?.sortOrder) qs.append("sort_order", filters.sortOrder);
  const url = qs.toString();
  return useApi<Paginated<ConversationItem>>(
    `/dashboard/conversations${url ? `?${url}` : ""}`,
    [
      page,
      pageSize,
      filters?.search,
      filters?.status?.join(","),
      filters?.channel?.join(","),
      filters?.fromDate,
      filters?.toDate,
      filters?.sortBy,
      filters?.sortOrder,
      rangeOk,
    ]
  );
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

export function useBusinessProfile() {
  return useApi<BusinessProfile>("/documents/business-profile");
}

export async function saveBusinessProfile(profile: BusinessProfile): Promise<BusinessProfile> {
  return api.put<BusinessProfile>("/documents/business-profile", profile);
}

export async function uploadPdfDocument(
  file: File,
  ctx?: SourceContextInput,
): Promise<DocumentUploadResponse> {
  if (!ctx) {
    return uploadFile<DocumentUploadResponse>("/documents/documents/upload", file, "file");
  }
  const formData = new FormData();
  formData.append("file", file);
  formData.append("source_context", JSON.stringify(ctx));
  const response = await fetch(`/api/v1/documents/documents/upload`, {
    method: "POST",
    headers: { "X-Requested-With": "XMLHttpRequest" },
    credentials: "include",
    body: formData,
  });
  if (!response.ok) {
    const errBody = await response.json().catch(() => ({}));
    throw new Error(errBody.detail || "Upload failed");
  }
  return response.json();
}

export async function deletePdfDocument(docId: string): Promise<void> {
  await api.del(`/documents/documents/${docId}`);
}

export async function startWebsiteCrawl(
  url: string,
  ctx?: CrawlContextInput,
): Promise<CrawlResponse> {
  return api.post<CrawlResponse>("/documents/website/crawl", { url, ...ctx });
}

export function useCrawlStatus(crawlId: string | null) {
  return useApiNullable<CrawlStatusResponse>(
    crawlId ? `/documents/website/crawl/status/${crawlId}` : null,
    [crawlId]
  );
}
