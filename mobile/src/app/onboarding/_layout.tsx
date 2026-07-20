// mobile/src/app/onboarding/_layout.tsx
// Onboarding wizard route group shell — 5 sections registered (02-02).
//
// Uses Stack (not Tabs) — the wizard is a linear forward-only flow.
// gestureEnabled: false prevents swipe-back (intentional: user must tap Continue
//   or force-quit; swipe-back would skip MMKV state writes).
// headerShown: false — FletcherIntroCard renders the full-screen header itself.
//
// Expo Router v57 Stack.Screen name values must match the file names exactly.
// File routing verified: index → play → working-on → aspire → preferences.
import { Stack } from 'expo-router';

export default function OnboardingLayout() {
  return (
    <Stack screenOptions={{ headerShown: false, gestureEnabled: false }}>
      <Stack.Screen name="index" />
      <Stack.Screen name="play" />
      <Stack.Screen name="working-on" />
      <Stack.Screen name="aspire" />
      <Stack.Screen name="preferences" />
    </Stack>
  );
}
