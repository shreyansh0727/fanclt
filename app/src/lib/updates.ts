import Constants from 'expo-constants';
import * as FileSystem from 'expo-file-system/legacy';
import * as IntentLauncher from 'expo-intent-launcher';
import { Platform } from 'react-native';

import { API_BASE, REQUEST_TIMEOUT_MS } from '../config';

export interface FirmwareRelease {
  version: string;
  url: string;
  sha256?: string;
}

export interface AppRelease {
  version: string;
  version_code?: number;
  url: string;
  notes?: string;
}

interface Manifest {
  firmware: FirmwareRelease | null;
  app: AppRelease | null;
}

/** The version baked into this APK at build time. */
export function installedVersion(): string {
  return Constants.expoConfig?.version ?? '0.0.0';
}

/** Numeric major.minor.patch compare; a missing part counts as zero. */
export function isNewer(remote: string, local: string): boolean {
  const parse = (value: string) => {
    const parts = value.split('.').map((part) => parseInt(part, 10));
    return [parts[0] || 0, parts[1] || 0, parts[2] || 0];
  };
  const [rMajor, rMinor, rPatch] = parse(remote);
  const [lMajor, lMinor, lPatch] = parse(local);
  if (rMajor !== lMajor) return rMajor > lMajor;
  if (rMinor !== lMinor) return rMinor > lMinor;
  return rPatch > lPatch;
}

async function fetchManifest(): Promise<Manifest | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${API_BASE}/api/v1/updates`, {
      signal: controller.signal,
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) return null;
    return (await response.json()) as Manifest;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Returns the published app release when it is newer than this build. Network
 * failures stay silent: an update check must never get in the way of the app.
 */
export async function checkForAppUpdate(): Promise<AppRelease | null> {
  if (Platform.OS !== 'android') return null;
  const manifest = await fetchManifest().catch(() => null);
  const release = manifest?.app;
  if (!release?.version || !release.url) return null;
  return isNewer(release.version, installedVersion()) ? release : null;
}

/**
 * Downloads the APK into the cache and hands it to the system installer. The
 * install still needs a tap in the system dialog, and the first one also asks
 * to allow FanCtl to install unknown apps.
 */
export async function downloadAndInstall(
  release: AppRelease,
  onProgress: (fraction: number) => void,
): Promise<void> {
  if (Platform.OS !== 'android') {
    throw new Error('In-app updates are Android only.');
  }
  const target = `${FileSystem.cacheDirectory}fanctl-${release.version}.apk`;
  await FileSystem.deleteAsync(target, { idempotent: true }).catch(() => undefined);

  const download = FileSystem.createDownloadResumable(release.url, target, {}, (progress) => {
    if (progress.totalBytesExpectedToWrite > 0) {
      onProgress(progress.totalBytesWritten / progress.totalBytesExpectedToWrite);
    }
  });
  const result = await download.downloadAsync();
  if (!result?.uri) throw new Error('The download did not finish.');

  const contentUri = await FileSystem.getContentUriAsync(result.uri);
  await IntentLauncher.startActivityAsync('android.intent.action.VIEW', {
    data: contentUri,
    flags: 1, // FLAG_GRANT_READ_URI_PERMISSION
    type: 'application/vnd.android.package-archive',
  });
}
