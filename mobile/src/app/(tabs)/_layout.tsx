// mobile/src/app/(tabs)/_layout.tsx
// Three-tab navigation shell — Today / Library / Toolkit.
// Pattern 1 from RESEARCH.md.
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
    </Tabs>
  );
}
