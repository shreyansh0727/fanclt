import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  View,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Chip } from '../components/Chip';
import { TextField } from '../components/TextField';
import { api } from '../lib/api';
import { useApp } from '../lib/store';
import type { Schedule, ScheduleActionType } from '../lib/types';
import type { RootStackParamList } from '../nav';
import { colors, spacing, type } from '../theme';

type Props = NativeStackScreenProps<RootStackParamList, 'Schedules'>;

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

function localTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

function daysLabel(days: number[]): string {
  const sorted = [...days].sort((a, b) => a - b);
  if (sorted.length === 7) return 'Every day';
  if (sorted.join(',') === '0,1,2,3,4') return 'Weekdays';
  if (sorted.join(',') === '5,6') return 'Weekends';
  return sorted.map((day) => DAYS[day] ?? '?').join(', ');
}

function nextRunLabel(iso: string | null): string {
  if (!iso) return 'No next run';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return 'No next run';
  return `next ${date.toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })}`;
}

function byTime(a: Schedule, b: Schedule): number {
  return a.time.localeCompare(b.time) || a.name.localeCompare(b.name);
}

export default function SchedulesScreen({ route, navigation }: Props) {
  const { deviceId, deviceName } = route.params;
  const { devicesById } = useApp();
  const device = devicesById[deviceId];

  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const [name, setName] = useState('');
  const [time, setTime] = useState('07:00');
  const [days, setDays] = useState<number[]>([0, 1, 2, 3, 4, 5, 6]);
  const [timezone, setTimezone] = useState(localTimezone);
  const [actionType, setActionType] = useState<ScheduleActionType>('set_all');
  const [channel, setChannel] = useState(0);
  const [turnOn, setTurnOn] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    navigation.setOptions({ title: `${deviceName} schedules` });
  }, [navigation, deviceName]);

  const load = useCallback(async () => {
    try {
      const list = await api.listSchedules(deviceId);
      setSchedules([...list].sort(byTime));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [deviceId]);

  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load]),
  );

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }, [load]);

  function channelLabel(index?: number | null): string {
    if (index === null || index === undefined) return 'Channel';
    const found = device?.channels.find((item) => item.index === index);
    return found?.name ?? `Channel ${index + 1}`;
  }

  function describe(schedule: Schedule): string {
    if (schedule.action.type === 'alarm') return 'Alarm · chime + animation';
    if (schedule.action.type === 'reminder') return 'Reminder · chime + animation';
    const target =
      schedule.action.type === 'set_all' ? 'All channels' : channelLabel(schedule.action.channel);
    return `${target} ${schedule.action.on ? 'on' : 'off'}`;
  }

  function resetForm() {
    setName('');
    setTime('07:00');
    setDays([0, 1, 2, 3, 4, 5, 6]);
    setTimezone(localTimezone());
    setActionType('set_all');
    setChannel(0);
    setTurnOn(true);
  }

  function toggleDay(day: number) {
    setDays((prev) =>
      prev.includes(day) ? prev.filter((item) => item !== day) : [...prev, day].sort((a, b) => a - b),
    );
  }

  async function create() {
    const trimmedTime = time.trim();
    if (!TIME_RE.test(trimmedTime)) {
      setError('Time must be 24-hour HH:MM, for example 07:30 or 19:00.');
      return;
    }
    if (days.length === 0) {
      setError('Pick at least one day.');
      return;
    }
    const tz = timezone.trim();
    if (!tz) {
      setError('Enter a timezone such as Asia/Kolkata.');
      return;
    }

    const isRelay = actionType === 'set_all' || actionType === 'set_channel';
    const target = actionType === 'set_all' ? 'All channels' : channelLabel(channel);
    const resolvedName =
      name.trim() ||
      (isRelay
        ? `${target} ${turnOn ? 'on' : 'off'}`
        : actionType === 'alarm'
          ? 'Alarm'
          : 'Reminder');

    setError(null);
    setSaving(true);
    try {
      const created = await api.createSchedule({
        device_id: deviceId,
        name: resolvedName.slice(0, 48),
        time: trimmedTime,
        days: [...days].sort((a, b) => a - b),
        timezone: tz,
        action:
          actionType === 'set_all'
            ? { type: 'set_all', on: turnOn }
            : actionType === 'set_channel'
              ? { type: 'set_channel', on: turnOn, channel }
              : { type: actionType },
        enabled: true,
      });
      setSchedules((prev) => [...prev, created].sort(byTime));
      setShowForm(false);
      resetForm();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function toggleEnabled(schedule: Schedule) {
    setBusyId(schedule.id);
    try {
      const updated = await api.updateSchedule(schedule.id, { enabled: !schedule.enabled });
      setSchedules((prev) =>
        prev.map((item) => (item.id === updated.id ? updated : item)).sort(byTime),
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  function confirmDelete(schedule: Schedule) {
    Alert.alert('Delete schedule', `Delete "${schedule.name}"?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          setBusyId(schedule.id);
          try {
            await api.deleteSchedule(schedule.id);
            setSchedules((prev) => prev.filter((item) => item.id !== schedule.id));
          } catch (err) {
            setError((err as Error).message);
          } finally {
            setBusyId(null);
          }
        },
      },
    ]);
  }

  const channelOptions = device?.channels ?? [];

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView
        contentContainerStyle={styles.container}
        keyboardShouldPersistTaps="handled"
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            colors={[colors.primary]}
            tintColor={colors.primary}
            progressBackgroundColor={colors.surfaceContainerHigh}
          />
        }
      >
        {error ? (
          <Pressable onPress={() => setError(null)}>
            <Card tone="errorContainer" style={styles.errorCard}>
              <Text style={styles.errorText}>{error}</Text>
            </Card>
          </Pressable>
        ) : null}

        {showForm ? (
          <Card tone="outlined">
            <Text style={styles.formTitle}>New schedule</Text>

            <TextField
              label="Time (24-hour)"
              value={time}
              onChangeText={setTime}
              placeholder="07:30"
              keyboardType="numbers-and-punctuation"
              maxLength={5}
              style={styles.field}
            />

            <Text style={styles.groupLabel}>Days</Text>
            <View style={styles.chips}>
              {DAYS.map((label, index) => (
                <Chip
                  key={label}
                  label={label}
                  selected={days.includes(index)}
                  onPress={() => toggleDay(index)}
                />
              ))}
            </View>

            <Text style={styles.groupLabel}>Action</Text>
            <View style={styles.chips}>
              <Chip
                label="All channels"
                selected={actionType === 'set_all'}
                onPress={() => setActionType('set_all')}
              />
              <Chip
                label="One channel"
                selected={actionType === 'set_channel'}
                onPress={() => setActionType('set_channel')}
              />
              <Chip
                label="Alarm"
                selected={actionType === 'alarm'}
                onPress={() => setActionType('alarm')}
              />
              <Chip
                label="Reminder"
                selected={actionType === 'reminder'}
                onPress={() => setActionType('reminder')}
              />
            </View>

            {actionType === 'set_channel' ? (
              <View style={styles.chips}>
                {channelOptions.map((option) => (
                  <Chip
                    key={option.index}
                    label={option.name}
                    selected={channel === option.index}
                    onPress={() => setChannel(option.index)}
                  />
                ))}
              </View>
            ) : null}

            {actionType === 'set_all' || actionType === 'set_channel' ? (
              <View style={styles.chips}>
                <Chip label="Turn on" selected={turnOn} onPress={() => setTurnOn(true)} />
                <Chip label="Turn off" selected={!turnOn} onPress={() => setTurnOn(false)} />
              </View>
            ) : null}

            <Text style={styles.hint}>
              Alarm rings a siren and a startled cat scene until you stop it from the alarm banner
              on the controller's screen; reminder plays a short soft chime with the cat. Neither
              switches relays.
            </Text>

            <TextField
              label="Timezone"
              value={timezone}
              onChangeText={setTimezone}
              placeholder="Asia/Kolkata"
              autoCapitalize="none"
              autoCorrect={false}
              helper="IANA timezone name. Defaults to this phone's timezone."
              style={styles.field}
            />

            <TextField
              label="Name (optional)"
              value={name}
              onChangeText={setName}
              placeholder="Morning fan"
              maxLength={48}
              style={styles.field}
            />

            <View style={styles.formActions}>
              <Button
                label="Cancel"
                variant="text"
                onPress={() => {
                  setShowForm(false);
                  setError(null);
                }}
              />
              <Button label="Save" onPress={create} loading={saving} />
            </View>
          </Card>
        ) : (
          <Button
            label="Add schedule"
            variant="tonal"
            onPress={() => {
              setError(null);
              setShowForm(true);
            }}
          />
        )}

        {loading ? null : schedules.length === 0 ? (
          <Card style={styles.empty}>
            <Text style={styles.emptyTitle}>No schedules yet</Text>
            <Text style={styles.emptyText}>
              Schedules run on the server, so they fire even when this app is closed.
            </Text>
          </Card>
        ) : (
          schedules.map((schedule) => (
            <Card key={schedule.id} tone="elevated" style={styles.row}>
              <View style={styles.rowTop}>
                <View style={styles.rowTitleWrap}>
                  <Text style={styles.rowTime}>{schedule.time}</Text>
                  <Text style={styles.rowName} numberOfLines={1}>
                    {schedule.name}
                  </Text>
                </View>
                <Switch
                  value={schedule.enabled}
                  onValueChange={() => {
                    if (busyId !== schedule.id) void toggleEnabled(schedule);
                  }}
                  disabled={busyId === schedule.id}
                  trackColor={{
                    false: colors.surfaceContainerHighest,
                    true: colors.primary,
                  }}
                  thumbColor={schedule.enabled ? colors.onPrimary : colors.outline}
                  ios_backgroundColor={colors.surfaceContainerHighest}
                />
              </View>
              <Text style={styles.rowMeta}>{daysLabel(schedule.days)}</Text>
              <Text style={styles.rowMeta}>
                {describe(schedule)}   ·   {schedule.timezone}
              </Text>
              <View style={styles.rowBottom}>
                <Chip
                  label={schedule.enabled ? nextRunLabel(schedule.next_run_at) : 'Paused'}
                  tone={schedule.enabled ? 'info' : 'neutral'}
                  dot
                />
                <Pressable
                  hitSlop={10}
                  onPress={() => confirmDelete(schedule)}
                  disabled={busyId === schedule.id}
                >
                  <Text style={styles.delete}>Delete</Text>
                </Pressable>
              </View>
            </Card>
          ))
        )}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.surface },
  container: {
    padding: spacing.lg,
    gap: spacing.md,
    paddingBottom: spacing.xxl * 2,
  },
  errorCard: { paddingVertical: spacing.md },
  errorText: { ...type.bodySmall, color: colors.onErrorContainer },
  formTitle: { ...type.titleMedium, color: colors.onSurface },
  field: { marginTop: spacing.lg },
  groupLabel: {
    ...type.labelMedium,
    color: colors.onSurfaceVariant,
    marginTop: spacing.lg,
  },
  hint: {
    ...type.bodySmall,
    color: colors.onSurfaceVariant,
    marginTop: spacing.sm,
  },
  chips: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
    marginTop: spacing.sm,
  },
  formActions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    alignItems: 'center',
    gap: spacing.sm,
    marginTop: spacing.xl,
  },
  empty: { gap: spacing.sm },
  emptyTitle: { ...type.titleMedium, color: colors.onSurface },
  emptyText: { ...type.bodyMedium, color: colors.onSurfaceVariant },
  row: { gap: spacing.xs },
  rowTop: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  rowTitleWrap: { flex: 1 },
  rowTime: { ...type.titleLarge, color: colors.onSurface, fontVariant: ['tabular-nums'] },
  rowName: { ...type.bodyMedium, color: colors.onSurfaceVariant, marginTop: 2 },
  rowMeta: { ...type.bodySmall, color: colors.onSurfaceVariant },
  rowBottom: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: spacing.sm,
  },
  delete: { ...type.labelLarge, color: colors.error },
});
