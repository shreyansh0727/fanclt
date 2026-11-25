import { LAN_TIMEOUT_MS } from '../config';
import type { CommandBody, Device } from './types';

// A failed LAN attempt costs LAN_TIMEOUT_MS, so remember failures for a minute
// instead of making every tap wait on a socket that cannot work.
const COOLDOWN_MS = 60_000;
const cooldown = new Map<string, number>();

function isPrivateIPv4(ip: string): boolean {
  const parts = ip.split('.').map(Number);
  if (parts.length !== 4 || parts.some((part) => Number.isNaN(part))) return false;
  const [a, b] = parts;
  if (a === 10 || a === 127) return true;
  if (a === 192 && b === 168) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  return false;
}

export function lanUsable(device: Device): boolean {
  if (!device.local_ip || !device.lan_key) return false;
  if (!isPrivateIPv4(device.local_ip)) return false;
  return Date.now() > (cooldown.get(device.id) ?? 0);
}

function markFailed(deviceId: string) {
  cooldown.set(deviceId, Date.now() + COOLDOWN_MS);
}

function markReachable(deviceId: string) {
  cooldown.delete(deviceId);
}

async function call(device: Device, path: string, init: RequestInit): Promise<any | null> {
  if (!lanUsable(device)) return null;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), LAN_TIMEOUT_MS);
  try {
    const response = await fetch(
      `http://${device.local_ip}:${device.local_port || 80}${path}`,
      {
        ...init,
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          'X-Local-Key': device.lan_key ?? '',
        },
      },
    );
    if (!response.ok) {
      markFailed(device.id);
      return null;
    }
    const data = await response.json();
    markReachable(device.id);
    return data;
  } catch {
    markFailed(device.id);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function cleanState(device: Device, data: any): boolean[] | null {
  if (!data || !Array.isArray(data.state)) return null;
  if (data.state.length !== device.channel_count) return null;
  return data.state.map(Boolean);
}

export async function localCommand(
  device: Device,
  body: CommandBody,
): Promise<boolean[] | null> {
  // The firmware frame uses `ch`; the cloud API uses `channel`.
  const payload: { action: string; ch?: number; on?: boolean } = { action: body.action };
  if (body.channel !== undefined) payload.ch = body.channel;
  if (body.on !== undefined) payload.on = body.on;

  const data = await call(device, '/api/command', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return cleanState(device, data);
}

export async function localState(device: Device): Promise<boolean[] | null> {
  const data = await call(device, '/api/state', { method: 'GET' });
  return cleanState(device, data);
}

export interface LocalInfo {
  ssid: string | null;
  ip: string | null;
  rssi: number | null;
}

export async function localInfo(device: Device): Promise<LocalInfo | null> {
  const data = await call(device, '/api/state', { method: 'GET' });
  if (!data) return null;
  return {
    ssid: typeof data.wifi_ssid === 'string' ? data.wifi_ssid : null,
    ip: typeof data.ip === 'string' ? data.ip : null,
    rssi: typeof data.wifi_rssi === 'number' ? data.wifi_rssi : null,
  };
}

// The device stores the credentials and restarts; it answers before it drops
// off the network, so a resolved `true` only means the change was accepted.
export async function localSetWifi(
  device: Device,
  ssid: string,
  pass: string,
): Promise<boolean> {
  const data = await call(device, '/api/wifi', {
    method: 'POST',
    body: JSON.stringify({ ssid, pass }),
  });
  return Boolean(data?.ok);
}
