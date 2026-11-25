import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { onAuthStateChanged, signOut as firebaseSignOut } from 'firebase/auth';
import type { User } from 'firebase/auth';

import { auth } from '../firebase';
import { api } from './api';
import { watchConnection, watchDevice, type LiveStatus } from './live';
import { localCommand } from './local';
import type { CommandBody, CommandResult, Device, DevicePatch, PetAction } from './types';

type Transport = 'local' | 'cloud' | null;

interface AppContextValue {
  user: User | null;
  authReady: boolean;
  devices: Device[];
  devicesById: Record<string, Device>;
  live: LiveStatus;
  transport: Transport;
  refresh: () => Promise<void>;
  claim: (code: string, name?: string) => Promise<Device>;
  updateDevice: (deviceId: string, patch: DevicePatch) => Promise<void>;
  removeDevice: (deviceId: string) => Promise<void>;
  setChannel: (device: Device, index: number, on: boolean) => Promise<CommandResult | null>;
  setAllChannels: (device: Device, on: boolean) => Promise<CommandResult | null>;
  pet: (device: Device, action: PetAction) => Promise<CommandResult>;
  stopAlarm: (device: Device) => Promise<void>;
  signOut: () => Promise<void>;
}

const AppContext = createContext<AppContextValue | null>(null);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [devices, setDevices] = useState<Device[]>([]);
  const [live, setLive] = useState<LiveStatus>('connecting');
  const [transport, setTransport] = useState<Transport>(null);

  useEffect(() => {
    // firebase.ts returns null if SDK init failed; show the sign-in screen
    // instead of crashing on a null listener.
    if (!auth) {
      setAuthReady(true);
      return;
    }
    return onAuthStateChanged(auth, (nextUser) => {
      setUser(nextUser);
      setAuthReady(true);
      if (!nextUser) setDevices([]);
    });
  }, []);

  const devicesById = useMemo(
    () => Object.fromEntries(devices.map((device) => [device.id, device])),
    [devices],
  );

  const refresh = useCallback(async () => {
    const list = await api.listDevices();
    setDevices(list);
  }, []);

  // REST gives the authoritative list (owner, names, LAN key, schedule data);
  // the database stream keeps the live fields fresh from then on.
  useEffect(() => {
    if (!user) return;
    refresh().catch(() => {
      // A failed refresh is not fatal: the next screen focus retries.
    });
  }, [user, refresh]);

  useEffect(() => {
    if (!user) {
      setLive('connecting');
      return;
    }
    return watchConnection(setLive);
  }, [user]);

  const deviceKey = useMemo(
    () =>
      devices
        .map((device) => device.id)
        .sort()
        .join(','),
    [devices],
  );

  useEffect(() => {
    if (!user || !deviceKey) return;
    const ids = deviceKey.split(',');

    const unsubscribes = ids.map((id) =>
      watchDevice(id, {
        onState: (state) =>
          setDevices((prev) => prev.map((device) => (device.id === id ? { ...device, state } : device))),
        onOnline: (online) =>
          setDevices((prev) =>
            prev.map((device) => (device.id === id ? { ...device, online } : device)),
          ),
        onAlarm: (alarm) =>
          setDevices((prev) =>
            prev.map((device) => (device.id === id ? { ...device, alarm } : device)),
          ),
        onMeta: (meta) =>
          setDevices((prev) =>
            prev.map((device) =>
              device.id === id
                ? {
                    ...device,
                    name: meta.name ?? device.name,
                    room: meta.room ?? device.room,
                    channels: meta.channels ?? device.channels,
                  }
                : device,
            ),
          ),
        onRemoved: () => {
          setDevices((prev) => prev.filter((device) => device.id !== id));
          refresh().catch(() => {});
        },
      }),
    );

    return () => unsubscribes.forEach((unsubscribe) => unsubscribe());
  }, [user, deviceKey, refresh]);

  // Commands are also presence probes: a command that never reaches the
  // controller proves it is offline, so take the backend's verdict instead of
  // waiting for the heartbeat window to expire.
  const applyState = useCallback((deviceId: string, state: boolean[], online?: boolean) => {
    setDevices((prev) =>
      prev.map((device) =>
        device.id === deviceId
          ? { ...device, state, online: online ?? device.online }
          : device,
      ),
    );
  }, []);

  const pushCommand = useCallback(
    async (
      device: Device,
      body: CommandBody,
      optimistic: boolean[],
    ): Promise<CommandResult | null> => {
      applyState(device.id, optimistic);

      // LAN first: lowest latency and it keeps working when the internet is down.
      const lanState = await localCommand(device, body);
      if (lanState) {
        setTransport('local');
        applyState(device.id, lanState, true);
        return null;
      }

      const result = await api.sendCommand(device.id, body);
      setTransport('cloud');
      applyState(device.id, result.state, result.online);
      return result;
    },
    [applyState],
  );

  const setChannel = useCallback(
    async (device: Device, index: number, on: boolean) => {
      const optimistic = device.state.map((value, i) => (i === index ? on : value));
      try {
        return await pushCommand(device, { action: 'set', channel: index, on }, optimistic);
      } catch (error) {
        applyState(device.id, device.state);
        throw error;
      }
    },
    [applyState, pushCommand],
  );

  const setAllChannels = useCallback(
    async (device: Device, on: boolean) => {
      const optimistic = device.state.map(() => on);
      try {
        return await pushCommand(device, { action: 'all', on }, optimistic);
      } catch (error) {
        applyState(device.id, device.state);
        throw error;
      }
    },
    [applyState, pushCommand],
  );

  // Pet reactions go straight to the cloud path: the firmware answers with an
  // ack that doubles as a presence check, and there is no relay state to race.
  const pet = useCallback(
    async (device: Device, action: PetAction) => {
      const result = await api.sendCommand(device.id, { action });
      setTransport('cloud');
      applyState(device.id, result.state, result.online);
      return result;
    },
    [applyState],
  );

  // Stopping goes LAN first: the chime is local, so it has to work even when
  // the internet link is down. The device mirrors the flag back over RTDB.
  const stopAlarm = useCallback(
    async (device: Device) => {
      setDevices((prev) =>
        prev.map((item) => (item.id === device.id ? { ...item, alarm: false } : item)),
      );
      try {
        await pushCommand(device, { action: 'alarm_stop' }, device.state);
      } catch (error) {
        setDevices((prev) =>
          prev.map((item) => (item.id === device.id ? { ...item, alarm: true } : item)),
        );
        throw error;
      }
    },
    [pushCommand],
  );

  const claim = useCallback(async (code: string, name?: string) => {
    const device = await api.claimDevice(code, name);
    setDevices((prev) => [...prev.filter((item) => item.id !== device.id), device]);
    return device;
  }, []);

  const updateDevice = useCallback(async (deviceId: string, patch: DevicePatch) => {
    const updated = await api.patchDevice(deviceId, patch);
    setDevices((prev) => prev.map((device) => (device.id === deviceId ? updated : device)));
  }, []);

  const removeDevice = useCallback(async (deviceId: string) => {
    await api.deleteDevice(deviceId);
    setDevices((prev) => prev.filter((device) => device.id !== deviceId));
  }, []);

  const signOut = useCallback(async () => {
    await firebaseSignOut(auth);
    setDevices([]);
  }, []);

  const value: AppContextValue = {
    user,
    authReady,
    devices,
    devicesById,
    live,
    transport,
    refresh,
    claim,
    updateDevice,
    removeDevice,
    setChannel,
    setAllChannels,
    pet,
    stopAlarm,
    signOut,
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) throw new Error('useApp must be used inside <AppProvider>');
  return value;
}
