// Launcher for the Google Calendar MCP server.
import { existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = fileURLToPath(new URL('.', import.meta.url));
const packageDir = `${here}node_modules/@cocal/google-calendar-mcp/`;
const candidates = ['build/index.js', 'dist/index.js', 'index.js'];

const entry = candidates.map((c) => packageDir + c).find(existsSync);
if (!entry) {
  console.error('[google-calendar-mcp] @cocal/google-calendar-mcp is not installed.');
  console.error('[google-calendar-mcp] Run: cd tools/mcp/google-calendar && npm install');
  process.exit(1);
}

await import(pathToFileURL(entry).href);
