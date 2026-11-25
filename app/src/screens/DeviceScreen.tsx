import React, { useEffect, useState } from 'react';
import {
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { ChannelTile } from '../components/ChannelTile';
import { Chip } from '../components/Chip';
import { TextField } from '../components/TextField';
import { api } from '../lib/api';
import { lanUsable, localInfo, localSetWifi } from '../lib/local';
import { useApp } from '../lib/store';
import type { Channel, ChannelType, PetAction } from '../lib/types';
import type { RootStackParamList } from '../nav';
import { colors, elevation, shape, spacing, type } from '../theme';

type Props = NativeStackScreenProps<RootStackParamList, 'Device'>;

const TYPE_OPTIONS: { key: ChannelType; label: string }[] = [
  { key: 'fan', label: 'Fan' },
  { key: 'light', label: 'Light' },
  { key: 'socket', label: 'Socket' },
  { key: 'other', label: 'Other' },
];

const PET_OPTIONS: { key: PetAction; label: string }[] = [
  { key: 'pet', label: 'Pet' },
  { key: 'play', label: 'Play' },
  { key: 'feed', label: 'Feed' },
  { key: 'sleep', label: 'Sleep' },
  { key: 'wake', label: 'Wake' },
];

export default function DeviceScreen({ route, navigation }: Props) {
  const {
    devicesById,
    setChannel,
    setAllChannels,
    pet,
    stopAlarm,
    updateDevice,
    removeDevice,
    transport,
  } = useApp();
  const device = devicesById[route.params.deviceId];

  const [busyChannel, setBusyChannel] = useState<number | null>(null);
  const [allBusy, setAllBusy] = useState(false);
  const [petBusy, setPetBusy] = useState<PetAction | null>(null);
  const [alarmBusy, setAlarmBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Channel | null>(null);
  const [namesOpen, setNamesOpen] = useState(false);
  const [draftName, setDraftName] = useState('');
  const [draftType, setDraftType] = useState<ChannelType>('other');
  const [saving, setSaving] = useState(false);
  const [wifiOpen, setWifiOpen] = useState(false);
  const [wifiSsid, setWifiSsid] = useState('');
  const [wifiPass, setWifiPass] = useState('');
  const [wifiCurrent, setWifiCurrent] = useState<string | null>(null);
  const [wifiBusy, setWifiBusy] = useState(false);

  useEffect(() => {
    if (device) navigation.setOptions({ title: device.name });
  }, [device?.name, navigation]);

  if (!device) {
    return (
      <View style={styles.missing}>
        <Text style={styles.missingText}>This controller is no longer paired.</Text>
      </View>
    );
  }

  const onCount = device.state.filter(Boolean).length;

  async function toggle(index: number) {
    if (!device) return;
    setError(null);
    setBusyChannel(index);
    try {
      const result = await setChannel(device, index, !device.state[index]);
      if (result && !result.acked) {
        setError(
          result.online
            ? 'The controller did not confirm that change.'
            : 'Controller is offline - nothing was switched.',
        );
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusyChannel(null);
    }
  }

  async function master(on: boolean) {
    if (!device) return;
    setError(null);
    setAllBusy(true);
    try {
      const result = await setAllChannels(device, on);
      if (result && !result.acked) {
        setError(
          result.online
            ? 'The controller did not confirm that change.'
            : 'Controller is offline - nothing was switched.',
        );
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setAllBusy(false);
    }
  }

  async function petCommand(action: PetAction) {
    if (!device) return;
    setError(null);
    setPetBusy(action);
    try {
      const result = await pet(device, action);
      if (!result.acked) {
        setError(
          result.online
            ? 'The controller did not confirm that.'
            : 'Controller is offline - nothing was played.',
        );
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPetBusy(null);
    }
  }

  async function silenceAlarm() {
    if (!device) return;
    setError(null);
    setAlarmBusy(true);
    try {
      await stopAlarm(device);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setAlarmBusy(false);
    }
  }

  function openEditor(channel: Channel) {
    setEditing(channel);
    setDraftName(channel.name);
    setDraftType(channel.type);
  }

  async function saveEditor() {
    if (!device || !editing) return;
    const name = draftName.trim();
    if (!name) {
      setError('Give the channel a name.');
      return;
    }
    setSaving(true);
    try {
      await updateDevice(device.id, {
        channels: device.channels.map((channel) =>
          channel.index === editing.index ? { ...channel, name, type: draftType } : channel,
        ),
      });
      setEditing(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function identify() {
    setError(null);
    try {
      await api.identify(device!.id);
      Alert.alert('Identify', 'The controller LED blinks for 5 seconds.');
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function openWifiEditor() {
    if (!device) return;
    setError(null);
    setWifiSsid('');
    setWifiPass('');
    setWifiCurrent(null);
    setWifiOpen(true);
    const info = await localInfo(device);
    setWifiCurrent(info?.ssid || null);
  }

  async function saveWifi() {
    if (!device) return;
    const ssid = wifiSsid.trim();
    if (!ssid) {
      setError('Enter the network name.');
      return;
    }
    setWifiBusy(true);
    try {
      const accepted = await localSetWifi(device, ssid, wifiPass);
      if (!accepted) {
        setError(
          'The controller did not accept the change. Your phone has to be on the same Wi-Fi as the controller.',
        );
        return;
      }
      setWifiOpen(false);
      Alert.alert(
        'Wi-Fi updated',
        `The controller is restarting and will join "${ssid}". If it cannot connect it comes back in setup mode, as the FanCtl-Setup hotspot.`,
      );
    } finally {
      setWifiBusy(false);
    }
  }

  function confirmRemove() {
    if (!device) return;
    Alert.alert(
      'Remove controller',
      `Remove ${device.name}? You will need the pairing code to add it back.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Remove',
          style: 'destructive',
          onPress: async () => {
            try {
              await removeDevice(device!.id);
              navigation.popToTop();
            } catch (err) {
              setError((err as Error).message);
            }
          },
        },
      ],
    );
  }

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={styles.container}>
        <Card style={styles.statusCard}>
          <View style={styles.statusRow}>
            <Chip
              label={device.online ? 'Online' : 'Offline'}
              tone={device.online ? 'success' : 'neutral'}
              dot
            />
            {transport ? (
              <Chip label={transport === 'local' ? 'LAN' : 'Cloud'} tone="info" />
            ) : null}
            {!lanUsable(device) && device.online ? <Chip label="No LAN" tone="warning" /> : null}
          </View>
          <Text style={styles.statusMeta}>
            {onCount} of {device.channel_count} on
            {device.local_ip ? `   ·   ${device.local_ip}` : ''}
            {device.telemetry?.rssi ? `   ·   ${device.telemetry.rssi} dBm` : ''}
          </Text>
          <Text style={styles.statusMeta}>
            {device.firmware ? `firmware ${device.firmware}` : 'firmware unknown'}
            {device.last_seen ? `   ·   seen ${new Date(device.last_seen).toLocaleTimeString()}` : ''}
          </Text>
        </Card>

        {device.alarm ? (
          <Card tone="errorContainer" style={styles.alarmCard}>
            <View style={styles.alarmTextWrap}>
              <Text style={styles.alarmTitle}>Alarm ringing</Text>
              <Text style={styles.alarmHint}>
                It keeps ringing until you stop it here or on the controller.
              </Text>
            </View>
            <Button
              label="Stop alarm"
              variant="error"
              onPress={() => {
                void silenceAlarm();
              }}
              loading={alarmBusy}
            />
          </Card>
        ) : null}

        {error ? (
          <Pressable onPress={() => setError(null)}>
            <Card tone="errorContainer" style={styles.errorCard}>
              <Text style={styles.errorText}>{error}</Text>
            </Card>
          </Pressable>
        ) : null}

        <View style={styles.masterRow}>
          <Button
            label="All on"
            variant="tonal"
            onPress={() => master(true)}
            loading={allBusy}
            style={styles.masterButton}
          />
          <Button
            label="All off"
            variant="tonal"
            onPress={() => master(false)}
            loading={allBusy}
            style={styles.masterButton}
          />
        </View>

        <View style={styles.sectionRow}>
          <Text style={styles.sectionTitle}>Channels</Text>
          <Pressable hitSlop={10} onPress={() => setNamesOpen(true)}>
            <Text style={styles.sectionAction}>Rename</Text>
          </Pressable>
        </View>
        <Text style={styles.sectionHint}>Tap to switch, hold to rename.</Text>
        <View style={styles.grid}>
          {device.channels.map((channel) => (
            <ChannelTile
              key={channel.index}
              channel={channel}
              on={Boolean(device.state[channel.index])}
              busy={busyChannel === channel.index}
              onPress={() => {
                void toggle(channel.index);
              }}
              onLongPress={() => openEditor(channel)}
            />
          ))}
        </View>

        <Text style={styles.sectionTitle}>Pet</Text>
        <Text style={styles.sectionHint}>
          Plays a reaction on the controller's screen and speaker.
        </Text>
        <View style={styles.petRow}>
          {PET_OPTIONS.map((option) => (
            <Button
              key={option.key}
              label={option.label}
              variant="outlined"
              onPress={() => {
                void petCommand(option.key);
              }}
              loading={petBusy === option.key}
              style={styles.petButton}
            />
          ))}
        </View>

        <View style={styles.actions}>
          <Button
            label="Schedules"
            variant="outlined"
            onPress={() =>
              navigation.navigate('Schedules', {
                deviceId: device.id,
                deviceName: device.name,
              })
            }
          />
          <Button
            label="Blink LED to identify"
            variant="outlined"
            onPress={() => {
              void identify();
            }}
            disabled={!device.online}
          />
          <Button
            label="Change Wi-Fi network"
            variant="outlined"
            onPress={() => {
              void openWifiEditor();
            }}
            disabled={!lanUsable(device)}
          />
          <Button
            label="Remove controller"
            variant="errorOutlined"
            onPress={confirmRemove}
          />
        </View>
      </ScrollView>

      {editing ? (
        <View style={styles.modalBackdrop}>
          <View style={[styles.dialog, elevation.level3]}>
            <Text style={styles.dialogTitle}>Channel {editing.index + 1}</Text>
            <TextField
              label="Name"
              value={draftName}
              onChangeText={setDraftName}
              placeholder="Ceiling fan"
              maxLength={32}
              autoFocus
              style={styles.dialogField}
            />
            <Text style={styles.dialogLabel}>Type</Text>
            <View style={styles.chips}>
              {TYPE_OPTIONS.map((option) => (
                <Chip
                  key={option.key}
                  label={option.label}
                  selected={draftType === option.key}
                  onPress={() => setDraftType(option.key)}
                />
              ))}
            </View>
            <View style={styles.dialogActions}>
              <Button label="Cancel" variant="text" onPress={() => setEditing(null)} />
              <Button label="Save" onPress={saveEditor} loading={saving} />
            </View>
          </View>
        </View>
      ) : null}

      {namesOpen ? (
        <View style={styles.modalBackdrop}>
          <View style={[styles.dialog, elevation.level3]}>
            <Text style={styles.dialogTitle}>Channel names</Text>
            <Text style={styles.dialogHint}>
              Pick a channel to rename it. The controller picks the new name up live and shows it
              on its display and local web page.
            </Text>
            <ScrollView style={styles.nameList}>
              {device.channels.map((channel) => (
                <Pressable
                  key={channel.index}
                  android_ripple={{ color: colors.onSurface }}
                  onPress={() => {
                    setNamesOpen(false);
                    openEditor(channel);
                  }}
                  style={styles.nameRow}
                >
                  <Text style={styles.nameIndex}>{channel.index + 1}</Text>
                  <Text style={styles.nameText} numberOfLines={1}>
                    {channel.name}
                  </Text>
                  <Text style={styles.nameType}>{channel.type}</Text>
                </Pressable>
              ))}
            </ScrollView>
            <View style={styles.dialogActions}>
              <Button label="Done" variant="text" onPress={() => setNamesOpen(false)} />
            </View>
          </View>
        </View>
      ) : null}

      {wifiOpen ? (
        <View style={styles.modalBackdrop}>
          <View style={[styles.dialog, elevation.level3]}>
            <Text style={styles.dialogTitle}>Wi-Fi network</Text>
            <Text style={styles.dialogHint}>
              {wifiCurrent ? `Currently on ${wifiCurrent}.` : 'Current network unknown.'}
            </Text>
            <TextField
              label="Network name (SSID)"
              value={wifiSsid}
              onChangeText={setWifiSsid}
              placeholder="Home Wi-Fi"
              autoCapitalize="none"
              autoCorrect={false}
              autoFocus
              style={styles.dialogField}
            />
            <TextField
              label="Password"
              value={wifiPass}
              onChangeText={setWifiPass}
              placeholder="Leave blank for an open network"
              secureTextEntry
              autoCapitalize="none"
              autoCorrect={false}
              style={styles.dialogField}
            />
            <Text style={styles.dialogHint}>
              2.4 GHz only. The controller restarts and joins this network; if it cannot, it
              returns to the FanCtl-Setup hotspot.
            </Text>
            {error ? <Text style={styles.dialogError}>{error}</Text> : null}
            <View style={styles.dialogActions}>
              <Button
                label="Cancel"
                variant="text"
                onPress={() => {
                  setError(null);
                  setWifiOpen(false);
                }}
              />
              <Button label="Save" onPress={saveWifi} loading={wifiBusy} />
            </View>
          </View>
        </View>
      ) : null}
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.surface },
  container: { padding: spacing.lg, gap: spacing.md, paddingBottom: spacing.xxl * 2 },
  missing: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.xl,
    backgroundColor: colors.surface,
  },
  missingText: { ...type.bodyLarge, color: colors.onSurfaceVariant },
  statusCard: { gap: spacing.xs },
  statusRow: { flexDirection: 'row', gap: spacing.sm, flexWrap: 'wrap' },
  statusMeta: { ...type.bodySmall, color: colors.onSurfaceVariant },
  alarmCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
  },
  alarmTextWrap: { flex: 1, gap: 2 },
  alarmTitle: { ...type.titleMedium, color: colors.onErrorContainer },
  alarmHint: { ...type.bodySmall, color: colors.onErrorContainer, opacity: 0.9 },
  errorCard: { paddingVertical: spacing.md },
  errorText: { ...type.bodySmall, color: colors.onErrorContainer },
  masterRow: { flexDirection: 'row', gap: spacing.md },
  masterButton: { flex: 1 },
  sectionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: spacing.sm,
  },
  sectionTitle: { ...type.titleMedium, color: colors.onSurface },
  sectionHint: { ...type.bodySmall, color: colors.onSurfaceVariant },
  sectionAction: { ...type.labelLarge, color: colors.primary },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md },
  petRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  petButton: { flexBasis: '30%', flexGrow: 1 },
  actions: { marginTop: spacing.sm, gap: spacing.sm },
  modalBackdrop: {
    position: 'absolute',
    top: 0,
    right: 0,
    bottom: 0,
    left: 0,
    backgroundColor: 'rgba(0,0,0,0.6)',
    justifyContent: 'center',
    padding: spacing.xl,
  },
  dialog: {
    backgroundColor: colors.surfaceContainerHigh,
    borderRadius: shape.extraLarge,
    padding: spacing.xl,
    alignSelf: 'center',
    width: '100%',
    maxWidth: 480,
  },
  dialogTitle: { ...type.headlineSmall, color: colors.onSurface },
  dialogHint: {
    ...type.bodyMedium,
    color: colors.onSurfaceVariant,
    marginTop: spacing.sm,
  },
  dialogField: { marginTop: spacing.lg },
  dialogLabel: {
    ...type.labelMedium,
    color: colors.onSurfaceVariant,
    marginTop: spacing.lg,
    marginBottom: spacing.sm,
  },
  dialogError: { ...type.bodySmall, color: colors.error, marginTop: spacing.sm },
  dialogActions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    alignItems: 'center',
    gap: spacing.sm,
    marginTop: spacing.xl,
  },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  nameList: { marginTop: spacing.lg, maxHeight: 320 },
  nameRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.sm,
    borderRadius: shape.small,
  },
  nameIndex: { ...type.labelMedium, color: colors.onSurfaceVariant, width: 18 },
  nameText: { ...type.bodyLarge, color: colors.onSurface, flex: 1 },
  nameType: { ...type.bodySmall, color: colors.onSurfaceVariant },
});
