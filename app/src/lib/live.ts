import { onValue, ref } from 'firebase/database';

import { database } from '../firebase';
import type { Channel } from './types';

export type LiveStatus = 'connecting' | 'online' | 'offline';

export interface DeviceMeta {
  name?: string;
  room?: string;
  channels?: Channel[];
}

interface NodeHandlers {
  onState?: (state: boolean[]) => void;
  onMeta?: (meta: DeviceMeta) => void;
  onOnline?: (online: boolean) => void;
  onAlarm?: (active: boolean) => void;
  onRemoved?: () => void;
}

interface RtdbNode {
  unclaim?: unknown;
  state?: unknown;
  name?: unknown;
  room?: unknown;
  channels?: unknown;
  info?: { ts?: unknown; alarm?: unknown };
}

// Same tolerance the backend uses: a controller rewrites info.ts every 10 s.
const ONLINE_WINDOW_MS = 40_000;
const CLOCK_SKEW_MS = 30_000;

function isChannelList(value: unknown): value is Channel[] {
  return (
    Array.isArray(value) &&
    value.every(
      (item) =>
        item &&
        typeof item === 'object' &&
        typeof (item as Channel).index === 'number' &&
        typeof (item as Channel).name === 'string',
    )
  );
}

/**
 * Mirrors one controller's Realtime Database node into callbacks. The owner
 * can read /devices/<id>; the node disappears (becomes {unclaim:true}) when
 * the owner deletes the controller.
 */
export function watchDevice(deviceId: string, handlers: NodeHandlers): () => void {
  if (!database) return () => {};

  let lastTs: number | null = null;
  let reportedOnline: boolean | null = null;
  let reportedAlarm: boolean | null = null;

  const evaluate = () => {
    const age = lastTs === null ? Number.POSITIVE_INFINITY : Date.now() - lastTs;
    const online = age > -CLOCK_SKEW_MS && age < ONLINE_WINDOW_MS;
    if (reportedOnline !== online) {
      reportedOnline = online;
      handlers.onOnline?.(online);
    }
  };

  // Nothing is pushed while the controller is offline, so re-derive the flag
  // on a timer as well.
  const timer = setInterval(evaluate, 5_000);

  const unsubscribe = onValue(
    ref(database, `devices/${deviceId}`),
    (snapshot) => {
      const node = snapshot.val() as RtdbNode | null;
      if (!node || node.unclaim) {
        handlers.onRemoved?.();
        return;
      }

      if (Array.isArray(node.state) && node.state.every((value) => typeof value === 'boolean')) {
        handlers.onState?.(node.state.map(Boolean));
      }

      const meta: DeviceMeta = {};
      if (typeof node.name === 'string') meta.name = node.name;
      if (typeof node.room === 'string') meta.room = node.room;
      if (isChannelList(node.channels)) meta.channels = node.channels;
      if (Object.keys(meta).length > 0) handlers.onMeta?.(meta);

      lastTs = typeof node.info?.ts === 'number' ? (node.info.ts as number) : null;
      evaluate();

      const alarm = node.info?.alarm === true;
      if (alarm !== reportedAlarm) {
        reportedAlarm = alarm;
        handlers.onAlarm?.(alarm);
      }
    },
    (error) => {
      console.warn(`[fanctl] live node for ${deviceId} failed`, error);
      lastTs = null;
      evaluate();
    },
  );

  return () => {
    clearInterval(timer);
    unsubscribe();
  };
}

/** `.info/connected` tells us whether the realtime connection is up. */
export function watchConnection(onStatus: (status: LiveStatus) => void): () => void {
  if (!database) {
    onStatus('offline');
    return () => {};
  }
  return onValue(
    ref(database, '.info/connected'),
    (snapshot) => onStatus(snapshot.val() === true ? 'online' : 'offline'),
    () => onStatus('offline'),
  );
}
