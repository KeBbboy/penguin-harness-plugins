/**
 * The online half of the checks: does npm actually serve what the manifest claims?
 *
 * Scoped to the manifests a pull request touches (zed's `changedExtensionIds` rule) —
 * re-probing every listed package on every run would make CI slow and would fail the
 * repository whenever an unrelated author's registry entry hiccups. `--all` forces a
 * full sweep for the periodic audit.
 */
import fs from "node:fs";
import { changedManifests, hasRef, listManifestFiles } from "./lib/git.js";
import { ManifestError, parseManifest } from "./lib/manifest.js";
import { checkAgainstNpm } from "./lib/npm.js";

const root = process.cwd();
const baseRef = process.env.BASE_REF ?? "origin/main";
const all = process.argv.includes("--all");

function targets(): string[] {
  if (all || !hasRef(baseRef)) return listManifestFiles(root);
  return changedManifests(baseRef)
    .filter((change) => change.status === "added" || change.status === "modified")
    .map((change) => change.file);
}

const files = targets();
if (files.length === 0) {
  console.log("No manifests to check against npm.");
  process.exit(0);
}

const problems: string[] = [];
for (const file of files) {
  let manifest;
  try {
    manifest = parseManifest(fs.readFileSync(file, "utf8"), file);
  } catch (err) {
    problems.push(err instanceof ManifestError ? err.message : String(err));
    continue;
  }
  if (manifest.yanked === true) {
    console.log(`${manifest.name}: yanked, skipping npm checks.`);
    continue;
  }
  console.log(`Checking ${manifest.name}@${manifest.version} against npm…`);
  const found = await checkAgainstNpm(manifest);
  if (found.length > 0) {
    problems.push([`${file}:`, ...found.map((problem) => `  - ${problem}`)].join("\n"));
  }
}

if (problems.length > 0) {
  console.error(`\n${problems.length} package(s) did not match npm:\n`);
  for (const problem of problems) console.error(`${problem}\n`);
  process.exit(1);
}
console.log("All checked packages match their published versions.");
