import { build } from 'esbuild';
import { mkdir, copyFile } from 'node:fs/promises';

await mkdir('dist', { recursive: true });
await build({
  entryPoints: ['index.ts'],
  bundle: true,
  format: 'iife',
  target: 'es2019',
  platform: 'neutral',
  minify: true,
  outfile: 'dist/index.js',
  logLevel: 'info',
});
await copyFile('index.html', 'dist/index.html');
// The developer console docs point the app URL at /local-home/index.html.
await mkdir('dist/local-home', { recursive: true });
await copyFile('index.html', 'dist/local-home/index.html');
await copyFile('dist/index.js', 'dist/local-home/index.js');
console.log('built dist/index.js and dist/index.html (+ /local-home alias)');
