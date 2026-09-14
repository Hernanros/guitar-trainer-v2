// mobile/src/app/breakdown/[songId]/_layout.tsx
// Nested Stack layout for the drill route group.
// Expo Router v57 nested dynamic route: breakdown/[songId]/drill/[drillIndex].tsx
// Docs: https://docs.expo.dev/versions/v57.0.0/router/
//
// This layout gives the drill screen its own navigation scope within the
// breakdown/[songId] route group. The parent breakdown/[songId].tsx screen
// continues to serve as the main breakdown list view via the parent
// breakdown/_layout.tsx Stack navigator.
//
// Stack config mirrors the parent breakdown/_layout.tsx — dark header with
// Fletcher orange accent, no shadow. The drill screen provides its own title
// via its content (drill.name rendered as a large header text).
import { Stack } from 'expo-router';

export default function DrillLayout() {
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
