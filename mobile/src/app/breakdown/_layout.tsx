// mobile/src/app/breakdown/_layout.tsx
// Layout for the breakdown route group.
// Expo Router v57 dynamic route: breakdown/[songId].tsx
// Docs: https://docs.expo.dev/versions/v57.0.0/router/
//
// Stack navigator with dark header matching the app palette.
// headerTitle: '' — the breakdown content is self-titled via technique_notes.
import { Stack } from 'expo-router';

export default function BreakdownLayout() {
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: '#1A1A1A' },
        headerTintColor: '#F5F5F5',
        headerTitle: '',
        headerShadowVisible: false,
      }}
    />
  );
}
