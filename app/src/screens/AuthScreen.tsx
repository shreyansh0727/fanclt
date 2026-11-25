import React, { useState } from 'react';
import {
  Image,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button } from '../components/Button';
import { TextField } from '../components/TextField';
import { auth, createUserWithEmailAndPassword, signInWithEmailAndPassword } from '../firebase';
import { colors, shape, spacing, type } from '../theme';

function friendlyError(error: unknown): string {
  const code = (error as { code?: string })?.code ?? '';
  switch (code) {
    case 'auth/invalid-email':
      return 'That email address looks wrong.';
    case 'auth/user-not-found':
    case 'auth/wrong-password':
    case 'auth/invalid-credential':
      return 'Email or password is incorrect.';
    case 'auth/email-already-in-use':
      return 'That email is already registered - try signing in.';
    case 'auth/weak-password':
      return 'Passwords need at least 6 characters.';
    case 'auth/network-request-failed':
      return 'Could not reach Firebase. Check your connection.';
    case 'auth/operation-not-allowed':
      return 'Enable Email/Password sign-in in Firebase Authentication.';
    case 'auth/too-many-requests':
      return 'Too many attempts. Wait a minute and try again.';
    default:
      return (error as Error)?.message ?? 'Something went wrong.';
  }
}

export default function AuthScreen() {
  const [mode, setMode] = useState<'signin' | 'signup'>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    if (!email.trim() || password.length < 6) {
      setError('Enter an email and a password with at least 6 characters.');
      return;
    }
    setBusy(true);
    try {
      if (mode === 'signin') {
        await signInWithEmailAndPassword(auth, email.trim(), password);
      } else {
        await createUserWithEmailAndPassword(auth, email.trim(), password);
      }
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SafeAreaView style={styles.flex} edges={['top', 'bottom']}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
          <View style={styles.brand}>
            <View style={styles.logo}>
              <Image
                source={require('../../assets/monochrome-icon.png')}
                style={styles.logoCat}
                tintColor={colors.onPrimaryContainer}
              />
            </View>
            <Text style={styles.title}>FanCtl</Text>
            <Text style={styles.subtitle}>Fans, lights and sockets - from anywhere.</Text>
          </View>

          <View style={styles.form}>
            <TextField
              label="Email"
              value={email}
              onChangeText={setEmail}
              placeholder="you@example.com"
              autoCapitalize="none"
              autoComplete="email"
              keyboardType="email-address"
              returnKeyType="next"
            />
            <TextField
              label="Password"
              value={password}
              onChangeText={setPassword}
              placeholder="At least 6 characters"
              secureTextEntry
              autoCapitalize="none"
              returnKeyType="done"
              onSubmitEditing={submit}
            />

            {error ? <Text style={styles.error}>{error}</Text> : null}

            <Button
              label={mode === 'signin' ? 'Sign in' : 'Create account'}
              onPress={submit}
              loading={busy}
              style={styles.submit}
            />
            <Button
              label={mode === 'signin' ? 'Create an account' : 'Sign in instead'}
              variant="text"
              onPress={() => {
                setMode(mode === 'signin' ? 'signup' : 'signin');
                setError(null);
              }}
            />
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.surface },
  container: {
    flexGrow: 1,
    justifyContent: 'center',
    padding: spacing.xl,
  },
  brand: { alignItems: 'center' },
  logo: {
    width: 88,
    height: 88,
    borderRadius: shape.full,
    backgroundColor: colors.primaryContainer,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.lg,
  },
  logoCat: { width: 54, height: 54, resizeMode: 'contain' },
  title: { ...type.displaySmall, color: colors.onSurface },
  subtitle: {
    ...type.bodyMedium,
    color: colors.onSurfaceVariant,
    marginTop: spacing.sm,
    textAlign: 'center',
  },
  form: { marginTop: spacing.xxl, gap: spacing.lg },
  error: { ...type.bodySmall, color: colors.error, marginTop: -spacing.xs },
  submit: { marginTop: spacing.sm },
});
