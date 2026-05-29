/**
 * i18n health check.
 *
 * 1. Cross-locale structural parity — same namespace files, same keys, same
 *    value types between en / zh-CN / zh-TW. Hard fail.
 * 2. Hardcoded-CJK scan — finds Chinese text in .vue / .js source that is
 *    NOT wrapped by t(...) / i18n.t(...) / $t(...). Reports as a soft
 *    warning by default; pass --strict-hardcoded to make it fail the build.
 *    Pass --quiet-hardcoded to suppress the listing entirely.
 *
 * CLI flags:
 *   --strict-hardcoded     hardcoded CJK in source → exit 1
 *   --quiet-hardcoded      suppress hardcoded report
 *   --strict-unused        possibly-unused locale keys → exit 1
 *
 * Exit code 1 on any structural inconsistency, or on flagged soft-fails.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const LOCALES_DIR = path.join(ROOT, "src", "locales");
const SRC = path.join(ROOT, "src");

const LOCALE_DIRS = {
  en: path.join(LOCALES_DIR, "en"),
  "zh-CN": path.join(LOCALES_DIR, "zh-CN"),
  "zh-TW": path.join(LOCALES_DIR, "zh-TW"),
};
const REFERENCE_LOCALE = "en";

const ARGS = new Set(process.argv.slice(2));
const STRICT_HARDCODED = ARGS.has("--strict-hardcoded");
const QUIET_HARDCODED = ARGS.has("--quiet-hardcoded");
const STRICT_UNUSED = ARGS.has("--strict-unused");

// Files / paths to skip during hardcoded-CJK scanning. These are either
// fixtures (binding labels), display-only debug shims, or assets we
// don't translate.
const HARDCODED_IGNORE_PATHS = [
  /[\\/]locales[\\/]/,
  /[\\/]node_modules[\\/]/,
  /[\\/]dist[\\/]/,
  /[\\/]__tests__[\\/]/,
  /\.test\.(js|ts|vue)$/,
  /\.spec\.(js|ts|vue)$/,
];

function getJsonType(v) {
  if (v === null) return "null";
  if (Array.isArray(v)) return "array";
  return typeof v;
}

/** @returns {Map<string, string>} path -> type */
function collectKeys(obj, prefix = "") {
  const out = new Map();
  if (obj === null || typeof obj !== "object") {
    out.set(prefix || "(root)", getJsonType(obj));
    return out;
  }
  if (Array.isArray(obj)) {
    out.set(prefix || "(root)", "array");
    return out;
  }
  for (const key of Object.keys(obj)) {
    const p = prefix ? `${prefix}.${key}` : key;
    const val = obj[key];
    const t = getJsonType(val);
    if (t === "object" && val !== null && !Array.isArray(val)) {
      const nested = collectKeys(val, p);
      for (const [k, typ] of nested) out.set(k, typ);
    } else {
      out.set(p, t);
    }
  }
  return out;
}

function listJsonFiles(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".json"))
    .sort();
}

function loadJson(filePath) {
  const raw = fs.readFileSync(filePath, "utf8");
  try {
    return JSON.parse(raw);
  } catch (e) {
    throw new Error(`Invalid JSON: ${filePath} - ${e.message}`);
  }
}

/** @returns {string[]} */
function listSourceFiles(dir, acc = []) {
  if (!fs.existsSync(dir)) return acc;
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name);
    const st = fs.statSync(full);
    if (st.isDirectory()) {
      if (name !== "locales" && name !== "node_modules")
        listSourceFiles(full, acc);
    } else if (
      (name.endsWith(".ts") ||
        name.endsWith(".tsx") ||
        name.endsWith(".js") ||
        name.endsWith(".jsx") ||
        name.endsWith(".mjs") ||
        name.endsWith(".vue")) &&
      !name.endsWith(".d.ts")
    ) {
      acc.push(full);
    }
  }
  return acc;
}

/** 收集所有 namespace:keyPath（仅用于未使用检测，基于字符串搜索，保守仅供参考） */
function reportPossiblyUnusedKeys(commonFiles, refDir) {
  const allKeys = [];
  for (const file of commonFiles) {
    const ns = file.replace(/\.json$/, "");
    const obj = loadJson(path.join(refDir, file));
    const keys = collectKeys(obj);
    for (const [keyPath] of keys) {
      if (keyPath === "(root)") continue;
      allKeys.push(`${ns}:${keyPath}`);
    }
  }
  const sourceFiles = listSourceFiles(SRC);
  let sourceContent = "";
  for (const f of sourceFiles) {
    try {
      sourceContent += fs.readFileSync(f, "utf8") + "\n";
    } catch (_) {}
  }

  const mightBeUsed = (ns, keyPath) => {
    // Common patterns:
    // - t("ns:keyPath") / t('ns:keyPath')
    // - useTranslation("ns") then t("keyPath") / t('keyPath')
    const withNs = `${ns}:${keyPath}`;
    return (
      sourceContent.includes(withNs) ||
      sourceContent.includes(`t("${withNs}")`) ||
      sourceContent.includes(`t('${withNs}')`) ||
      sourceContent.includes(`t("${keyPath}")`) ||
      sourceContent.includes(`t('${keyPath}')`)
    );
  };

  const possiblyUnused = allKeys.filter((ref) => {
    const [ns, keyPath] = ref.split(":");
    if (!ns || !keyPath) return false;
    return !mightBeUsed(ns, keyPath);
  });
  if (possiblyUnused.length === 0) return { count: 0 };
  console.log("\n--- 可能未使用的 key（仅供参考）---");
  possiblyUnused.forEach((k) => console.log(`  ${k}`));
  console.log(
    '  说明: 本报告基于字符串搜索 t("namespace:key") / useTranslation("namespace") + t("key")，可能存在漏报/误报；动态 key 会误报为未使用。不导致脚本失败（除非 --strict-unused）。',
  );
  return { count: possiblyUnused.length };
}

/* ----------------------------- hardcoded CJK ----------------------------- */

const CJK_RE = /[一-鿿㐀-䶿]/;
// Match any chunk that contains CJK chars (greedy across consecutive CJK + ascii within the same line).
const CJK_RUN = /[^\s<>{}"'`]*[一-鿿㐀-䶿]+[^\s<>{}"'`]*/g;

function shouldSkipFile(file) {
  return HARDCODED_IGNORE_PATHS.some((re) => re.test(file));
}

function stripBlocks(src, openRe, closeRe) {
  // Remove everything between an opening tag and its closing tag (script/style blocks),
  // replacing with newlines to preserve line numbers.
  let out = src;
  let m;
  while ((m = openRe.exec(out))) {
    const tail = out.slice(m.index + m[0].length);
    const close = tail.search(closeRe);
    if (close === -1) break;
    const blob = out.slice(m.index, m.index + m[0].length + close);
    const blanked = blob.replace(/[^\n]/g, " ");
    out = out.slice(0, m.index) + blanked + out.slice(m.index + blob.length);
    openRe.lastIndex = m.index + blanked.length;
  }
  return out;
}

function stripComments(src) {
  // Replace /* ... */ and // ... \n with spaces (preserve newlines).
  let out = src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "));
  out = out.replace(
    /(^|[^:])\/\/[^\n]*/g,
    (m, p) => p + m.slice(p.length).replace(/./g, " "),
  );
  return out;
}

/**
 * Returns true if the offset is inside a t("...") / $t('...') / i18n.t(...) call
 * by scanning a small window before the match.
 */
function isInsideTCall(text, offset) {
  const start = Math.max(0, offset - 80);
  const window = text.slice(start, offset);
  return /\b(?:\$?t|i18n\.t|i18next\.t|useTranslation)\s*\(\s*['"`][^'"`]*$/.test(
    window,
  );
}

/**
 * True if the CJK chunk is inside an HTML/Vue comment.
 */
function isInsideHtmlComment(text, offset) {
  const before = text.lastIndexOf("<!--", offset);
  if (before === -1) return false;
  const close = text.indexOf("-->", before);
  return close === -1 || close > offset;
}

function scanHardcodedCjk(file) {
  let src;
  try {
    src = fs.readFileSync(file, "utf8");
  } catch {
    return [];
  }
  if (!CJK_RE.test(src)) return [];

  // For .vue we want to scan template text + script string literals but NOT style.
  let scan = src;
  if (file.endsWith(".vue")) {
    scan = stripBlocks(scan, /<style\b[^>]*>/gi, /<\/style>/i);
  }
  scan = stripComments(scan);

  const findings = [];
  let m;
  CJK_RUN.lastIndex = 0;
  while ((m = CJK_RUN.exec(scan))) {
    const chunk = m[0];
    const offset = m.index;
    if (!CJK_RE.test(chunk)) continue;
    if (isInsideHtmlComment(scan, offset)) continue;
    if (isInsideTCall(scan, offset)) continue;

    // Skip if this looks like a console.log / throw new Error / log call.
    const lineStart = scan.lastIndexOf("\n", offset) + 1;
    const lineEnd = scan.indexOf("\n", offset);
    const lineText = scan.slice(
      lineStart,
      lineEnd === -1 ? scan.length : lineEnd,
    );
    if (
      /\b(?:console|logger|log)\.(?:log|warn|error|info|debug)\s*\(/.test(
        lineText,
      )
    )
      continue;
    if (/\bthrow\s+new\s+\w*Error/.test(lineText)) continue;

    const line = scan.slice(0, offset).split("\n").length;
    findings.push({
      file,
      line,
      snippet: lineText.trim().slice(0, 200),
      chunk,
    });
  }
  return findings;
}

function reportHardcodedCjk() {
  if (QUIET_HARDCODED) return { count: 0, failed: false };
  const sourceFiles = listSourceFiles(SRC).filter((f) => !shouldSkipFile(f));
  const all = [];
  for (const f of sourceFiles) {
    for (const finding of scanHardcodedCjk(f)) all.push(finding);
  }
  if (all.length === 0) {
    console.log("\n--- 硬编码 CJK 扫描：未发现 ---");
    return { count: 0, failed: false };
  }
  console.log(`\n--- 硬编码 CJK 文案（${all.length} 处）---`);
  // Group by file for readability
  const byFile = new Map();
  for (const f of all) {
    if (!byFile.has(f.file)) byFile.set(f.file, []);
    byFile.get(f.file).push(f);
  }
  for (const [file, items] of byFile) {
    const rel = path.relative(ROOT, file);
    console.log(`\n  ${rel}`);
    for (const it of items) {
      console.log(`    L${it.line}: ${it.chunk}  ⟵  ${it.snippet}`);
    }
  }
  const failed = STRICT_HARDCODED;
  console.log(
    `\n  说明: 扫描排除 t()/$t()/i18n.t() 包裹、console/log/throw、HTML 注释、style 块。` +
      `${failed ? "（--strict-hardcoded 已启用：本批次将判定失败）" : "（默认软提示，--strict-hardcoded 可升级为失败）"}`,
  );
  return { count: all.length, failed };
}

function main() {
  console.log(
    `i18n health check (reference: ${REFERENCE_LOCALE} vs ${Object.keys(
      LOCALE_DIRS,
    )
      .filter((l) => l !== REFERENCE_LOCALE)
      .join(", ")})\n`,
  );

  const errors = [];
  const refFiles = listJsonFiles(LOCALE_DIRS[REFERENCE_LOCALE]);

  // Check each non-reference locale
  for (const [locale, localeDir] of Object.entries(LOCALE_DIRS)) {
    if (locale === REFERENCE_LOCALE) continue;
    const localeFiles = listJsonFiles(localeDir);

    const onlyRef = refFiles.filter((f) => !localeFiles.includes(f));
    const onlyLocale = localeFiles.filter((f) => !refFiles.includes(f));

    if (onlyRef.length)
      errors.push({ kind: "missing_file", locale, files: onlyRef });
    if (onlyLocale.length)
      errors.push({
        kind: "extra_file",
        locale,
        files: onlyLocale,
      });
  }

  const allLocales = Object.keys(LOCALE_DIRS);
  let totalKeys = 0;

  for (const file of refFiles) {
    const refPath = path.join(LOCALE_DIRS[REFERENCE_LOCALE], file);
    const refObj = loadJson(refPath);
    const refKeys = collectKeys(refObj);
    totalKeys += refKeys.size;
    const ns = file.replace(/\.json$/, "");

    for (const [locale, localeDir] of Object.entries(LOCALE_DIRS)) {
      if (locale === REFERENCE_LOCALE) continue;
      const localePath = path.join(localeDir, file);
      if (!fs.existsSync(localePath)) continue; // already reported as missing_file

      const localeObj = loadJson(localePath);
      const localeKeys = collectKeys(localeObj);

      for (const [keyPath, typeRef] of refKeys) {
        if (!localeKeys.has(keyPath)) {
          errors.push({
            kind: "missing_key",
            namespace: ns,
            key: keyPath,
            locale,
          });
        } else {
          const typeLocale = localeKeys.get(keyPath);
          if (typeRef !== typeLocale) {
            errors.push({
              kind: "type_mismatch",
              namespace: ns,
              key: keyPath,
              ref: typeRef,
              locale,
              localeType: typeLocale,
            });
          }
        }
      }
      for (const keyPath of localeKeys.keys()) {
        if (!refKeys.has(keyPath)) {
          errors.push({
            kind: "extra_key",
            namespace: ns,
            key: keyPath,
            locale,
          });
        }
      }
    }
  }

  if (errors.length > 0) {
    console.log("--- 检查失败 ---\n");
    const missingFiles = errors.filter((e) => e.kind === "missing_file");
    const extraFiles = errors.filter((e) => e.kind === "extra_file");
    const missingKeys = errors.filter((e) => e.kind === "missing_key");
    const extraKeys = errors.filter((e) => e.kind === "extra_key");
    const typeMismatch = errors.filter((e) => e.kind === "type_mismatch");

    if (missingFiles.length) {
      console.log("缺失文件:");
      missingFiles.forEach((e) =>
        console.log(`  ${e.locale} 缺少: ${e.files.join(", ")}`),
      );
      console.log();
    }
    if (extraFiles.length) {
      console.log("多余文件:");
      extraFiles.forEach((e) =>
        console.log(`  ${e.locale} 多余: ${e.files.join(", ")}`),
      );
      console.log();
    }
    if (missingKeys.length) {
      console.log("缺失 key:");
      missingKeys.forEach((e) =>
        console.log(
          `  [${e.locale}] ${e.namespace}: ${e.key} (仅存在于 ${REFERENCE_LOCALE})`,
        ),
      );
      console.log();
    }
    if (extraKeys.length) {
      console.log("多余 key:");
      extraKeys.forEach((e) =>
        console.log(
          `  [${e.locale}] ${e.namespace}: ${e.key} (不存在于 ${REFERENCE_LOCALE})`,
        ),
      );
      console.log();
    }
    if (typeMismatch.length) {
      console.log("类型不一致:");
      typeMismatch.forEach((e) =>
        console.log(
          `  [${e.locale}] ${e.namespace}.${e.key}: ${REFERENCE_LOCALE}=${e.ref} ${e.locale}=${e.localeType}`,
        ),
      );
      console.log();
    }
    console.log("Summary: 失败");
    console.log(`  缺失文件: ${missingFiles.length}`);
    console.log(`  多余文件: ${extraFiles.length}`);
    console.log(`  缺失 key: ${missingKeys.length}`);
    console.log(`  多余 key: ${extraKeys.length}`);
    console.log(`  类型不一致: ${typeMismatch.length}`);
    process.exit(1);
  }

  console.log("--- 结构检查通过 ---");
  console.log(`  locale 数量: ${allLocales.length}`);
  console.log(`  namespace 数量: ${refFiles.length}`);
  console.log(`  总 key 数 (参考语言): ${totalKeys}`);

  const hardcoded = reportHardcodedCjk();
  const unused = reportPossiblyUnusedKeys(
    refFiles,
    LOCALE_DIRS[REFERENCE_LOCALE],
  );

  const softFail =
    (hardcoded.failed && STRICT_HARDCODED) ||
    (unused?.count > 0 && STRICT_UNUSED);
  if (softFail) {
    console.log("\nSummary: 软失败（严格模式开启）");
    process.exit(1);
  }
  console.log("\nSummary: 成功");
}

main();
