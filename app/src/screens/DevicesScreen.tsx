import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Card } from '../components/Card';
import { Chip } from '../components/Chip';
import { useApp } from '../lib/store';
import type { Device } from '../lib/types';
import type { RootStackParamList } from '../nav';
import { colors, elevation, shape, spacing, type } from '../theme';

type Props = NativeStackScreenProps<RootStackParamList, 'Devices'>;

function LiveChip({ status }: { status: 'connecting' | 'online' | 'offline' }) {
  // This is the app's link to the database, not the controller's presence -
  // the Online/Offline chip on each card is the device.
  if (status === 'online') return <Chip label="Synced" tone="success" dot />;
  if (status === 'connecting') return <Chip label="Connecting" tone="warning" dot />;
  return <Chip label="Offline" tone="error" dot />;
}

function DeviceCard({ device, onPress }: { device: Device; onPress: () => void }) {
  const onCount = device.state.filter(Boolean).length;
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={`${device.name}, ${device.online ? 'online' : 'offline'}`}
      style={({ pressed }) => (pressed ? styles.pressed : undefined)}
    >
      <Card tone="elevated" style={styles.card}>
        <View style={styles.cardTop}>
          <View style={styles.cardTitleWrap}>
            <Text style={styles.cardTitle} numberOfLines={1}>
              {device.name}
            </Text>
            <Text style={styles.cardSub} numberOfLines={1}>
              {device.room ? device.room : `${device.channel_count} channels`}
            </Text>
          </View>
          <Chip
            label={device.online ? 'Online' : 'Offline'}
            tone={device.online ? 'success' : 'neutral'}
            dot
          />
        </View>

        <View style={styles.dots}>
          {device.state.map((on, index) => (
            <View
              key={`${device.id}-${index}`}
              style={[styles.stateDot, on ? styles.stateDotOn : null]}
            />
          ))}
        </View>

        <Text style={styles.cardMeta}>
          {onCount} of {device.channel_count} on
          {device.local_ip ? `  ·  LAN ${device.local_ip}` : ''}
        </Text>
      </Card>
    </Pressable>
  );
}

export default function DevicesScreen({ navigation }: Props) {
  const { devices, refresh, live, signOut, user } = useApp();
  const insets = useSafeAreaInsets();
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    navigation.setOptions({
      headerRight: () => (
        <Pressable
          hitSlop={12}
          onPress={() =>
            Alert.alert('Sign out', 'Sign out of FanCtl?', [
              { text: 'Cancel', style: 'cancel' },
              {
                text: 'Sign out',
                style: 'destructive',
                onPress: () => {
                  signOut().catch(() => undefined);
                },
              },
            ])
          }
        >
          <Text style={styles.headerAction}>Sign out</Text>
        </Pressable>
      ),
    });
  }, [navigation, signOut]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await refresh();
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setRefreshing(false);
    }
  }, [refresh]);

  return (
    <View style={styles.root}>
      <FlatList
        data={devices}
        keyExtractor={(device) => device.id}
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            colors={[colors.primary]}
            tintColor={colors.primary}
            progressBackgroundColor={colors.surfaceContainerHigh}
          />
        }
        ListHeaderComponent={
          <View style={styles.header}>
            <View style={styles.headerText}>
              <Text style={styles.headerLabel}>Signed in as</Text>
              <Text style={styles.email} numberOfLines={1}>
                {user?.email ?? 'FanCtl user'}
              </Text>
            </View>
            <LiveChip status={live} />
          </View>
        }
        ListEmptyComponent={
          <Card style={styles.empty}>
            <Text style={styles.emptyTitle}>No controllers yet</Text>
            <Text style={styles.emptyText}>
              Power up the ESP32 controller, let it join your WiFi, then pair it using the button
              below.
            </Text>
          </Card>
        }
        ListFooterComponent={
          <View style={styles.footer}>
            <Pressable
              onPress={() => navigation.navigate('Assistants')}
              accessibilityRole="button"
              accessibilityLabel="Voice assistants"
            >
              <Card tone="outlined" style={styles.assistantsCard}>
                <View style={styles.assistantsRow}>
                  <View style={styles.assistantsText}>
                    <Text style={styles.assistantsTitle}>Voice assistants</Text>
                    <Text style={styles.assistantsSub}>
                      Google Home and Bixby · manage linked accounts
                    </Text>
                  </View>
                  <Text style={styles.headerAction}>Manage</Text>
                </View>
              </Card>
            </Pressable>
            {error ? (
              <Pressable onPress={() => setError(null)}>
                <Text style={styles.error}>{error}</Text>
              </Pressable>
            ) : null}
          </View>
        }
        renderItem={({ item }) => (
          <DeviceCard
            device={item}
            onPress={() => navigation.navigate('Device', { deviceId: item.id, name: item.name })}
          />
        )}
      />

      <Pressable
        onPress={() => navigation.navigate('AddDevice')}
        accessibilityRole="button"
        accessibilityLabel="Pair a controller"
        android_ripple={{ color: colors.onPrimaryContainer }}
        style={({ pressed }) => [
          styles.fab,
          elevation.level2,
          { bottom: insets.bottom + spacing.lg },
          pressed ? styles.fabPressed : null,
        ]}
      >
        <Text style={styles.fabLabel}>Pair a controller</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  list: {
    padding: spacing.lg,
    paddingBottom: 104,
    gap: spacing.md,
    flexGrow: 1,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.md,
    marginBottom: spacing.xs,
  },
  headerText: { flexShrink: 1 },
  headerLabel: { ...type.labelMedium, color: colors.onSurfaceVariant },
  email: { ...type.bodyMedium, color: colors.onSurface, marginTop: 2 },
  headerAction: { ...type.labelLarge, color: colors.primary },
  pressed: { opacity: 0.9 },
  card: { gap: spacing.sm },
  cardTop: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.md },
  cardTitleWrap: { flex: 1 },
  cardTitle: { ...type.titleMedium, color: colors.onSurface },
  cardSub: { ...type.bodySmall, color: colors.onSurfaceVariant, marginTop: 2 },
  dots: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.sm },
  stateDot: {
    flex: 1,
    height: 4,
    borderRadius: 2,
    backgroundColor: colors.surfaceContainerHighest,
  },
  stateDotOn: { backgroundColor: colors.primary },
  cardMeta: { ...type.bodySmall, color: colors.onSurfaceVariant },
  footer: { gap: spacing.sm },
  assistantsCard: { paddingVertical: spacing.md },
  assistantsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
  },
  assistantsText: { flex: 1 },
  assistantsTitle: { ...type.titleSmall, color: colors.onSurface },
  assistantsSub: { ...type.bodySmall, color: colors.onSurfaceVariant, marginTop: 2 },
  empty: { marginTop: spacing.sm, gap: spacing.sm },
  emptyTitle: { ...type.titleMedium, color: colors.onSurface },
  emptyText: { ...type.bodyMedium, color: colors.onSurfaceVariant },
  error: {
    ...type.bodySmall,
    color: colors.error,
    textAlign: 'center',
    marginTop: spacing.sm,
  },
  fab: {
    position: 'absolute',
    right: spacing.lg,
    minHeight: 56,
    paddingHorizontal: spacing.xl,
    borderRadius: shape.large,
    backgroundColor: colors.primaryContainer,
    alignItems: 'center',
    justifyContent: 'center',
  },
  fabPressed: { opacity: 0.9 },
  fabLabel: { ...type.labelLarge, color: colors.onPrimaryContainer },
});
