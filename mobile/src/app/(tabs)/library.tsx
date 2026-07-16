// mobile/src/app/(tabs)/library.tsx
// Library tab — placeholder screen. Functionality added in Phase 5.
import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

export default function LibraryScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.text}>Library — Coming soon</Text>
      <Text style={styles.subtext}>Browse and manage your tracked songs.</Text>
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
