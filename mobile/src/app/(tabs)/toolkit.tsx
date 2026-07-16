// mobile/src/app/(tabs)/toolkit.tsx
// Toolkit tab — placeholder screen. Metronome + tuner added in Phase 5.
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

export default function ToolkitScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.text}>Toolkit — Coming soon</Text>
      <Text style={styles.subtext}>Metronome and chromatic tuner.</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#fff',
    padding: 24,
  },
  text: {
    fontSize: 20,
    fontWeight: '700',
    color: '#1a1a1a',
    marginBottom: 8,
  },
  subtext: {
    fontSize: 14,
    color: '#888',
    textAlign: 'center',
  },
});
