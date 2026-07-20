// mobile/src/app/onboarding/_layout.tsx
// Onboarding route group shell (Phase 2).
// Uses Stack (not Tabs) — the wizard is a linear flow, not a tab-bar navigation.
//
// gestureEnabled: false prevents swipe-back from the onboarding sections
// (the user should complete or use the explicit "Back" button, not swipe).
// headerShown: false — each section renders its own title/progress UI.
//
// 02-02 will add more Stack.Screen entries for play/working-on/aspire/preferences
// and replace index.tsx with the full Welcome screen.
import { Stack } from 'expo-router';

export default function OnboardingLayout() {
  return (
    <Stack screenOptions={{ headerShown: false, gestureEnabled: false }}>
      <Stack.Screen name="index" />
    </Stack>
  );
}
