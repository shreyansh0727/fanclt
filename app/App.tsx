import React from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from 'react-native';
import { DarkTheme, NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';

import { isFirebaseConfigured } from './src/config';
import { Card } from './src/components/Card';
import { UpdateGate } from './src/components/UpdateGate';
import { AppProvider, useApp } from './src/lib/store';
import type { RootStackParamList } from './src/nav';
import AddDeviceScreen from './src/screens/AddDeviceScreen';
import AuthScreen from './src/screens/AuthScreen';
import DeviceScreen from './src/screens/DeviceScreen';
import DevicesScreen from './src/screens/DevicesScreen';
import SchedulesScreen from './src/screens/SchedulesScreen';
import { colors, spacing, type } from './src/theme';

const Stack = createNativeStackNavigator<RootStackParamList>();

const navTheme = {
  ...DarkTheme,
  colors: {
    ...DarkTheme.colors,
    primary: colors.primary,
    background: colors.surface,
    card: colors.surface,
    text: colors.onSurface,
    border: colors.outlineVariant,
    notification: colors.error,
  },
};

function AppContent() {
  const { authReady, user } = useApp();

  if (!authReady) {
    return (
      <View style={styles.splash}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  return (
    <NavigationContainer theme={navTheme}>
      <Stack.Navigator
        screenOptions={{
          headerStyle: { backgroundColor: colors.surface },
          headerTintColor: colors.onSurface,
          headerTitleStyle: {
            fontSize: 22,
            fontWeight: '400',
          },
          headerTitleAlign: 'left',
          headerShadowVisible: false,
          contentStyle: { backgroundColor: colors.surface },
        }}
      >
        {user ? (
          <>
            <Stack.Screen
              name="Devices"
              component={DevicesScreen}
              options={{ title: 'FanCtl' }}
            />
            <Stack.Screen
              name="Device"
              component={DeviceScreen}
              options={{ title: 'Controller' }}
            />
            <Stack.Screen
              name="AddDevice"
              component={AddDeviceScreen}
              options={{ title: 'Pair a controller' }}
            />
            <Stack.Screen name="Schedules" component={SchedulesScreen} />
          </>
        ) : (
          <Stack.Screen
            name="Auth"
            component={AuthScreen}
            options={{ headerShown: false }}
          />
        )}
      </Stack.Navigator>
    </NavigationContainer>
  );
}

function ConfigNotice() {
  return (
    <ScrollView contentContainerStyle={styles.noticeContainer}>
      <Card tone="outlined" style={styles.noticeCard}>
        <Text style={styles.noticeTitle}>Firebase is not configured</Text>
        <Text style={styles.noticeText}>
          Open app/app.json and replace the placeholder values in `expo.extra` with your Firebase
          web app config (apiKey, authDomain, projectId, appId) and set `apiBase` to the address of
          your FastAPI server.
        </Text>
        <Text style={styles.noticeText}>
          Then restart the dev server with `npx expo start -c` so the new values are picked up.
        </Text>
      </Card>
    </ScrollView>
  );
}

export default function App() {
  return (
    <SafeAreaProvider>
      <StatusBar style="light" />
      {isFirebaseConfigured ? (
        <AppProvider>
          <View style={styles.flex}>
            <AppContent />
            <UpdateGate />
          </View>
        </AppProvider>
      ) : (
        <ConfigNotice />
      )}
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.surface },
  splash: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.surface,
  },
  noticeContainer: {
    flexGrow: 1,
    justifyContent: 'center',
    padding: spacing.xl,
    backgroundColor: colors.surface,
  },
  noticeCard: { gap: spacing.sm },
  noticeTitle: { ...type.titleMedium, color: colors.onSurface },
  noticeText: { ...type.bodyMedium, color: colors.onSurfaceVariant },
});
