import { jsPDF } from 'jspdf';
import type { SimulatorInput, SimulatorOutput } from '../data/types';
import { formatWeekDelta } from './format';
import { bandVerdict, confidenceShape } from './simulator';

/**
 * One-page PDF export of a run scenario result.
 *
 * Built with jsPDF's own vector text rather than screenshotting the DOM
 * (html2canvas or similar): the page has its own typography and layout
 * suited to print — the on-screen flood panels and animated figures are a
 * screen experience, not a document. A judge or a director should be able to
 * hand this single page to someone who never saw the app and have it stand
 * on its own.
 *
 * Every figure here is read directly off the same `SimulatorOutput` the
 * screen renders — nothing is recomputed, nothing is summarised by a model.
 * That is deliberate: the point of this export is to be exactly what was on
 * screen, in a form that can be attached to an email.
 *
 * NO NON-ASCII GLYPHS ANYWHERE IN THIS FILE. jsPDF's built-in fonts
 * (Helvetica/Times/Courier) only carry the WinAnsi (Windows-1252) glyph set —
 * no ₹, no →, no ▲, not even the − (U+2212) minus sign `formatMoney` uses on
 * screen. The first version of this export used all three and every one came
 * out as garbage in the actual downloaded file (confirmed by opening it, not
 * assumed) — `Delta !' Payments`, `¹8.2L`, `%² RAMP-UP`. Money, the arrow
 * between the two projects, and the ramp-up flag are built from ASCII text
 * and drawn vector shapes instead. Embedding a Unicode font was the other
 * option; skipped for now as more machinery than a one-page export needs.
 */

const PAGE_W = 210; // A4, mm
const MARGIN = 20;
const CONTENT_W = PAGE_W - MARGIN * 2;

const INK = [20, 22, 28] as const;
const MUTED = [110, 118, 132] as const;
const CORAL = [214, 84, 62] as const;
const TEAL = [24, 158, 140] as const;
const AMBER = [191, 128, 15] as const;
const LINE = [222, 225, 230] as const;

const LAKH = 100_000;
const CRORE = 10_000_000;
const inr = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

function trim(n: number, places: number): string {
  return n.toFixed(places).replace(/\.0+$/, '');
}

/** ASCII-only counterpart to lib/format's formatMoney — see file header. */
function money(rupees: number): string {
  const abs = Math.abs(rupees);
  if (abs >= CRORE) return `Rs.${trim(abs / CRORE, 2)}Cr`;
  if (abs >= LAKH) return `Rs.${trim(abs / LAKH, 1)}L`;
  return `Rs.${inr.format(abs)}`;
}

function moneyDelta(rupees: number): string {
  if (rupees === 0) return 'Rs.0';
  return `${rupees > 0 ? '+' : '-'}${money(Math.abs(rupees))}`;
}

function rupeesExact(rupees: number): string {
  return `Rs.${inr.format(rupees)}`;
}

interface ExportOptions {
  input: SimulatorInput;
  output: SimulatorOutput;
  /** e.g. "Engineering Spend Intelligence" — printed small, top right. */
  productName?: string;
}

/** Builds the document without saving it — split out so it's testable headless. */
export function buildScenarioDoc({ input, output, productName = 'Engineering Spend Intelligence' }: ExportOptions) {
  const doc = new jsPDF({ unit: 'mm', format: 'a4' });
  const costs = output.netCostRupees > 0;
  const tone = costs ? CORAL : TEAL;
  const conf = confidenceShape(output);
  let y = MARGIN;

  const setColor = (rgb: readonly number[]) => doc.setTextColor(rgb[0], rgb[1], rgb[2]);
  const rule = (yy: number) => {
    doc.setDrawColor(LINE[0], LINE[1], LINE[2]);
    doc.setLineWidth(0.2);
    doc.line(MARGIN, yy, PAGE_W - MARGIN, yy);
  };

  // Header
  doc.setFont('helvetica', 'normal');
  doc.setFontSize(9);
  setColor(MUTED);
  doc.text(productName.toUpperCase(), PAGE_W - MARGIN, y, { align: 'right' });
  doc.text('SIMULATOR - SCENARIO EXPORT', MARGIN, y);
  y += 10;

  // Title: "Source -> Dest", the arrow drawn as a vector, not a Unicode glyph.
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(20);
  setColor(INK);
  doc.text(input.sourceProject, MARGIN, y);
  const srcW = doc.getTextWidth(input.sourceProject);

  const arrowX1 = MARGIN + srcW + 5;
  const arrowLen = 9;
  const arrowY = y - 2.3;
  doc.setDrawColor(INK[0], INK[1], INK[2]);
  doc.setFillColor(INK[0], INK[1], INK[2]);
  doc.setLineWidth(0.7);
  doc.line(arrowX1, arrowY, arrowX1 + arrowLen, arrowY);
  doc.triangle(
    arrowX1 + arrowLen,
    arrowY - 1.4,
    arrowX1 + arrowLen,
    arrowY + 1.4,
    arrowX1 + arrowLen + 2.2,
    arrowY,
    'F',
  );
  doc.text(input.destProject, arrowX1 + arrowLen + 6, y);
  y += 7;

  doc.setFont('helvetica', 'normal');
  doc.setFontSize(11);
  setColor(MUTED);
  doc.text(
    `Moving ${input.engineerCount} engineer${input.engineerCount === 1 ? '' : 's'} out of ${input.sourceProject}, into ${input.destProject}.`,
    MARGIN,
    y,
  );
  y += 10;
  rule(y);
  y += 10;

  // The trade — two rows
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(10);
  setColor(INK);
  doc.text('THE TRADE', MARGIN, y);
  y += 8;

  const row = (label: string, value: string, rgb: readonly number[], detail: string) => {
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(12);
    setColor(INK);
    doc.text(label, MARGIN, y);

    doc.setFont('helvetica', 'bold');
    doc.setFontSize(14);
    setColor(rgb);
    doc.text(value, MARGIN + 68, y);

    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9.5);
    setColor(MUTED);
    doc.text(detail, PAGE_W - MARGIN, y, { align: 'right' });
    y += 8;
  };

  row(
    input.sourceProject,
    formatWeekDelta(output.sourceDeltaWeeks),
    output.sourceDeltaWeeks > 0 ? CORAL : TEAL,
    `${input.engineerCount} engineer${input.engineerCount === 1 ? '' : 's'} out`,
  );
  row(
    input.destProject,
    formatWeekDelta(output.destDeltaWeeks),
    output.destDeltaWeeks > 0 ? CORAL : TEAL,
    `${input.engineerCount} engineer${input.engineerCount === 1 ? '' : 's'} in`,
  );

  y += 4;
  rule(y);
  y += 12;

  // Net cost impact — the headline figure
  doc.setFont('helvetica', 'normal');
  doc.setFontSize(9);
  setColor(MUTED);
  doc.text('NET COST IMPACT', MARGIN, y);
  y += 11;

  doc.setFont('helvetica', 'bold');
  doc.setFontSize(30);
  setColor(tone);
  doc.text(moneyDelta(output.netCostRupees), MARGIN, y);
  y += 5;

  doc.setFont('helvetica', 'normal');
  doc.setFontSize(10.5);
  setColor(INK);
  const verdict = costs
    ? `The move costs more than it buys. ${input.sourceProject}'s slip outweighs ${input.destProject}'s gain.`
    : `The move pays for itself. ${input.destProject}'s gain outweighs ${input.sourceProject}'s slip.`;
  const verdictLines = doc.splitTextToSize(verdict, CONTENT_W - 10);
  doc.text(verdictLines, MARGIN, y);
  y += verdictLines.length * 5 + 6;

  doc.setFont('helvetica', 'normal');
  doc.setFontSize(8.5);
  setColor(MUTED);
  doc.text(`Exact figure: ${rupeesExact(Math.abs(output.netCostRupees))}`, MARGIN, y);
  y += 10;
  rule(y);
  y += 10;

  // Confidence
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(10);
  setColor(INK);
  doc.text('CONFIDENCE', MARGIN, y);
  y += 7;

  // Reads the width correctly, but was the third inline copy of these
  // thresholds. One function now, so a PDF and the screen it was exported
  // from cannot call the same band by two different names.
  const verdictLabel = bandVerdict(conf);

  doc.setFont('helvetica', 'normal');
  doc.setFontSize(11);
  setColor(INK);
  const confLine = `${verdictLabel} - P10-P90 ${output.confidenceLow}-${output.confidenceHigh}%${
    output.confidencePercent !== undefined ? ` (${output.confidencePercent}%)` : ''
  }`;
  doc.text(confLine, MARGIN, y);
  y += 7;

  // band visual — a simple horizontal bar, since the report has no interactivity
  const barW = CONTENT_W;
  const barH = 4;
  doc.setDrawColor(LINE[0], LINE[1], LINE[2]);
  doc.setFillColor(244, 245, 247);
  doc.rect(MARGIN, y, barW, barH, 'F');
  const lo = (output.confidenceLow / 100) * barW;
  const hi = (output.confidenceHigh / 100) * barW;
  doc.setFillColor(tone[0], tone[1], tone[2]);
  doc.rect(MARGIN + lo, y, hi - lo, barH, 'F');
  y += barH + 7;

  doc.setFont('helvetica', 'normal');
  doc.setFontSize(9.5);
  setColor(MUTED);
  const confNote = doc.splitTextToSize(
    'The band is the forecast. A wider band means the event log supports a wider range of outcomes for this move - it is not a margin of error on a single answer.',
    CONTENT_W,
  );
  doc.text(confNote, MARGIN, y);
  y += confNote.length * 4.6 + 8;

  // Ramp-up note — the warning triangle is drawn, not the ▲ character.
  if (output.rampUpPenaltyApplied) {
    doc.setDrawColor(AMBER[0], AMBER[1], AMBER[2]);
    doc.setFillColor(252, 246, 233);
    const noteText = doc.splitTextToSize(
      output.rampUpNote ?? 'Limited experience in this component - adjustment applied.',
      CONTENT_W - 16,
    );
    const boxH = 10 + noteText.length * 4.6;
    doc.roundedRect(MARGIN, y, CONTENT_W, boxH, 1.5, 1.5, 'FD');

    doc.setFillColor(AMBER[0], AMBER[1], AMBER[2]);
    doc.triangle(MARGIN + 6.5, y + 7.2, MARGIN + 9.5, y + 7.2, MARGIN + 8, y + 4, 'F');

    doc.setFont('helvetica', 'bold');
    doc.setFontSize(9.5);
    setColor(AMBER);
    doc.text('RAMP-UP ADJUSTMENT APPLIED', MARGIN + 13, y + 6.5);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    setColor(INK);
    doc.text(noteText, MARGIN + 6, y + 11.5);
    y += boxH + 8;
  }

  // Footer — pinned to the bottom of the page, not wherever content happens to end
  const footerY = 297 - MARGIN;
  rule(footerY - 12);
  doc.setFont('helvetica', 'italic');
  doc.setFontSize(9);
  setColor(INK);
  doc.text('Scenarios, not decisions. A human reviews every reallocation.', MARGIN, footerY - 6);

  doc.setFont('helvetica', 'normal');
  doc.setFontSize(7.5);
  setColor(MUTED);
  doc.text(
    'All figures computed from the event log. No individual is named or scored anywhere in this product.',
    MARGIN,
    footerY - 1,
  );
  doc.text(
    `Generated ${new Date().toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })}`,
    PAGE_W - MARGIN,
    footerY - 1,
    { align: 'right' },
  );

  return doc;
}

export function exportScenarioPdf(opts: ExportOptions) {
  const doc = buildScenarioDoc(opts);
  const fileName = `scenario-${opts.input.sourceProject}-${opts.input.destProject}-${opts.input.engineerCount}eng`
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-');
  doc.save(`${fileName}.pdf`);
}
