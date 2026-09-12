// Launcher for the Playwright MCP server.
// Resolves the package's CLI entry after `npm install`, so the Codex config
// does not depend on a hard-coded, version-specific path.
import { existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = fileURLToPath(new URL('.', import.meta.url));
const packageDir = `${here}node_modules/@playwright/mcp/`;
const candidates = ['cli.js', 'dist/cli.js', 'index.js'];

const entry = candidates.map((c) => packageDir + c).find(existsSync);
if (!entry) {
  console.error('[playwright-mcp] @playwright/mcp is not installed.');
  console.error('[playwright-mcp] Run: cd tools/mcp/playwright && npm install');
  process.exit(1);
}

await import(pathToFileURL(entry).href);
