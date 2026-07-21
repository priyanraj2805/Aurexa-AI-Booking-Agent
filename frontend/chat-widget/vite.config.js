import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
  build: {
    // Output a single self-contained JS file (IIFE — no module imports)
    lib: {
      entry: resolve(__dirname, 'src/index.js'),
      name: 'DotStarkWidget',
      formats: ['iife'],
      fileName: () => 'widget.js',
    },
    // Inline all CSS into the JS bundle (Shadow DOM uses inline styles already)
    cssCodeSplit: false,
    // Single chunk — no code splitting
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
      },
    },
    // Output to dist/
    outDir: 'dist',
    emptyOutDir: true,
    // Minify for production (esbuild is built into Vite)
    minify: 'esbuild',
  },
  // Dev server config (unchanged behavior)
  server: {
    port: 5174,
    open: true,
  },
});
