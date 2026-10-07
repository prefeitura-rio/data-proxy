import { execFile } from "node:child_process";
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const run = promisify(execFile);

const directory = new URL(".", import.meta.url);
const grammar = fileURLToPath(new URL("filter.peggy", directory));
const parser = fileURLToPath(new URL("filter.generated.js", directory));
const declaration = fileURLToPath(new URL("filter.generated.d.ts", directory));
const peggy = fileURLToPath(new URL("../bin/peggy.js", import.meta.resolve("peggy")));

await run(process.execPath, [
    peggy,
    "--format",
    "es",
    "--dts",
    "--output",
    parser,
    grammar,
]);

// njs rejects any named export, so the parser keeps only a default export.
const generated = await readFile(parser, "utf8");
const withoutNamedExports = generated.replace(/\nexport \{[\s\S]*?\};\s*$/, "");
const wrapped = `${withoutNamedExports}\nexport default { StartRules: peg$allowedStartRules, parse: peg$parse, SyntaxError: peg$SyntaxError };\n`;

await writeFile(parser, wrapped);

// The generated declaration describes the named exports it no longer has.
const types = await readFile(declaration, "utf8");
const local = types
    .replace("export declare class SyntaxError", "declare class SyntaxError")
    .replace("export declare const StartRules:", "declare const StartRules:")
    .replace("export declare const parse:", "declare const parse:");
const defaultExport = "\ndeclare const parser: {\n    StartRules: typeof StartRules;\n    parse: typeof parse;\n    SyntaxError: typeof SyntaxError;\n};\nexport default parser;\n";

await writeFile(declaration, local + defaultExport);
