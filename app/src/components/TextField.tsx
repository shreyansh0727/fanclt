import React, { useState } from 'react';
import {
  StyleProp,
  StyleSheet,
  Text,
  TextInput,
  TextInputProps,
  View,
  ViewStyle,
} from 'react-native';

import { colors, shape, spacing, type } from '../theme';

interface Props extends Omit<TextInputProps, 'style'> {
  label: string;
  value: string;
  onChangeText: (text: string) => void;
  helper?: string;
  error?: string;
  style?: StyleProp<ViewStyle>;
  inputStyle?: TextInputProps['style'];
}

/**
 * Material 3 filled text field. The label floats to the top edge once the
 * field is focused or holds text, and the underline carries the focus/error
 * accent.
 */
export function TextField({
  label,
  value,
  onChangeText,
  helper,
  error,
  style,
  inputStyle,
  onFocus,
  onBlur,
  placeholder,
  ...rest
}: Props) {
  const [focused, setFocused] = useState(false);
  const floating = focused || value.length > 0;

  return (
    <View style={style}>
      <View
        style={[
          styles.field,
          {
            borderBottomColor: error
              ? colors.error
              : focused
                ? colors.primary
                : colors.outline,
            borderBottomWidth: focused || error ? 2 : 1,
          },
        ]}
      >
        {floating ? (
          <Text
            style={[
              styles.floatingLabel,
              {
                color: error
                  ? colors.error
                  : focused
                    ? colors.primary
                    : colors.onSurfaceVariant,
              },
            ]}
            numberOfLines={1}
          >
            {label}
          </Text>
        ) : null}
        <TextInput
          {...rest}
          value={value}
          onChangeText={onChangeText}
          placeholder={floating ? placeholder : label}
          placeholderTextColor={colors.onSurfaceVariant}
          selectionColor={colors.primary}
          cursorColor={colors.primary}
          onFocus={(event) => {
            setFocused(true);
            onFocus?.(event);
          }}
          onBlur={(event) => {
            setFocused(false);
            onBlur?.(event);
          }}
          style={[styles.input, floating ? styles.inputFloating : null, inputStyle]}
        />
      </View>
      {error ? (
        <Text style={[styles.support, { color: colors.error }]}>{error}</Text>
      ) : helper ? (
        <Text style={styles.support}>{helper}</Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  field: {
    backgroundColor: colors.surfaceContainerHighest,
    borderTopLeftRadius: shape.extraSmall,
    borderTopRightRadius: shape.extraSmall,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    paddingBottom: spacing.sm,
    minHeight: 56,
    justifyContent: 'center',
  },
  floatingLabel: {
    ...type.bodySmall,
    paddingBottom: 2,
  },
  input: {
    ...type.bodyLarge,
    color: colors.onSurface,
    padding: 0,
    minHeight: 24,
  },
  inputFloating: {
    paddingTop: 0,
  },
  support: {
    ...type.bodySmall,
    color: colors.onSurfaceVariant,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.xs,
  },
});
