const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { parse: parseIcuMessage } = require("@formatjs/icu-messageformat-parser");

const root = path.resolve(__dirname, "..");
const catalogDir = path.join(root, "messages");
const sourceRoots = ["app", "components", "lib"].map((directory) => path.join(root, directory));
const allowedCompatibilityValues = new Set([
  // Existing installations may already have a source with this historical name.
  "对话上传",
]);

function readCatalog(locale) {
  return JSON.parse(fs.readFileSync(path.join(catalogDir, `${locale}.json`), "utf8"));
}

function flattenKeys(value, prefix = "", result = []) {
  for (const [key, child] of Object.entries(value)) {
    const next = prefix ? `${prefix}.${key}` : key;
    if (child && typeof child === "object" && !Array.isArray(child)) {
      flattenKeys(child, next, result);
    } else {
      result.push(next);
    }
  }
  return result;
}

function invalidIcuMessages(value, locale, prefix = "", result = []) {
  for (const [key, child] of Object.entries(value)) {
    const next = prefix ? `${prefix}.${key}` : key;
    if (child && typeof child === "object" && !Array.isArray(child)) {
      invalidIcuMessages(child, locale, next, result);
    } else if (typeof child === "string") {
      try {
        parseIcuMessage(child);
      } catch (error) {
        result.push({ locale, key: next, error: error instanceof Error ? error.message : String(error) });
      }
    }
  }
  return result;
}

function collectSourceFiles(directory, result = []) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const filename = path.join(directory, entry.name);
    if (entry.isDirectory()) collectSourceFiles(filename, result);
    else if (/\.(?:ts|tsx|js|jsx)$/.test(entry.name) && !/\.test\.[^.]+$/.test(entry.name)) {
      result.push(filename);
    }
  }
  return result;
}

function userFacingHanLiterals(filename) {
  const content = fs.readFileSync(filename, "utf8");
  const sourceFile = ts.createSourceFile(
    filename,
    content,
    ts.ScriptTarget.Latest,
    true,
    filename.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const findings = [];
  const check = (text, node) => {
    if (!/[\u3400-\u9fff]/u.test(text) || allowedCompatibilityValues.has(text)) return;
    findings.push({
      line: sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1,
      text: text.replace(/\s+/g, " ").slice(0, 100),
    });
  };
  const visit = (node) => {
    if (
      ts.isStringLiteral(node)
      || ts.isNoSubstitutionTemplateLiteral(node)
      || ts.isJsxText(node)
    ) {
      check(node.text ?? node.getText(sourceFile), node);
    } else if (ts.isTemplateExpression(node)) {
      check(node.head.text, node.head);
      for (const span of node.templateSpans) check(span.literal.text, span.literal);
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return findings;
}

const zhCatalog = readCatalog("zh-CN");
const enCatalog = readCatalog("en-US");
const zhKeys = flattenKeys(zhCatalog).sort();
const enKeys = flattenKeys(enCatalog).sort();
const zhSet = new Set(zhKeys);
const enSet = new Set(enKeys);
const missingInEnglish = zhKeys.filter((key) => !enSet.has(key));
const missingInChinese = enKeys.filter((key) => !zhSet.has(key));
const invalidMessages = [
  ...invalidIcuMessages(zhCatalog, "zh-CN"),
  ...invalidIcuMessages(enCatalog, "en-US"),
];
const sourceFindings = sourceRoots.flatMap((directory) =>
  collectSourceFiles(directory).flatMap((filename) =>
    userFacingHanLiterals(filename).map((finding) => ({ filename, ...finding })),
  ),
);

if (
  missingInEnglish.length
  || missingInChinese.length
  || invalidMessages.length
  || sourceFindings.length
) {
  if (missingInEnglish.length) {
    console.error(`Missing in en-US:\n${missingInEnglish.map((key) => `  ${key}`).join("\n")}`);
  }
  if (missingInChinese.length) {
    console.error(`Missing in zh-CN:\n${missingInChinese.map((key) => `  ${key}`).join("\n")}`);
  }
  if (invalidMessages.length) {
    console.error("Invalid ICU messages:");
    for (const finding of invalidMessages) {
      console.error(`  ${finding.locale}:${finding.key} ${finding.error}`);
    }
  }
  if (sourceFindings.length) {
    console.error("Hard-coded Han text outside the locale catalogs:");
    for (const finding of sourceFindings) {
      console.error(`  ${path.relative(root, finding.filename)}:${finding.line} ${finding.text}`);
    }
  }
  process.exitCode = 1;
} else {
  console.log(`i18n check passed: ${zhKeys.length} message keys across 2 locales.`);
}
