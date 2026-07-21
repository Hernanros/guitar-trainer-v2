// mobile/src/app/(tabs)/_layout.tsx
// Four-tab navigation shell — Today / Library / Toolkit / Settings.
// Settings added in 02-04; first three tabs are unchanged from 01-04.
// Pattern: Ionicons + Expo Router Tabs, activeColor #E07B39 per palette.
import { Tabs } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import type { ColorValue } from 'react-native';

export default function TabLayout() {
  return (
    <Tabs screenOptions={{ tabBarActiveTintColor: '#E07B39' }}>
      <Tabs.Screen
        name="index"
        options={{
          title: 'Today',
          tabBarIcon: ({ color }: { color: ColorValue }) => (
            <Ionicons name="musical-notes" size={24} color={color as string} />
          ),
        }}
      />
      <Tabs.Screen
        name="library"
        options={{
          title: 'Library',
          tabBarIcon: ({ color }: { color: ColorValue }) => (
            <Ionicons name="library" size={24} color={color as string} />
          ),
        }}
      />
      <Tabs.Screen
        name="toolkit"
        options={{
          title: 'Toolkit',
          tabBarIcon: ({ color }: { color: ColorValue }) => (
            <Ionicons name="construct" size={24} color={color as string} />
          ),
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{
          title: 'Settings',
          tabBarIcon: ({ color }: { color: ColorValue }) => (
            <Ionicons name="settings" size={24} color={color as string} />
          ),
        }}
      />
    </Tabs>
  );
}
