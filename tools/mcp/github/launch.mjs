// Launcher for the official GitHub MCP server.
import { existsSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = fileURLToPath(new URL('.', import.meta.url));
const packageDir = `${here}node_modules/@modelcontextprotocol/server-github/`;
const candidates = ['dist/index.js', 'index.js'];

const entry = candidates.map((c) => packageDir + c).find(existsSync);
if (!entry) {
  console.error('[github-mcp] @modelcontextprotocol/server-github is not installed.');
  console.error('[github-mcp] Run: cd tools/mcp/github && npm install');
  process.exit(1);
}

await import(pathToFileURL(entry).href);
