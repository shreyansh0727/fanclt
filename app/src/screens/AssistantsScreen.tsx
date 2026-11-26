import React, { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';

import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Chip } from '../components/Chip';
import { api } from '../lib/api';
import type { AssistantLink } from '../lib/types';
import { colors, spacing, type } from '../theme';

function providerLabel(link: AssistantLink): string {
  if (link.provider === 'google') return 'Google Home';
  if (link.provider === 'smartthings') return 'Bixby · SmartThings';
  return link.client_name;
}

function dateLabel(iso: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

function LinkCard({
  link,
  busy,
  onUnlink,
}: {
  link: AssistantLink;
  busy: boolean;
  onUnlink: () => void;
}) {
  const linked = dateLabel(link.created_at);
  const used = dateLabel(link.last_used_at);
  return (
    <Card tone="elevated" style={styles.card}>
      <View style={styles.cardTop}>
        <View style={styles.cardTitleWrap}>
          <Text style={styles.cardTitle}>{providerLabel(link)}</Text>
          <Text style={styles.cardSub} numberOfLines={1}>
            {link.client_name}
          </Text>
        </View>
        <Chip label="Linked" tone="success" dot />
      </View>

      <Text style={styles.meta}>
        {linked ? `Linked ${linked}` : 'Linked'}
        {used ? `  ·  last used ${used}` : '  ·  not used yet'}
      </Text>

      <Button
        label="Unlink"
        variant="errorOutlined"
        loading={busy}
        onPress={onUnlink}
        style={styles.unlink}
      />
    </Card>
  );
}

export default function AssistantsScreen() {
  const [links, setLinks] = useState<AssistantLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLinks(await api.listAssistantLinks());
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

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

  const unlink = useCallback(
    (link: AssistantLink) => {
      Alert.alert(
        `Unlink ${providerLabel(link)}?`,
        'The assistant loses access to your controllers until you link the account again.',
        [
          { text: 'Cancel', style: 'cancel' },
          {
            text: 'Unlink',
            style: 'destructive',
            onPress: async () => {
              setBusyId(link.id);
              try {
                await api.revokeAssistantLink(link.id);
                setLinks((prev) => prev.filter((item) => item.id !== link.id));
                setError(null);
              } catch (err) {
                setError((err as Error).message);
              } finally {
                setBusyId(null);
              }
            },
          },
        ],
      );
    },
    [],
  );

  return (
    <ScrollView
      contentContainerStyle={styles.content}
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
      <Card tone="outlined" style={styles.intro}>
        <Text style={styles.introTitle}>Control FanCtl with your voice</Text>
        <Text style={styles.introText}>
          Google Home and Bixby see every relay channel as a switch named after the channel. Link
          the account from the assistant's own app — after that it appears here.
        </Text>
        <View style={styles.steps}>
          <View style={styles.step}>
            <Text style={styles.stepLabel}>Google Home</Text>
            <Text style={styles.stepText}>Google Home app → Works with Google → FanCtl</Text>
          </View>
          <View style={styles.step}>
            <Text style={styles.stepLabel}>Bixby</Text>
            <Text style={styles.stepText}>SmartThings app → Partner devices → FanCtl</Text>
          </View>
        </View>
      </Card>

      <Text style={styles.sectionLabel}>Linked assistants</Text>

      {loading ? (
        <ActivityIndicator color={colors.primary} style={styles.loading} />
      ) : links.length === 0 ? (
        <Card style={styles.empty}>
          <Text style={styles.emptyTitle}>No assistants linked yet</Text>
          <Text style={styles.emptyText}>
            Add FanCtl in the Google Home or SmartThings app to get started. Nothing is shared until
            you sign in on the linking page.
          </Text>
        </Card>
      ) : (
        links.map((link) => (
          <LinkCard
            key={link.id}
            link={link}
            busy={busyId === link.id}
            onUnlink={() => unlink(link)}
          />
        ))
      )}

      {error ? (
        <Pressable onPress={() => setError(null)}>
          <Text style={styles.error}>{error}</Text>
        </Pressable>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: spacing.lg,
    gap: spacing.md,
    flexGrow: 1,
    backgroundColor: colors.surface,
  },
  intro: { gap: spacing.sm },
  introTitle: { ...type.titleMedium, color: colors.onSurface },
  introText: { ...type.bodyMedium, color: colors.onSurfaceVariant },
  steps: { gap: spacing.sm, marginTop: spacing.xs },
  step: { gap: 2 },
  stepLabel: { ...type.labelMedium, color: colors.onSurfaceVariant },
  stepText: { ...type.bodySmall, color: colors.onSurface },
  sectionLabel: {
    ...type.labelMedium,
    color: colors.onSurfaceVariant,
    marginTop: spacing.xs,
  },
  loading: { marginTop: spacing.lg },
  card: { gap: spacing.sm },
  cardTop: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.md },
  cardTitleWrap: { flex: 1 },
  cardTitle: { ...type.titleMedium, color: colors.onSurface },
  cardSub: { ...type.bodySmall, color: colors.onSurfaceVariant, marginTop: 2 },
  meta: { ...type.bodySmall, color: colors.onSurfaceVariant },
  unlink: { alignSelf: 'flex-start' },
  empty: { gap: spacing.sm },
  emptyTitle: { ...type.titleMedium, color: colors.onSurface },
  emptyText: { ...type.bodyMedium, color: colors.onSurfaceVariant },
  error: {
    ...type.bodySmall,
    color: colors.error,
    textAlign: 'center',
    marginTop: spacing.sm,
  },
});
