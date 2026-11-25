import Constants from 'expo-constants';

type Extra = Record<string, string | undefined>;

const extra = (Constants.expoConfig?.extra ?? {}) as Extra;

// EXPO_PUBLIC_* vars are inlined by Babel at bundle time and win over app.json.
const envApiBase = process.env.EXPO_PUBLIC_API_BASE;
const envApiKey = process.env.EXPO_PUBLIC_FIREBASE_API_KEY;
const envAuthDomain = process.env.EXPO_PUBLIC_FIREBASE_AUTH_DOMAIN;
const envProjectId = process.env.EXPO_PUBLIC_FIREBASE_PROJECT_ID;
const envAppId = process.env.EXPO_PUBLIC_FIREBASE_APP_ID;
const envDatabaseUrl = process.env.EXPO_PUBLIC_FIREBASE_DATABASE_URL;
const envAuthEmulator = process.env.EXPO_PUBLIC_AUTH_EMULATOR;
const envRtdbEmulator = process.env.EXPO_PUBLIC_RTDB_EMULATOR;

function pick(override: string | undefined, fallback: string | undefined): string {
  const value = (override ?? '').trim() || (fallback ?? '').trim();
  return value;
}

export const API_BASE = (pick(envApiBase, extra.apiBase) || 'http://192.168.1.10:8000').replace(
  /\/+$/,
  '',
);

export const firebaseConfig = {
  apiKey: pick(envApiKey, extra.firebaseApiKey),
  authDomain: pick(envAuthDomain, extra.firebaseAuthDomain),
  projectId: pick(envProjectId, extra.firebaseProjectId),
  appId: pick(envAppId, extra.firebaseAppId),
  // Needed even against the emulator: the namespace comes from this host.
  databaseURL: pick(envDatabaseUrl, extra.firebaseDatabaseUrl),
};

export const isFirebaseConfigured =
  Boolean(firebaseConfig.apiKey && firebaseConfig.projectId) &&
  !firebaseConfig.apiKey.includes('REPLACE_ME');

/**
 * When set, the JS SDK sends sign-in calls to the local Auth emulator instead
 * of the real project. The emulator accepts any non-empty API key, but the
 * project id must match the backend's FANCTL_PROJECT_ID so that tokens minted
 * here verify on the server.
 */
export const AUTH_EMULATOR_URL = pick(envAuthEmulator, undefined).replace(/\/+$/, '');

/**
 * Local Realtime Database emulator, e.g. http://192.168.1.10:9000. The JS SDK
 * keeps the namespace from firebaseConfig.databaseURL and only swaps host/port.
 */
export const RTDB_EMULATOR_URL = pick(envRtdbEmulator, extra.rtdbEmulator).replace(/\/+$/, '');

export const LAN_TIMEOUT_MS = 1500;
export const REQUEST_TIMEOUT_MS = 12000;
