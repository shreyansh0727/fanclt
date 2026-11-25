import React from 'react';
import { Pressable, StyleProp, StyleSheet, Text, View, ViewStyle } from 'react-native';

import { colors, shape, spacing, type } from '../theme';

type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'error';

const tones: Record<Tone, { container: string; label: string; dot: string }> = {
  neutral: {
    container: colors.surfaceContainerHighest,
    label: colors.onSurfaceVariant,
    dot: colors.onSurfaceVariant,
  },
  info: {
    container: colors.secondaryContainer,
    label: colors.onSecondaryContainer,
    dot: colors.primary,
  },
  success: {
    container: colors.successContainer,
    label: colors.onSuccessContainer,
    dot: colors.success,
  },
  warning: {
    container: colors.warningContainer,
    label: colors.onWarningContainer,
    dot: colors.warning,
  },
  error: {
    container: colors.errorContainer,
    label: colors.onErrorContainer,
    dot: colors.error,
  },
};

interface Props {
  label: string;
  /** Filter chip: pass `selected` and an `onPress`. */
  selected?: boolean;
  onPress?: () => void;
  /** Status chip: tinted container, no interaction. */
  tone?: Tone;
  dot?: boolean;
  style?: StyleProp<ViewStyle>;
}

export function Chip({ label, selected, onPress, tone, dot, style }: Props) {
  if (tone) {
    const palette = tones[tone];
    return (
      <View style={[styles.status, { backgroundColor: palette.container }, style]}>
        {dot ? <View style={[styles.dot, { backgroundColor: palette.dot }]} /> : null}
        <Text style={[styles.statusLabel, { color: palette.label }]} numberOfLines={1}>
          {label}
        </Text>
      </View>
    );
  }

  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityState={{ selected: Boolean(selected) }}
      android_ripple={{ color: colors.onSurface }}
      style={({ pressed }) => [
        styles.filter,
        selected ? styles.filterSelected : styles.filterIdle,
        pressed ? styles.pressed : null,
        style,
      ]}
    >
      <Text
        style={[styles.filterLabel, { color: selected ? colors.onSecondaryContainer : colors.onSurface }]}
        numberOfLines={1}
      >
        {label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  status: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs + 2,
    height: 28,
    paddingHorizontal: spacing.md,
    borderRadius: shape.small,
  },
  statusLabel: {
    ...type.labelMedium,
  },
  dot: { width: 6, height: 6, borderRadius: 3 },
  filter: {
    height: 32,
    paddingHorizontal: spacing.md,
    borderRadius: shape.small,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
  },
  filterIdle: {
    backgroundColor: 'transparent',
    borderColor: colors.outlineVariant,
  },
  filterSelected: {
    backgroundColor: colors.secondaryContainer,
    borderColor: colors.secondaryContainer,
  },
  filterLabel: {
    ...type.labelLarge,
  },
  pressed: { opacity: 0.85 },
});
