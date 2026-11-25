import AsyncStorage from '@react-native-async-storage/async-storage';
import { getApp, getApps, initializeApp } from 'firebase/app';
import * as FirebaseAuth from 'firebase/auth';
import { connectDatabaseEmulator, getDatabase } from 'firebase/database';
import type { Database } from 'firebase/database';

import {
  AUTH_EMULATOR_URL,
  RTDB_EMULATOR_URL,
  firebaseConfig,
  isFirebaseConfigured,
} from './config';

const app = isFirebaseConfigured
  ? getApps().length
    ? getApp()
    : initializeApp(firebaseConfig)
  : null;

/**
 * Firebase JS SDK needs AsyncStorage on React Native to persist the session.
 * `getReactNativePersistence` lives on the RN entry point; if a future SDK
 * drops it we fall back to the default persistence instead of crashing.
 */
function buildAuth(): FirebaseAuth.Auth | null {
  if (!app) return null;
  const sdk = FirebaseAuth as unknown as {
    getReactNativePersistence?: (storage: unknown) => unknown;
    initializeAuth: (app: unknown, deps: unknown) => FirebaseAuth.Auth;
    getAuth: (app?: unknown) => FirebaseAuth.Auth;
  };
  try {
    if (typeof sdk.getReactNativePersistence === 'function') {
      return sdk.initializeAuth(app, {
        persistence: sdk.getReactNativePersistence(AsyncStorage),
      });
    }
  } catch {
    // Already initialised (fast refresh) or unsupported - fall through.
  }
  try {
    return sdk.getAuth(app);
  } catch (error) {
    console.warn('[fanctl] Firebase auth could not start', error);
    return null;
  }
}

/** Null when app.json still has placeholder Firebase values. */
export const auth = buildAuth() as FirebaseAuth.Auth;

if (auth && AUTH_EMULATOR_URL) {
  try {
    FirebaseAuth.connectAuthEmulator(auth, AUTH_EMULATOR_URL, { disableWarnings: true });
  } catch {
    // Fast refresh re-runs this module; the emulator is already wired then.
  }
}

function buildDatabase(): Database | null {
  if (!app || !firebaseConfig.databaseURL) return null;
  try {
    return getDatabase(app, firebaseConfig.databaseURL);
  } catch (error) {
    console.warn('[fanctl] Realtime Database could not start', error);
    return null;
  }
}

/** Null when no databaseURL is configured; live views then stay static. */
export const database = buildDatabase();

if (database && RTDB_EMULATOR_URL) {
  const match = RTDB_EMULATOR_URL.match(/^[a-z]+:\/\/([^/:]+)(?::(\d+))?/i);
  if (match) {
    try {
      connectDatabaseEmulator(database, match[1], match[2] ? Number(match[2]) : 9000);
    } catch {
      // Fast refresh re-runs this module; the emulator is already wired then.
    }
  }
}

export const {
  onAuthStateChanged,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  signOut,
} = FirebaseAuth;
