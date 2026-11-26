import { API_BASE, REQUEST_TIMEOUT_MS } from '../config';
import { auth } from '../firebase';
import type {
  AssistantLink,
  CommandBody,
  CommandResult,
  Device,
  DevicePatch,
  Schedule,
  ScheduleInput,
} from './types';

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const user = auth.currentUser;
  if (!user) {
    throw new ApiError(401, 'not_signed_in', 'Please sign in first.');
  }

  const token = await user.getIdToken();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch(`${API_BASE}/api/v1${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
        ...((init.headers as Record<string, string> | undefined) ?? {}),
      },
    });

    if (response.status === 204) {
      return undefined as T;
    }

    const text = await response.text();
    let body: any = null;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = null;
      }
    }

    if (!response.ok) {
      const error = body?.error;
      throw new ApiError(
        response.status,
        error?.code ?? 'http_error',
        error?.message ?? `Request failed (${response.status})`,
      );
    }
    return body as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error)?.name === 'AbortError') {
      throw new ApiError(0, 'timeout', 'The server did not answer in time.');
    }
    throw new ApiError(0, 'network_error', 'Cannot reach the server. Check your connection.');
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  listDevices: () => request<Device[]>('/devices'),
  getDevice: (deviceId: string) => request<Device>(`/devices/${deviceId}`),
  claimDevice: (code: string, name?: string) =>
    request<Device>('/devices/claim', {
      method: 'POST',
      body: JSON.stringify(name ? { code, name } : { code }),
    }),
  patchDevice: (deviceId: string, patch: DevicePatch) =>
    request<Device>(`/devices/${deviceId}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteDevice: (deviceId: string) =>
    request<void>(`/devices/${deviceId}`, { method: 'DELETE' }),
  sendCommand: (deviceId: string, body: CommandBody) =>
    request<CommandResult>(`/devices/${deviceId}/commands`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  identify: (deviceId: string) =>
    request<{ sent: boolean }>(`/devices/${deviceId}/identify`, { method: 'POST' }),
  listSchedules: (deviceId: string) =>
    request<Schedule[]>(`/schedules?device_id=${encodeURIComponent(deviceId)}`),
  createSchedule: (input: ScheduleInput) =>
    request<Schedule>('/schedules', { method: 'POST', body: JSON.stringify(input) }),
  updateSchedule: (scheduleId: string, patch: Partial<ScheduleInput> & { enabled?: boolean }) =>
    request<Schedule>(`/schedules/${scheduleId}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  deleteSchedule: (scheduleId: string) =>
    request<void>(`/schedules/${scheduleId}`, { method: 'DELETE' }),
  listAssistantLinks: () => request<AssistantLink[]>('/assistants/links'),
  revokeAssistantLink: (linkId: string) =>
    request<void>(`/assistants/links/${encodeURIComponent(linkId)}`, { method: 'DELETE' }),
};
