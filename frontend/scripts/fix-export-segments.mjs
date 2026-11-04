// Flattens Next.js static-export segment files that were written into nested folders.
//
// With `output: "export"` the client router prefetches per-segment files named
// `<route>/__next.<segment>.<path>.txt` (slashes in the segment path replaced by dots).
// When `next build` runs on Windows, Next.js 16.4 builds that name from a path that
// still contains backslashes, so the file ends up in a sub-folder instead:
//   out\evaluate\__next.evaluate\__PAGE__.txt   (should be out\evaluate\__next.evaluate.__PAGE__.txt)
// The browser then gets a 404 for every page prefetch (visible in the console as
// "__next.evaluate.__PAGE__.txt 404"). This script renames such files to the flat
// name the router asks for. On Linux/macOS builds nothing is nested and it is a no-op.
//
// Usage: node scripts/fix-export-segments.mjs [outDir]   (default: ./out, runs after `next build`)
import { existsSync, readdirSync, renameSync, rmdirSync, statSync } from "node:fs";
import { join } from "node:path";

const outDir = process.argv[2] ?? "out";
if (!existsSync(outDir) || !statSync(outDir).isDirectory()) process.exit(0); // not a static export build

let moved = 0;

/** Move every file below `dir` to `parent/<prefix>.<relative path joined with dots>` and drop the folder. */
function flatten(parent, dir, prefix) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      flatten(parent, full, `${prefix}.${entry.name}`);
    } else {
      renameSync(full, join(parent, `${prefix}.${entry.name}`));
      moved++;
    }
  }
  rmdirSync(dir);
}

function walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const full = join(dir, entry.name);
    if (entry.name.startsWith("__next.")) flatten(dir, full, entry.name);
    else if (entry.name !== "_next") walk(full);
  }
}

walk(outDir);
if (moved) console.log(`fix-export-segments: moved ${moved} nested segment file(s) in ${outDir}/ to flat names`);
