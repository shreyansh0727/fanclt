import React from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { colors, shape, spacing, type } from '../theme';
import type { Channel } from '../lib/types';

const typeLetter: Record<Channel['type'], string> = {
  fan: 'F',
  light: 'L',
  socket: 'S',
  other: 'C',
};

interface Props {
  channel: Channel;
  on: boolean;
  busy?: boolean;
  onPress: () => void;
  onLongPress: () => void;
}

/** Material 3 filled card; ON swaps to the primary-container tonal state. */
export function ChannelTile({ channel, on, busy, onPress, onLongPress }: Props) {
  return (
    <Pressable
      onPress={onPress}
      onLongPress={onLongPress}
      delayLongPress={350}
      accessibilityRole="switch"
      accessibilityState={{ checked: on, disabled: Boolean(busy) }}
      accessibilityLabel={`${channel.name}, ${on ? 'on' : 'off'}`}
      android_ripple={{ color: on ? colors.onPrimaryContainer : colors.onSurface }}
      style={({ pressed }) => [
        styles.tile,
        on ? styles.tileOn : null,
        pressed ? styles.pressed : null,
      ]}
    >
      <View style={[styles.badge, on ? styles.badgeOn : null]}>
        <Text style={[styles.badgeText, on ? styles.badgeTextOn : null]}>
          {typeLetter[channel.type] ?? 'C'}
        </Text>
      </View>
      <Text style={[styles.name, on ? styles.nameOn : null]} numberOfLines={2}>
        {channel.name}
      </Text>
      {busy ? (
        <ActivityIndicator
          size="small"
          color={on ? colors.onPrimaryContainer : colors.onSurfaceVariant}
        />
      ) : (
        <View style={styles.stateRow}>
          <View style={[styles.stateDot, on ? styles.stateDotOn : null]} />
          <Text style={[styles.state, on ? styles.stateOn : null]}>{on ? 'On' : 'Off'}</Text>
        </View>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  tile: {
    flexBasis: '48%',
    flexGrow: 1,
    minHeight: 124,
    borderRadius: shape.medium,
    backgroundColor: colors.surfaceContainerHigh,
    padding: spacing.lg,
    justifyContent: 'space-between',
  },
  tileOn: {
    backgroundColor: colors.primaryContainer,
  },
  pressed: { opacity: 0.88 },
  badge: {
    width: 32,
    height: 32,
    borderRadius: shape.full,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surfaceContainerHighest,
  },
  badgeOn: {
    backgroundColor: colors.primary,
  },
  badgeText: { ...type.labelLarge, color: colors.onSurfaceVariant },
  badgeTextOn: { color: colors.onPrimary },
  name: {
    ...type.titleMedium,
    color: colors.onSurface,
    marginTop: spacing.sm,
  },
  nameOn: { color: colors.onPrimaryContainer },
  stateRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginTop: spacing.sm,
  },
  stateDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: colors.outline,
  },
  stateDotOn: { backgroundColor: colors.success },
  state: { ...type.labelMedium, color: colors.onSurfaceVariant },
  stateOn: { color: colors.onPrimaryContainer },
});
