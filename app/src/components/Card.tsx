import React from 'react';
import { StyleProp, StyleSheet, View, ViewStyle } from 'react-native';

import { colors, elevation, shape, spacing } from '../theme';

type Tone = 'filled' | 'elevated' | 'outlined' | 'primaryContainer' | 'errorContainer';

const tones: Record<Tone, ViewStyle> = {
  filled: { backgroundColor: colors.surfaceContainerHigh },
  elevated: { backgroundColor: colors.surfaceContainerLow, ...elevation.level1 },
  outlined: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.outlineVariant,
  },
  primaryContainer: { backgroundColor: colors.primaryContainer },
  errorContainer: { backgroundColor: colors.errorContainer },
};

interface Props {
  children: React.ReactNode;
  tone?: Tone;
  style?: StyleProp<ViewStyle>;
}

/** Material 3 card: filled, elevated, outlined or a tonal container. */
export function Card({ children, tone = 'filled', style }: Props) {
  return <View style={[styles.base, tones[tone], style]}>{children}</View>;
}

const styles = StyleSheet.create({
  base: {
    borderRadius: shape.medium,
    padding: spacing.lg,
  },
});
