// Copy the PDF.js worker into /public so it is served from our own origin.
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const src = require.resolve("pdfjs-dist/build/pdf.worker.min.mjs");
mkdirSync("public/pdfjs", { recursive: true });
copyFileSync(src, "public/pdfjs/pdf.worker.min.mjs");
console.log("pdf.worker copied");
