#!/usr/bin/env node
/**
 * Generic finance workbook authoring with the bundled @oai/artifact-tool.
 *
 * Run with the bundled Node executable after the root agent has successfully
 * run mark_artifact_operation_started.mjs. A node_modules symlink to the
 * bundled dependency directory must already exist beside this script or in
 * its parent directory. This script does not perform operation marking.
 *
 * Usage: node scripts/build_workbook.mjs /absolute/path/to/payload.json
 *
 * Required payload:
 *   {outputPath, previewDir, sheets:[{name,title,subtitle,headers,rows}]}
 * Cell values: string | finite number | boolean | null | {formula:"=..."}.
 * Dates must arrive as Excel serial numbers with a matching numberFormats entry.
 * All business formulas arrive in the payload; this builder does not infer any.
 *
 * Sheet options:
 *   columnWidths: {A:26,B:18} or [26,18] (Excel character units)
 *   numberFormats: {B:'#,##0.00;(#,##0.00);"-"'} (data cells only)
 *   cellFormats: {B22:"0.0%"} (overrides column-level number formats)
 *   freezeRows:4, freezeColumns:0, table:true, renderRange:"A1:L18"
 *   role:"output"|"build"|"input"|"source" (default "build")
 *   sourceColumns:["L"], textColumns:["A"], wrapColumns:["A"]
 *   editableCells:["B5"] (only explicitly marked numeric/blank inputs use blue)
 *   rowStyles:{"8":"subtotal","12":"total"} (actual Excel row numbers)
 *     Kinds: subtotal | total | section | source | note | ratio.
 *     Alias: rowStyle. Arrays [{row:8,kind:"subtotal"}] are also accepted.
 *   rowHeights:{"2":32,"5":26} (points); titleHeight:30; subtitleHeight:30
 *   headerHeight:30; bodyHeight:22
 *
 * Verification:
 *   verify:[{sheet,cell,expectedNumber,tolerance:1e-7}]
 *   Optional recalculateProbe:{
 *     mutations:[{sheet,cell,value}], verify:[{sheet,cell,expectedNumber,tolerance}]
 *   }
 *   The probe imports the final XLSX to a disposable in-memory workbook, changes
 *   inputs and recalculates without re-exporting or touching the final workbook.
 *
 * Writes one XLSX, one PNG per sheet, and previewDir/validation.json.
 * Logs never print payload values, formula text, inspect output, or exception
 * messages from the dependency. Validation results expose only differences.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const COLORS = Object.freeze({
  navy: "#19354A",
  teal: "#137C83",
  ink: "#172C3B",
  input: "#0000FF",
  formula: "#000000",
  link: "#008000",
  external: "#C00000",
  source: "#687785",
  pale: "#EDF5F5",
  light: "#F4F7FA",
  line: "#CBD5DF",
  white: "#FFFFFF",
});
const FONT = "Arial";
const ACCOUNTING = '#,##0.00;(#,##0.00);"-"';
const INTEGER = '#,##0;(#,##0);"-"';
const ERROR_SOURCE = "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!|#GETTING_DATA|#CONNECT!|#BLOCKED!|#UNKNOWN!";
const EXACT_ERROR = new RegExp(`^(?:${ERROR_SOURCE})$`, "i");
const MAX_ROWS = 1048576;
const MAX_COLS = 16384;
const STYLE_KINDS = new Set(["subtotal", "total", "section", "source", "note", "ratio"]);
let stage = "arguments";
let context = {};
let validationPath;
let report;

class BuildError extends Error {
  constructor(code, location = {}) {
    super(code);
    this.name = "BuildError";
    this.safeCode = code;
    this.location = location;
  }
}

function assert(condition, code, location) {
  if (!condition) throw new BuildError(code, location);
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isFormula(value) {
  return isObject(value) && typeof value.formula === "string";
}

function isScalar(value) {
  return value === null || typeof value === "string" || typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value));
}

function columnName(index) {
  let result = "";
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) {
    result = String.fromCharCode(65 + (n - 1) % 26) + result;
  }
  return result;
}

function columnIndex(name) {
  if (typeof name !== "string" || !/^[A-Z]{1,3}$/.test(name)) return -1;
  let result = 0;
  for (const char of name) result = result * 26 + char.charCodeAt(0) - 64;
  return result - 1;
}

function parseCell(cell) {
  const match = typeof cell === "string" && cell.match(/^([A-Z]{1,3})([1-9]\d*)$/);
  assert(match && columnIndex(match[1]) < MAX_COLS && Number(match[2]) <= MAX_ROWS,
    "invalid_cell_address");
  return { column: columnIndex(match[1]), row: Number(match[2]) };
}

function validateRange(range) {
  const parts = typeof range === "string" ? range.split(":") : [];
  assert(parts.length >= 1 && parts.length <= 2, "invalid_render_range");
  const first = parseCell(parts[0]);
  const last = parseCell(parts.at(-1));
  assert(first.row <= last.row && first.column <= last.column, "reversed_render_range");
}

function literal(value) {
  // Preserve literal strings beginning with '=' rather than allowing accidental
  // formula evaluation. Only explicit {formula:...} cells are formulas.
  return typeof value === "string" && value.startsWith("=") ? `'${value}` : value;
}

function safeFilename(name, index) {
  return `${String(index + 1).padStart(2, "0")}-${name.replace(/[\\/:*?"<>|\x00-\x1F]/g, "_")}.png`;
}

function styleEntries(spec) {
  const styles = spec.rowStyles ?? spec.rowStyle ?? {};
  return Array.isArray(styles)
    ? styles.map(({ row, kind }) => [String(row), kind])
    : Object.entries(styles);
}

function mapColumns(value, defaultValue, count) {
  if (value === undefined) return Array.from({ length: count }, () => defaultValue);
  if (Array.isArray(value)) return Array.from({ length: count }, (_, i) => value[i] ?? defaultValue);
  return Array.from({ length: count }, (_, i) => value[columnName(i)] ?? defaultValue);
}

function validateChecks(checks, names) {
  assert(Array.isArray(checks), "verify_must_be_array");
  for (const check of checks) {
    assert(isObject(check) && names.has(check.sheet), "verify_sheet_not_found");
    parseCell(check.cell);
    assert(typeof check.expectedNumber === "number" && Number.isFinite(check.expectedNumber),
      "verify_expected_number_required", { sheet: check.sheet, cell: check.cell });
    assert(check.tolerance === undefined ||
      (typeof check.tolerance === "number" && Number.isFinite(check.tolerance) && check.tolerance >= 0),
    "verify_tolerance_invalid", { sheet: check.sheet, cell: check.cell });
  }
}

function validatePayload(payload) {
  assert(isObject(payload), "payload_must_be_object");
  assert(typeof payload.outputPath === "string" && path.extname(payload.outputPath).toLowerCase() === ".xlsx",
    "output_path_must_be_xlsx");
  assert(typeof payload.previewDir === "string" && payload.previewDir.length > 0, "preview_dir_required");
  assert(Array.isArray(payload.sheets) && payload.sheets.length > 0, "sheets_required");
  const names = new Set();
  const normalizedNames = new Set();
  for (const spec of payload.sheets) {
    assert(isObject(spec) && typeof spec.name === "string" && spec.name.length >= 1 && spec.name.length <= 31 &&
      !/[\\/?*\[\]:\x00-\x1F]/.test(spec.name) && !/^'|'$/.test(spec.name), "invalid_sheet_name");
    assert(!normalizedNames.has(spec.name.toLocaleLowerCase("en-US")), "duplicate_sheet_name");
    names.add(spec.name);
    normalizedNames.add(spec.name.toLocaleLowerCase("en-US"));
    const location = { sheet: spec.name };
    assert(typeof spec.title === "string" && typeof (spec.subtitle ?? "") === "string", "invalid_titles", location);
    assert(Array.isArray(spec.headers) && spec.headers.length > 0 && spec.headers.length <= MAX_COLS &&
      spec.headers.every((header) => typeof header === "string" && header.length > 0), "invalid_headers", location);
    assert(Array.isArray(spec.rows) && spec.rows.length <= MAX_ROWS - 4, "invalid_rows", location);
    const width = spec.headers.length;
    for (let r = 0; r < spec.rows.length; r++) {
      const row = spec.rows[r];
      assert(Array.isArray(row) && row.length === width, "row_width_mismatch", { ...location, row: r + 5 });
      for (let c = 0; c < width; c++) {
        const value = row[c];
        assert(isScalar(value) || (isFormula(value) && value.formula.startsWith("=") && value.formula.length > 1),
          "invalid_cell_value", { ...location, cell: `${columnName(c)}${r + 5}` });
      }
    }
    if (spec.table !== undefined) assert(typeof spec.table === "boolean", "invalid_table_flag", location);
    if (spec.table !== false) {
      assert(new Set(spec.headers.map((header) => header.toLocaleLowerCase("en-US"))).size === width,
        "duplicate_table_headers", location);
    }
    assert(spec.role === undefined || ["output", "build", "input", "source"].includes(spec.role), "invalid_sheet_role", location);
    if (spec.columnWidths !== undefined) {
      assert(Array.isArray(spec.columnWidths) || isObject(spec.columnWidths), "invalid_column_widths", location);
      for (const value of Object.values(spec.columnWidths)) {
        assert(typeof value === "number" && Number.isFinite(value) && value > 0 && value <= 255,
          "invalid_column_width", location);
      }
    }
    if (spec.numberFormats !== undefined) {
      assert(isObject(spec.numberFormats), "invalid_number_formats", location);
      for (const [key, format] of Object.entries(spec.numberFormats)) {
        assert(columnIndex(key) >= 0 && columnIndex(key) < width && typeof format === "string",
          "invalid_number_format_column", location);
      }
    }
    if (spec.cellFormats !== undefined) {
      assert(isObject(spec.cellFormats), "invalid_cell_formats", location);
      for (const [cell, format] of Object.entries(spec.cellFormats)) {
        const address = parseCell(cell);
        assert(address.column < width && address.row <= spec.rows.length + 4 &&
          typeof format === "string" && format.length > 0,
        "invalid_cell_format", { ...location, cell });
      }
    }
    if (spec.editableCells !== undefined) {
      assert(Array.isArray(spec.editableCells), "invalid_editable_cells", location);
      for (const cell of spec.editableCells) {
        const address = parseCell(cell);
        assert(address.column < width && address.row >= 5 && address.row <= spec.rows.length + 4,
          "editable_cell_outside_data", { ...location, cell });
        assert(!isFormula(spec.rows[address.row - 5][address.column]),
          "editable_cell_cannot_be_formula", { ...location, cell });
      }
    }
    for (const key of ["sourceColumns", "textColumns", "wrapColumns"]) {
      assert(spec[key] === undefined || (Array.isArray(spec[key]) && spec[key].every((column) =>
        columnIndex(column) >= 0 && columnIndex(column) < width)), "invalid_style_columns", location);
    }
    assert(Number.isInteger(spec.freezeRows ?? 4) && (spec.freezeRows ?? 4) >= 0 && (spec.freezeRows ?? 4) <= MAX_ROWS,
      "invalid_freeze_rows", location);
    assert(Number.isInteger(spec.freezeColumns ?? 0) && (spec.freezeColumns ?? 0) >= 0 && (spec.freezeColumns ?? 0) <= width,
      "invalid_freeze_columns", location);
    for (const [row, kind] of styleEntries(spec)) {
      assert(/^\d+$/.test(row) && Number(row) >= 5 && Number(row) <= spec.rows.length + 4 && STYLE_KINDS.has(kind),
        "invalid_row_style", location);
    }
    for (const key of ["titleHeight", "subtitleHeight", "headerHeight", "bodyHeight"]) {
      assert(spec[key] === undefined || (typeof spec[key] === "number" && Number.isFinite(spec[key]) && spec[key] > 0),
        "invalid_row_height", location);
    }
    for (const [row, height] of Object.entries(spec.rowHeights ?? {})) {
      assert(/^\d+$/.test(row) && Number(row) >= 1 && Number(row) <= spec.rows.length + 4 &&
        typeof height === "number" && Number.isFinite(height) && height > 0, "invalid_row_height", location);
    }
    if (spec.renderRange !== undefined) validateRange(spec.renderRange);
  }
  validateChecks(payload.verify ?? [], names);
  if (payload.recalculateProbe !== undefined) {
    const probe = payload.recalculateProbe;
    assert(isObject(probe) && Array.isArray(probe.mutations) && probe.mutations.length > 0,
      "invalid_recalculate_probe");
    validateChecks(probe.verify, names);
    assert(probe.verify.length > 0, "probe_verification_required");
    for (const mutation of probe.mutations) {
      assert(isObject(mutation) && names.has(mutation.sheet), "probe_sheet_not_found");
      parseCell(mutation.cell);
      assert(isScalar(mutation.value), "probe_requires_scalar_input");
    }
  }
}

// Runs are grouped by column to avoid a remote write/format operation per cell.
function forColumnRuns(spec, classify, apply) {
  for (let c = 0; c < spec.headers.length; c++) {
    let start = 0;
    while (start < spec.rows.length) {
      const category = classify(spec.rows[start][c]);
      let end = start + 1;
      while (end < spec.rows.length && classify(spec.rows[end][c]) === category) end++;
      if (category !== null) apply(category, c, start, end);
      start = end;
    }
  }
}

function runRange(sheet, c, start, end) {
  return sheet.getRange(`${columnName(c)}${start + 5}:${columnName(c)}${end + 4}`);
}

function formulaColor(formula, role) {
  if (role === "output") return COLORS.ink;
  // Ignore quoted string literals before identifying sheet or workbook links.
  const expression = formula.replace(/"(?:[^"]|"")*"/g, '""');
  if (/\[[^\]]+\][^!]*!/.test(expression)) return COLORS.external;
  return expression.includes("!") ? COLORS.link : COLORS.formula;
}

function fillSheet(sheet, spec, index) {
  const width = spec.headers.length;
  const lastColumn = columnName(width - 1);
  // Keep the heading within the visible leading columns of wide schedules.
  const previewEnd = spec.renderRange ? parseCell(spec.renderRange.split(":").at(-1)).column + 1 : 8;
  const titleLastColumn = columnName(Math.min(width, previewEnd, 8) - 1);
  const lastRow = spec.rows.length + 4;
  const used = sheet.getRange(`A1:${lastColumn}${lastRow}`);
  sheet.showGridLines = false;
  sheet.tabColor = spec.role === "output" ? COLORS.navy : spec.role === "source" ? "#C8B99B" : COLORS.teal;
  used.format = {
    fill: COLORS.white,
    font: { name: FONT, size: 10, color: COLORS.ink },
    verticalAlignment: "center",
    horizontalAlignment: "left",
    rowHeight: spec.bodyHeight ?? 22,
    wrapText: false,
  };
  const widths = mapColumns(spec.columnWidths, 18, width);
  for (let c = 0; c < width; c++) {
    sheet.getRange(`${columnName(c)}1:${columnName(c)}${lastRow}`).format.columnWidth = widths[c];
  }
  if (width > 1) {
    sheet.mergeCells(`A1:${titleLastColumn}1`);
    sheet.mergeCells(`A2:${titleLastColumn}2`);
  }
  sheet.getRange("A1").values = [[literal(spec.title)]];
  sheet.getRange("A2").values = [[literal(spec.subtitle ?? "")]];
  sheet.getRange(`A1:${lastColumn}1`).format = {
    font: { name: FONT, size: 16, bold: true, color: COLORS.navy },
    rowHeight: spec.titleHeight ?? 30,
    verticalAlignment: "center",
  };
  sheet.getRange(`A2:${lastColumn}2`).format = {
    font: { name: FONT, size: 10, italic: true, color: COLORS.source },
    rowHeight: spec.subtitleHeight ?? 42,
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.getRange(`A3:${lastColumn}3`).format.rowHeight = 9;
  sheet.getRange(`A3:${lastColumn}3`).format.borders = {
    bottom: { style: "thin", color: COLORS.teal },
  };
  sheet.getRange(`A4:${lastColumn}4`).values = [spec.headers.map(literal)];
  if (spec.rows.length > 0) {
    sheet.getRange(`A5:${lastColumn}${lastRow}`).values = spec.rows.map((row) =>
      row.map((value) => isFormula(value) ? null : literal(value)));
    forColumnRuns(spec, (value) => isFormula(value) ? "formula" : null, (_, c, start, end) => {
      runRange(sheet, c, start, end).formulas = spec.rows.slice(start, end).map((row) => [row[c].formula]);
    });
  }
  if (spec.table !== false && spec.rows.length > 0) {
    const table = sheet.tables.add(`A4:${lastColumn}${lastRow}`, true, `FinanceTable${index + 1}`);
    table.showFilterButton = true;
    table.showTotals = false;
    table.showBandedColumns = false;
    // Explicit body fills keep the workbook palette independent of table style.
    sheet.getRange(`A5:${lastColumn}${lastRow}`).format.fill = COLORS.white;
  }
  const header = sheet.getRange(`A4:${lastColumn}4`);
  header.format = {
    fill: COLORS.navy,
    font: { name: FONT, size: 10, bold: true, color: COLORS.white },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    rowHeight: spec.headerHeight ?? 30,
    borders: {
      bottom: { style: "thin", color: COLORS.teal },
      insideVertical: { style: "thin", color: COLORS.white },
    },
  };
  if (spec.rows.length > 0) {
    forColumnRuns(spec, (value) => {
      if (isFormula(value)) return `formula:${formulaColor(value.formula, spec.role)}`;
      if (typeof value === "number") return Number.isInteger(value) ? "integer" : "number";
      if (typeof value === "boolean") return "boolean";
      return null;
    }, (category, c, start, end) => {
      const range = runRange(sheet, c, start, end);
      range.format.horizontalAlignment = category === "boolean" ? "center" : "right";
      if (category === "integer" || category === "number") {
        range.format.font.color = COLORS.ink;
        range.setNumberFormat(category === "integer" ? INTEGER : ACCOUNTING);
      } else if (category.startsWith("formula:")) {
        range.format.font.color = category.slice(8);
        range.setNumberFormat(ACCOUNTING);
      }
    });
    for (const [column, format] of Object.entries(spec.numberFormats ?? {})) {
      sheet.getRange(`${column}5:${column}${lastRow}`).setNumberFormat(format);
    }
    for (const column of spec.textColumns ?? []) {
      sheet.getRange(`${column}5:${column}${lastRow}`).format.horizontalAlignment = "left";
    }
    for (const column of spec.wrapColumns ?? []) {
      const range = sheet.getRange(`${column}5:${column}${lastRow}`);
      range.format.wrapText = true;
      range.format.verticalAlignment = "top";
    }
    for (const column of spec.sourceColumns ?? []) {
      const range = sheet.getRange(`${column}5:${column}${lastRow}`);
      range.format.font.color = COLORS.source;
      range.format.horizontalAlignment = "left";
    }
    for (const [row, kind] of styleEntries(spec)) {
      const range = sheet.getRange(`A${row}:${lastColumn}${row}`);
      if (kind === "subtotal" || kind === "total") {
        range.format.font.bold = true;
        range.format.fill = kind === "total" ? COLORS.pale : COLORS.light;
        range.format.borders = { top: { style: kind === "total" ? "medium" : "thin", color: COLORS.line } };
      } else if (kind === "section") {
        range.format.fill = COLORS.pale;
        range.format.font.bold = true;
      } else if (kind === "source" || kind === "note") {
        range.format.font.color = COLORS.source;
        range.format.font.italic = true;
      } else if (kind === "ratio") {
        range.format.font.italic = true;
      }
    }
    for (const cell of spec.editableCells ?? []) {
      const { row, column } = parseCell(cell);
      const value = spec.rows[row - 5][column];
      // Explicitly marked blanks retain the input color for a future numeric
      // entry. Imported numbers and captured outputs remain ordinary dark text.
      if (typeof value === "number" || value === null) {
        sheet.getRange(cell).format.font.color = COLORS.input;
      }
    }
  }
  // Cell-level formats override columns, including on a header-only sheet.
  for (const [cell, format] of Object.entries(spec.cellFormats ?? {})) {
    sheet.getRange(cell).setNumberFormat(format);
  }
  for (const [row, height] of Object.entries(spec.rowHeights ?? {})) {
    sheet.getRange(`A${row}:${lastColumn}${row}`).format.rowHeight = height;
  }
  for (const range of spec.mergedNoteRanges ?? []) {
    sheet.mergeCells(range);
    sheet.getRange(range).format.wrapText = true;
  }
  if ((spec.freezeRows ?? 4) > 0) sheet.freezePanes.freezeRows(spec.freezeRows ?? 4);
  if ((spec.freezeColumns ?? 0) > 0) sheet.freezePanes.freezeColumns(spec.freezeColumns);
}

async function verifyCells(workbook, checks) {
  const results = [];
  for (const check of checks) {
    context = { sheet: check.sheet, range: check.cell };
    // Inspect is intentionally retained in memory; financial values and formula
    // text from the returned NDJSON must not be written to logs or support files.
    await workbook.inspect({
      kind: "table", sheetId: check.sheet, range: check.cell,
      include: "values,formulas", tableMaxRows: 1, tableMaxCols: 1, maxChars: 2000,
    });
    const actual = workbook.worksheets.getItem(check.sheet).getRange(check.cell).values[0]?.[0];
    const tolerance = check.tolerance ?? 1e-7;
    const isNumber = typeof actual === "number" && Number.isFinite(actual);
    const difference = isNumber ? actual - check.expectedNumber : null;
    const passed = isNumber && Math.abs(difference) <= tolerance;
    results.push({
      sheet: check.sheet, cell: check.cell, passed,
      actualType: actual === null ? "null" : typeof actual,
      difference, tolerance,
    });
  }
  return results;
}

async function scanErrors(workbook, sheets) {
  context = {};
  await workbook.inspect({
    kind: "match", searchTerm: ERROR_SOURCE,
    options: { useRegex: true, maxResults: 300 },
    maxChars: 3000, summary: "final formula error scan",
  });
  // Do not infer counts from undocumented inspect response internals. Use the
  // documented value getter to check every populated cell independently.
  let errorCount = 0;
  const errors = [];
  for (const spec of sheets) {
    const lastColumn = columnName(spec.headers.length - 1);
    const lastRow = spec.rows.length + 4;
    context = { sheet: spec.name, range: `A1:${lastColumn}${lastRow}` };
    // Chunk reads keep large source sheets from adding a second full copy.
    for (let firstRow = 1; firstRow <= lastRow; firstRow += 1000) {
      const endRow = Math.min(lastRow, firstRow + 999);
      const values = workbook.worksheets.getItem(spec.name).getRange(`A${firstRow}:${lastColumn}${endRow}`).values;
      for (let r = 0; r < values.length; r++) {
        for (let c = 0; c < (values[r]?.length ?? 0); c++) {
          const value = values[r][c];
          if (typeof value === "string" && EXACT_ERROR.test(value)) {
            errorCount++;
            if (errors.length < 300) errors.push({ sheet: spec.name, cell: `${columnName(c)}${firstRow + r}`, error: value });
          }
        }
      }
    }
  }
  return { inspected: true, errorCount, errors, truncated: errorCount > errors.length };
}

async function saveReport() {
  await fs.writeFile(validationPath, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
}

async function main() {
  assert(process.argv.length === 3, "usage_node_build_workbook_payload_json");
  const payloadPath = path.resolve(process.argv[2]);
  stage = "read_payload";
  const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
  stage = "validate_payload";
  validatePayload(payload);
  const outputPath = path.resolve(payload.outputPath);
  const previewDir = path.resolve(payload.previewDir);
  assert(outputPath !== payloadPath, "input_output_path_conflict");
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.mkdir(previewDir, { recursive: true });
  validationPath = path.join(previewDir, "validation.json");
  report = {
    status: "building", workbook: path.basename(outputPath),
    engine: "@oai/artifact-tool", checks: [], errorScan: null,
    previews: [], recalculationProbe: null, exported: false,
  };
  stage = payload.inputPath ? "import_workbook" : "create_workbook";
  const workbook = payload.inputPath
    ? await SpreadsheetFile.importXlsx(await FileBlob.load(path.resolve(payload.inputPath)))
    : Workbook.create();
  const existingNames = new Set(payload.inputPath ? payload.inputSheetNames : []);
  const rewriteNames = new Set(payload.rewriteSheets ?? []);
  // Every named worksheet must exist before cross-sheet formulas are assigned.
  for (const spec of payload.sheets) if (!existingNames.has(spec.name)) workbook.worksheets.add(spec.name);
  for (let i = 0; i < payload.sheets.length; i++) {
    const spec = payload.sheets[i];
    stage = "populate_and_style";
    context = { sheet: spec.name, range: `A1:${columnName(spec.headers.length - 1)}${spec.rows.length + 4}` };
    if (rewriteNames.has(spec.name)) {
      const sheet = workbook.worksheets.getItem(spec.name);
      const used = sheet.getUsedRange();
      used.unmerge();
      used.clear({ applyTo: "all" });
      sheet.freezePanes.unfreeze();
      fillSheet(sheet, spec, i);
    } else if (!existingNames.has(spec.name)) fillSheet(workbook.worksheets.getItem(spec.name), spec, i);
  }
  for (const control of payload.caseSelectors ?? []) {
    const sheet = workbook.worksheets.getItem(control.sheet);
    sheet.dataValidations.add({ range: control.cell, rule: {
      type: "whole", operator: "between", formula1: 1, formula2: 2,
    } });
  }
  for (const spec of payload.sheets) {
    for (const cell of spec.caseLabelCells ?? []) {
      const range = workbook.worksheets.getItem(spec.name).getRange(cell);
      range.format.font.color = COLORS.link;
      range.format.borders = { preset: "outside", style: "dashed", color: COLORS.line };
    }
  }
  if (payload.inputPath) {
    // Patch only requested values and formatting. Imported sheets retain their
    // formulas, inputs, row order, panes and formatting outside these ranges.
    console.log(JSON.stringify({ event: "applying_value_patches" }));
    const plainColumns = new Map();
    const singlePatches = [];
    for (const patch of payload.cellPatches ?? []) {
      const address = parseCell(patch.cell);
      const spec = payload.sheets.find(s => s.name === patch.sheet);
      const columnHasFormulas = spec.rows.some(row => isFormula(row[address.column]));
      if (address.row < 5 || isFormula(patch.value) || columnHasFormulas) {
        singlePatches.push(patch);
        continue;
      }
      const key = `${patch.sheet}\u0000${address.column}`;
      if (!plainColumns.has(key)) plainColumns.set(key, []);
      plainColumns.get(key).push({ ...patch, row: address.row, column: address.column });
    }
    for (const patches of plainColumns.values()) {
      const first = Math.min(...patches.map(p => p.row));
      const last = Math.max(...patches.map(p => p.row));
      const letter = columnName(patches[0].column);
      const range = workbook.worksheets.getItem(patches[0].sheet).getRange(`${letter}${first}:${letter}${last}`);
      const values = range.values;
      for (const patch of patches) values[patch.row - first] = [literal(patch.value)];
      range.values = values;
    }
    for (const patch of singlePatches) {
      const range = workbook.worksheets.getItem(patch.sheet).getRange(patch.cell);
      if (isFormula(patch.value)) range.formulas = [[patch.value.formula]];
      else range.values = [[literal(patch.value)]];
    }
    for (const patch of payload.tablePatches ?? []) {
      const sheet = workbook.worksheets.getItem(patch.sheet);
      const current = sheet.tables.items.find(t => t.name === patch.name);
      assert(current, "patch_table_not_found", { sheet: patch.sheet });
      current.delete();
      const table = sheet.tables.add(patch.range, true, patch.name);
      table.showFilterButton = true;
      table.showTotals = false;
      table.showBandedColumns = false;
    }
    for (const patch of payload.mergePatches ?? []) {
      workbook.worksheets.getItem(patch.sheet).mergeCells(patch.range);
    }
    console.log(JSON.stringify({ event: "applying_format_patches" }));
    for (const patch of payload.formatPatches ?? []) {
      const range = workbook.worksheets.getItem(patch.sheet).getRange(patch.range);
      range.format = patch.format;
      if (patch.numberFormat) range.setNumberFormat(patch.numberFormat);
    }
    // Imported Excel shared-formula caches do not reliably invalidate after
    // category/source edits. Reassign the same reviewed formulas in blocks so
    // every affected summary is recalculated from current source values.
    for (const spec of payload.sheets) {
      if (!existingNames.has(spec.name)) continue;
      const sheet = workbook.worksheets.getItem(spec.name);
      forColumnRuns(spec, value => isFormula(value) ? "formula" : null, (_, c, start, end) => {
        runRange(sheet, c, start, end).formulas = spec.rows.slice(start, end).map(row => [row[c].formula]);
      });
    }
  }
  stage = "recalculate";
  console.log(JSON.stringify({ event: "recalculating" }));
  context = {};
  workbook.recalculate();
  stage = "verify_cells";
  report.checks = await verifyCells(workbook, payload.verify ?? []);
  stage = "scan_errors";
  report.errorScan = await scanErrors(workbook, payload.sheets);
  report.status = report.checks.every((check) => check.passed) && report.errorScan.errorCount === 0 ? "verified" : "failed";
  await saveReport();
  for (let i = 0; i < payload.sheets.length; i++) {
    const spec = payload.sheets[i];
    if (payload.renderSheets && !payload.renderSheets.includes(spec.name)) continue;
    const range = spec.renderRange ?? `A1:${columnName(Math.min(spec.headers.length, 12) - 1)}${Math.min(spec.rows.length + 4, 18)}`;
    stage = "render";
    context = { sheet: spec.name, range };
    const preview = await workbook.render({ sheetName: spec.name, range, scale: 1.5, format: "png" });
    const filename = safeFilename(spec.name, i);
    await fs.writeFile(path.join(previewDir, filename), new Uint8Array(await preview.arrayBuffer()), { mode: 0o600 });
    report.previews.push({ sheet: spec.name, range, file: filename });
    console.log(JSON.stringify({ event: "rendered", file: filename, range }));
    for (const extraRange of spec.extraRenderRanges ?? []) {
      validateRange(extraRange);
      context = { sheet: spec.name, range: extraRange };
      const extraPreview = await workbook.render({ sheetName: spec.name, range: extraRange, scale: 1.5, format: "png" });
      const extraFilename = filename.replace(/\.png$/, `-${extraRange.replace(":", "-")}.png`);
      await fs.writeFile(path.join(previewDir, extraFilename), new Uint8Array(await extraPreview.arrayBuffer()), { mode: 0o600 });
      report.previews.push({ sheet: spec.name, range: extraRange, file: extraFilename });
      console.log(JSON.stringify({ event: "rendered", file: extraFilename, range: extraRange }));
    }
  }
  await saveReport();
  assert(report.status === "verified", "workbook_verification_failed");
  stage = "export";
  context = {};
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);
  await fs.chmod(outputPath, 0o600);
  report.exported = true;
  if (payload.recalculateProbe) {
    stage = "disposable_recalculation_probe";
    const disposable = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
    for (const mutation of payload.recalculateProbe.mutations) {
      context = { sheet: mutation.sheet, range: mutation.cell };
      disposable.worksheets.getItem(mutation.sheet).getRange(mutation.cell).values = [[literal(mutation.value)]];
    }
    disposable.recalculate();
    const checks = await verifyCells(disposable, payload.recalculateProbe.verify);
    report.recalculationProbe = {
      engine: "@oai/artifact-tool", finalWorkbookUnmodified: true,
      passed: checks.every((check) => check.passed), checks,
    };
    await saveReport();
    assert(report.recalculationProbe.passed, "recalculation_probe_failed");
  }
  report.status = "passed";
  await saveReport();
  console.log(JSON.stringify({
    event: "completed", file: path.basename(outputPath),
    validation: path.basename(validationPath),
    checks: report.checks.map(({ sheet, cell, passed, difference }) => ({ sheet, cell, passed, difference })),
    formulaErrors: report.errorScan.errorCount,
    recalculationProbePassed: report.recalculationProbe?.passed ?? null,
  }));
}

try {
  await main();
} catch (error) {
  const failure = {
    event: "failed", stage, ...context,
    code: error instanceof BuildError ? error.safeCode : "operation_failed",
    ...(error instanceof BuildError ? error.location : {}),
    errorType: error?.name ?? "Error",
  };
  if (report && validationPath) {
    report.status = "failed";
    report.failure = failure;
    try { await saveReport(); } catch { /* Keep console output free of sensitive exception messages. */ }
  }
  console.error(JSON.stringify(failure));
  process.exitCode = 1;
}
