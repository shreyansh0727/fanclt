import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors, elevation, shape, spacing, type } from '../theme';
import {
  checkForAppUpdate,
  downloadAndInstall,
  installedVersion,
  type AppRelease,
} from '../lib/updates';

const RECHECK_MS = 30 * 60 * 1000;

/**
 * Floating "update available" card. Checks the release manifest on launch and
 * whenever the app comes back to the foreground; silent when offline.
 */
export function UpdateGate() {
  const insets = useSafeAreaInsets();
  const [release, setRelease] = useState<AppRelease | null>(null);
  const [hidden, setHidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const lastCheck = useRef(0);

  const check = useCallback(async () => {
    if (Date.now() - lastCheck.current < RECHECK_MS) return;
    lastCheck.current = Date.now();
    const found = await checkForAppUpdate().catch(() => null);
    if (found) setRelease(found);
  }, []);

  useEffect(() => {
    void check();
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') void check();
    });
    return () => subscription.remove();
  }, [check]);

  if (!release || hidden) return null;

  async function install() {
    if (!release) return;
    setError(null);
    setBusy(true);
    setProgress(0);
    try {
      await downloadAndInstall(release, setProgress);
      setProgress(null);
    } catch (err) {
      setProgress(null);
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <View
      style={[styles.wrap, { paddingBottom: insets.bottom + spacing.lg }]}
      pointerEvents="box-none"
    >
      <View style={[styles.card, elevation.level2]}>
        <Text style={styles.title}>FanCtl {release.version} is available</Text>
        <Text style={styles.bodyText}>
          You have {installedVersion()}. {release.notes ? `${release.notes} ` : ''}
          The download installs with a tap in the system dialog.
        </Text>

        {progress !== null ? (
          <View style={styles.progressTrack}>
            <View
              style={[styles.progressFill, { width: `${Math.round(progress * 100)}%` }]}
            />
          </View>
        ) : null}
        {error ? <Text style={styles.error}>{error}</Text> : null}

        <View style={styles.actions}>
          <Pressable
            onPress={() => setHidden(true)}
            android_ripple={{ color: colors.inverseOnSurface }}
            style={styles.textButton}
          >
            <Text style={styles.actionLabel}>Later</Text>
          </Pressable>
          <Pressable
            onPress={() => {
              void install();
            }}
            disabled={busy}
            android_ripple={{ color: colors.inverseOnSurface }}
            style={styles.textButton}
          >
            <Text style={styles.actionLabel}>{busy ? 'Downloading…' : 'Update now'}</Text>
          </Pressable>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: spacing.lg,
  },
  card: {
    backgroundColor: colors.inverseSurface,
    borderRadius: shape.medium,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.md,
    paddingBottom: spacing.sm,
  },
  title: { ...type.titleSmall, color: colors.inverseOnSurface },
  bodyText: {
    ...type.bodySmall,
    color: colors.inverseOnSurface,
    opacity: 0.9,
    marginTop: spacing.xs,
  },
  progressTrack: {
    height: 4,
    borderRadius: 2,
    backgroundColor: 'rgba(46,48,54,0.2)',
    overflow: 'hidden',
    marginTop: spacing.md,
  },
  progressFill: {
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.inversePrimary,
  },
  error: { ...type.bodySmall, color: colors.error, marginTop: spacing.sm },
  actions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: spacing.xs,
    marginTop: spacing.sm,
  },
  textButton: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: shape.full,
  },
  actionLabel: {
    ...type.labelLarge,
    color: colors.inversePrimary,
  },
});
