// mobile/__tests__/app/route-tree.test.ts
// Guards the one navigation failure that no component test can see.
//
// Expo Router builds its route tree from the file system, then hands each layout's
// children to React Navigation as <Screen name={...}> elements. React Navigation
// throws on the spot when a navigator receives two screens with the same name
// (useNavigationBuilder.js: "A navigator cannot contain multiple 'Screen' components
// with the same name"). In a release bundle there is no red box to catch it, so the
// app simply dies the moment that layout mounts.
//
// That is exactly what `breakdown/[songId].tsx` + `breakdown/[songId]/_layout.tsx`
// did: two sibling nodes both named `[songId]` under the breakdown Stack, so tapping
// "See the breakdown" killed the app before a single pixel of the screen rendered.
// Every screen still unit-tested green — the screens were never the problem; the
// shape of the directory was.
//
// This test rebuilds the real tree from src/app and asserts sibling names are unique
// at every level. It reads the filesystem rather than mocking it, so any future route
// added as both a file and a directory-with-layout fails here instead of on a phone.
import { readdirSync } from 'fs';
import { join } from 'path';

// getRoutesCore is the same builder the runtime uses; it takes a Metro require.context.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { getRoutes } = require('expo-router/build/getRoutesCore');

const APP_DIR = join(__dirname, '..', '..', 'src', 'app');

function routeFiles(dir: string, prefix = '.'): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const key = `${prefix}/${entry.name}`;
    if (entry.isDirectory()) return routeFiles(join(dir, entry.name), key);
    if (!/\.(tsx|ts|jsx|js)$/.test(entry.name)) return [];
    if (/\.(test|spec)\./.test(entry.name)) return [];
    return [key];
  });
}

// Minimal stand-in for Metro's require.context: the builder only needs keys() plus a
// module with a default export per key.
function contextModule(keys: string[]) {
  const ctx = () => ({ default: function Screen() { return null; } });
  ctx.keys = () => keys;
  ctx.resolve = (id: string) => id;
  ctx.id = 'app';
  return ctx;
}

type Node = { route: string; type?: string; children?: Node[] };

function duplicateSiblings(node: Node, path = ''): string[] {
  const children = node.children ?? [];
  const seen = new Set<string>();
  const found: string[] = [];
  for (const child of children) {
    if (seen.has(child.route)) found.push(`${path}/${child.route}`);
    seen.add(child.route);
  }
  return children.reduce<string[]>(
    (acc, child) => acc.concat(duplicateSiblings(child, `${path}/${child.route}`)),
    found,
  );
}

describe('expo-router route tree', () => {
  const tree = getRoutes(contextModule(routeFiles(APP_DIR)), {
    ignoreRequireErrors: true,
    platform: 'ios',
  }) as Node;

  it('gives every navigator uniquely named children', () => {
    // A hit here is a production crash on entering that navigator, not a lint nit.
    expect(duplicateSiblings(tree)).toEqual([]);
  });

  it('routes /breakdown/[songId] and its drill screen under one breakdown Stack', () => {
    const breakdown = tree.children?.find((c) => c.route === 'breakdown');
    const names = breakdown?.children?.map((c) => c.route) ?? [];
    expect(names).toContain('[songId]');
    expect(names).toContain('[songId]/drill/[drillIndex]');
  });
});
