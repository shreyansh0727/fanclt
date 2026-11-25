import type { TextStyle } from 'react-native';

/**
 * Material 3 dark scheme, tonal palette seeded from the FanCtl blue. Screens
 * paint plain `surface`; depth comes from the surface-container tones and the
 * elevation levels below, never from gradients or blur.
 */
export const colors = {
  primary: '#A9C7FF',
  onPrimary: '#003258',
  primaryContainer: '#00497E',
  onPrimaryContainer: '#D6E3FF',

  secondary: '#BDC7DC',
  onSecondary: '#273141',
  secondaryContainer: '#3D4758',
  onSecondaryContainer: '#D9E3F8',

  tertiary: '#DCBCE7',
  onTertiary: '#3E2845',
  tertiaryContainer: '#553C5B',
  onTertiaryContainer: '#F8D8FA',

  error: '#FFB4AB',
  onError: '#690005',
  errorContainer: '#93000A',
  onErrorContainer: '#FFDAD6',

  surface: '#111318',
  surfaceDim: '#111318',
  surfaceBright: '#37393E',
  surfaceContainerLowest: '#0C0E12',
  surfaceContainerLow: '#191C20',
  surfaceContainer: '#1D2024',
  surfaceContainerHigh: '#282A2E',
  surfaceContainerHighest: '#33353A',
  onSurface: '#E1E2E9',
  onSurfaceVariant: '#C3C6CF',
  surfaceVariant: '#43474E',
  outline: '#8D9199',
  outlineVariant: '#43474E',
  inverseSurface: '#E1E2E9',
  inverseOnSurface: '#2E3036',
  inversePrimary: '#0061A4',
  scrim: '#000000',

  // Extended status colours; the baseline scheme has no success/warning roles.
  success: '#79D999',
  successContainer: '#1E4B2E',
  onSuccessContainer: '#A3F5BD',
  warning: '#F1C66D',
  warningContainer: '#4C3A00',
  onWarningContainer: '#FFE08C',
} as const;

/** Material 3 type scale. Roboto is the platform default on Android. */
export const type = {
  displaySmall: { fontSize: 36, lineHeight: 44, fontWeight: '400', letterSpacing: 0 },
  headlineSmall: { fontSize: 24, lineHeight: 32, fontWeight: '400', letterSpacing: 0 },
  titleLarge: { fontSize: 22, lineHeight: 28, fontWeight: '400', letterSpacing: 0 },
  titleMedium: { fontSize: 16, lineHeight: 24, fontWeight: '500', letterSpacing: 0.15 },
  titleSmall: { fontSize: 14, lineHeight: 20, fontWeight: '500', letterSpacing: 0.1 },
  bodyLarge: { fontSize: 16, lineHeight: 24, fontWeight: '400', letterSpacing: 0.5 },
  bodyMedium: { fontSize: 14, lineHeight: 20, fontWeight: '400', letterSpacing: 0.25 },
  bodySmall: { fontSize: 12, lineHeight: 16, fontWeight: '400', letterSpacing: 0.4 },
  labelLarge: { fontSize: 14, lineHeight: 20, fontWeight: '500', letterSpacing: 0.1 },
  labelMedium: { fontSize: 12, lineHeight: 16, fontWeight: '500', letterSpacing: 0.5 },
  labelSmall: { fontSize: 11, lineHeight: 16, fontWeight: '500', letterSpacing: 0.5 },
} as const satisfies Record<string, TextStyle>;

/** Shape scale, in dp. */
export const shape = {
  extraSmall: 4,
  small: 8,
  medium: 12,
  large: 16,
  extraLarge: 28,
  full: 999,
} as const;

/** 4 dp grid. */
export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const;

/** Android elevation levels, matching the M3 component heights. */
export const elevation = {
  level1: {
    shadowColor: '#000000',
    shadowOpacity: 0.18,
    shadowRadius: 3,
    shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
  level2: {
    shadowColor: '#000000',
    shadowOpacity: 0.22,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 3,
  },
  level3: {
    shadowColor: '#000000',
    shadowOpacity: 0.28,
    shadowRadius: 10,
    shadowOffset: { width: 0, height: 6 },
    elevation: 6,
  },
} as const;
