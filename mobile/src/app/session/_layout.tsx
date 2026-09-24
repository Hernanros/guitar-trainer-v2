// mobile/src/app/session/_layout.tsx
// Layout for the session-player route group (FLE-10 Task 6).
//
// Headerless, gesture-back disabled: an item is left by finishing or skipping it,
// never by swiping away (design doc §3). A swipe-back that silently un-does an
// item would poison completion_ratio, which feeds FLE-4 mode rule 3.
//
// useKeepAwake mounted here (not per-screen) so the screen never sleeps for the
// whole lifetime of a session, hands occupied on the guitar. Costs no new native
// build — expo-keep-awake is already compiled in (FLE-10 design doc §1, §6).
import { Stack } from 'expo-router';
import { useKeepAwake } from 'expo-keep-awake';

export default function SessionLayout() {
  useKeepAwake();
  return (
    <Stack
      screenOptions={{
        headerShown: false,
        gestureEnabled: false,
      }}
    />
  );
}
