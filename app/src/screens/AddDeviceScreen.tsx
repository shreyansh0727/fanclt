import React, { useState } from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { TextField } from '../components/TextField';
import { ApiError } from '../lib/api';
import { useApp } from '../lib/store';
import type { RootStackParamList } from '../nav';
import { colors, spacing, type } from '../theme';

type Props = NativeStackScreenProps<RootStackParamList, 'AddDevice'>;

/** Crockford pairing codes are printed as XXXX-XXXX. */
function formatCode(raw: string): string {
  const clean = raw
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, '')
    .slice(0, 8);
  return clean.length > 4 ? `${clean.slice(0, 4)}-${clean.slice(4)}` : clean;
}

function friendlyClaimError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case 'invalid_code':
        return 'That pairing code was not recognised. Listen again: the controller repeats the code every 15 seconds while it is online and unpaired.';
      case 'already_claimed':
        return 'This controller is already paired to an account. Remove it from that account first.';
      case 'code_expired':
        return 'The pairing window has closed. Reboot the controller to open it again.';
      case 'device_offline':
      case 'device_disconnected':
        return 'The controller is not connected right now. Keep it powered and on WiFi, then try again.';
      case 'timeout':
        return 'The server took too long to answer. Try again.';
      default:
        return error.message;
    }
  }
  return (error as Error)?.message ?? 'Pairing failed.';
}

export default function AddDeviceScreen({ navigation }: Props) {
  const { claim } = useApp();
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    const normalized = code.replace(/[^A-Z0-9]/gi, '');
    if (normalized.length < 4) {
      setError('Enter the pairing code from the controller (8 characters, like ABCD-1234).');
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const device = await claim(code, name.trim() || undefined);
      navigation.replace('Device', { deviceId: device.id, name: device.name });
    } catch (err) {
      setError(friendlyClaimError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
        <Text style={styles.intro}>
          Power up the controller, join its setup hotspot and save your WiFi. Once it is online it
          reads its pairing code aloud - type the 8 characters you hear.
        </Text>

        <View style={styles.form}>
          <TextField
            label="Pairing code"
            value={code}
            onChangeText={(text) => setCode(formatCode(text))}
            placeholder="ABCD-1234"
            autoCapitalize="characters"
            autoCorrect={false}
            autoComplete="off"
            maxLength={9}
            inputStyle={styles.codeInput}
            onSubmitEditing={submit}
          />
          <TextField
            label="Name (optional)"
            value={name}
            onChangeText={setName}
            placeholder="Living room"
            maxLength={48}
            onSubmitEditing={submit}
          />
          {error ? <Text style={styles.error}>{error}</Text> : null}
          <Button label="Pair controller" onPress={submit} loading={busy} />
        </View>

        <Card tone="outlined" style={styles.hintCard}>
          <Text style={styles.hintTitle}>Nothing heard?</Text>
          <Text style={styles.hintText}>
            While the controller is online and unpaired it repeats the code every 15 seconds; the
            serial console at 115200 baud prints the same code. Hold BOOT for 3 seconds to reopen
            the setup hotspot, or 8 seconds to factory reset.
          </Text>
        </Card>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.surface },
  container: { flexGrow: 1, padding: spacing.xl, gap: spacing.xl },
  intro: { ...type.bodyMedium, color: colors.onSurfaceVariant },
  form: { gap: spacing.lg },
  codeInput: { fontSize: 24, letterSpacing: 4, fontWeight: '600' },
  error: { ...type.bodySmall, color: colors.error, marginTop: -spacing.sm },
  hintCard: { gap: spacing.sm },
  hintTitle: { ...type.titleSmall, color: colors.onSurface },
  hintText: { ...type.bodySmall, color: colors.onSurfaceVariant },
});
