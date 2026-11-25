import React from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleProp,
  StyleSheet,
  Text,
  ViewStyle,
} from 'react-native';

import { colors, shape, spacing, type } from '../theme';

type Variant = 'filled' | 'tonal' | 'outlined' | 'text' | 'error' | 'errorOutlined';

const palette: Record<Variant, { container: string; label: string; border?: string }> = {
  filled: { container: colors.primary, label: colors.onPrimary },
  tonal: {
    container: colors.secondaryContainer,
    label: colors.onSecondaryContainer,
  },
  outlined: { container: 'transparent', label: colors.primary, border: colors.outline },
  text: { container: 'transparent', label: colors.primary },
  error: { container: colors.error, label: colors.onError },
  errorOutlined: { container: 'transparent', label: colors.error, border: colors.error },
};

interface Props {
  label: string;
  onPress: () => void;
  variant?: Variant;
  disabled?: boolean;
  loading?: boolean;
  style?: StyleProp<ViewStyle>;
}

export function Button({
  label,
  onPress,
  variant = 'filled',
  disabled,
  loading,
  style,
}: Props) {
  const tone = palette[variant];
  const isDisabled = disabled || loading;

  return (
    <Pressable
      onPress={onPress}
      disabled={isDisabled}
      accessibilityRole="button"
      accessibilityState={{ disabled: Boolean(isDisabled) }}
      android_ripple={{ color: tone.label, foreground: true }}
      style={({ pressed }) => [
        styles.base,
        {
          backgroundColor: tone.container,
          borderWidth: tone.border ? 1 : 0,
          borderColor: tone.border,
        },
        pressed && !isDisabled ? styles.pressed : null,
        isDisabled ? styles.disabled : null,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={tone.label} />
      ) : (
        <Text style={[styles.label, { color: tone.label }]} numberOfLines={1}>
          {label}
        </Text>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    minHeight: 48,
    borderRadius: shape.full,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.xl,
  },
  label: {
    ...type.labelLarge,
  },
  pressed: { opacity: 0.88 },
  disabled: { opacity: 0.38 },
});
