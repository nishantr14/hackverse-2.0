import { jsPDF } from 'jspdf';
import type {
  EmployeeRecommendation,
  SimulatorInput,
  SimulatorOutput,
  WorkforceRecommendationSet,
} from '../data/types';
import { formatWeekDelta } from './format';
import { bandVerdict, confidenceShape } from './simulator';
import { FIT_DIMENSION_SHORT_LABEL, FIT_DIMENSIONS, fitPoints } from './workforce';

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

/**
 * Typographic characters this document cannot render, and what to print
 * instead. See the file header for why: jsPDF's built-in fonts carry WinAnsi
 * and nothing else.
 */
const ASCII_FALLBACK: Record<string, string> = {
  '—': '-', // em dash; spacing is normalised below
  '–': '-', // en dash
  '−': '-', // true minus
  '‘': "'",
  '’': "'",
  '“': '"',
  '”': '"',
  '…': '...',
  '×': 'x',
  '₹': 'Rs.',
  '→': '->',
  '·': '-',
  '✓': '+', // check
  '⚠': '!', // warning
  '±': '+/-',
};

/**
 * Makes a backend string safe to print.
 *
 * EVERY STRING THAT CAME FROM THE API GOES THROUGH THIS. The literals in this
 * file were written ASCII-only by hand, but `rampUpNote`, `dataBasis.note`,
 * a candidate's reasons and an exclusion reason are all composed server-side
 * and none of them is under this file's control. `rampUpNote` has carried an
 * em dash since it was written.
 *
 * Accents are folded rather than replaced (NFD, then combining marks dropped),
 * so a name like Ramirez survives as Ramirez instead of Ram?rez. That covers
 * Latin scripts and nothing else.
 *
 * WHAT A NON-LATIN NAME ACTUALLY DOES, MEASURED RATHER THAN ASSUMED. Devanagari
 * and CJK names were put through the real export and the file was opened:
 *
 *     "आरती वेंकटेश"  ->  "???? ???????"
 *     "王小明"          ->  "???"
 *
 * One '?' per surviving code point. It DEGRADES, it does not throw and it does
 * not corrupt the file — the page renders, the layout holds, the row keeps its
 * employeeId, its score and its reasons, so the record stays traceable through
 * the workforce store even when the name is unreadable.
 *
 * That is an accepted limit for now, not a solved problem, and it is worse
 * than it looks on a page whose whole purpose is naming people: an unreadable
 * name in a staffing document is a person who cannot be identified by the
 * human who has to make the decision, and the length of the run of '?'
 * still leaks how long their name was. Embedding a Unicode font (jsPDF
 * `addFileToVFS` + `addFont` with a subset of Noto) is the fix when this data
 * stops being an ASCII-only seed.
 */
function ascii(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^\x20-\x7E\n]/g, (ch) => ASCII_FALLBACK[ch] ?? '?')
    // A substituted glyph often already had spaces around it, so a naive
    // replacement leaves "streams  -  the destination". Collapse afterwards
    // rather than guessing per-character.
    .replace(/ {2,}/g, ' ');
}

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
  /**
   * Present only when the scenario was run in NAMED mode.
   *
   * When it is present it is not optional to print it. A page carrying
   * people's names has to carry, on the same page, what the profiles behind
   * those names actually are (modelled, not submitted) and who could not be
   * named at all. On screen those sit a scroll away and the reader can go
   * find them; a PDF is read somewhere else entirely, by someone who cannot,
   * so the provenance travels with the names or the names do not go.
   */
  workforce?: WorkforceRecommendationSet | null;
  /** e.g. "Engineering Spend Intelligence" — printed small, top right. */
  productName?: string;
}

/** Builds the document without saving it — split out so it's testable headless. */
export function buildScenarioDoc({
  input,
  output,
  workforce = null,
  productName = 'Engineering Spend Intelligence',
}: ExportOptions) {
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
  /**
   * NEVER "±<confidencePercent>%".
   *
   * That field is a confidence LEVEL (`100 - spread`), and the width is
   * `confidenceHigh - confidenceLow`. Three screens conflated the two; this
   * page did not, and the wording below keeps it that way by naming both
   * quantities rather than leaving a bare parenthetical for a reader to
   * interpret. On paper there is nobody to ask what "(54%)" meant.
   */
  const confLine =
    `${verdictLabel} - P10-P90 ${output.confidenceLow}-${output.confidenceHigh}% ` +
    `(${trim(conf.spread, 1)} points wide)${
      output.confidencePercent !== undefined
        ? `, ${output.confidencePercent}% confidence`
        : ''
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
    // Sanitised: the backend composes this note and it has always contained
    // an em dash, which is not in this document's glyph set.
    const noteText = doc.splitTextToSize(
      ascii(output.rampUpNote ?? 'Limited experience in this component - adjustment applied.'),
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

  /**
   * The footer, and it is DIFFERENT in named mode.
   *
   * It read "No individual is named or scored anywhere in this product" on
   * every page. That was true when it was written and stopped being true the
   * day named mode shipped — and the worst place for it to be false is a
   * printed page that names four people directly above it. The claim is
   * per-mode now, the same way the sidebar's privacy note is per route.
   */
  const footer = (named: boolean) => {
    const footerY = 297 - MARGIN;
    rule(footerY - 12);
    doc.setFont('helvetica', 'italic');
    doc.setFontSize(9);
    setColor(INK);
    doc.text(
      named
        ? 'Recommendations, not decisions. Nobody is moved until they are asked and agree.'
        : 'Scenarios, not decisions. A human reviews every reallocation.',
      MARGIN,
      footerY - 6,
    );

    doc.setFont('helvetica', 'normal');
    doc.setFontSize(7.5);
    setColor(MUTED);
    doc.text(
      named
        ? 'Delivery figures from the event log, which is pseudonymised and carries no per-person measure. People are named only from a preference form and a resume, never joined to it.'
        : 'All figures computed from the event log. No individual is named or scored anywhere in this export.',
      MARGIN,
      footerY - 1,
      { maxWidth: CONTENT_W - 42 },
    );
    doc.text(
      `Generated ${new Date().toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })}`,
      PAGE_W - MARGIN,
      footerY - 1,
      { align: 'right' },
    );
  };

  /**
   * BOTH pages get the named footer when the document is a named one.
   *
   * The claim is about "this export", and a two-page export is one document:
   * page 1 saying nobody is named while page 2 lists four people is the same
   * false denial this footer was split to remove, just moved one page up. A
   * page can also be printed, cropped or forwarded on its own, so each has to
   * be true by itself.
   */
  const isNamed = Boolean(workforce);
  footer(isNamed);

  if (workforce) {
    doc.addPage();
    peoplePage(doc, workforce, { rule, setColor });
    footer(true);
  }

  return doc;
}

/**
 * Page two: who the engine proposed, and everything that qualifies it.
 *
 * ORDER IS THE ARGUMENT, and it is deliberately not the screen's. On screen
 * the names come first and the provenance badge sits beside them, because a
 * reader can see both at once. On paper the page is scanned top-down and may
 * be photographed, cropped or read over someone's shoulder, so what these
 * profiles ARE goes above the first name rather than beside it. A reader who
 * gets one paragraph in has already been told the profiles are modelled.
 */
function peoplePage(
  doc: jsPDF,
  set: WorkforceRecommendationSet,
  ctx: { rule: (y: number) => void; setColor: (rgb: readonly number[]) => void },
) {
  const { rule, setColor } = ctx;
  let y = MARGIN;

  doc.setFont('helvetica', 'normal');
  doc.setFontSize(9);
  setColor(MUTED);
  doc.text('SIMULATOR - RECOMMENDED PEOPLE', MARGIN, y);
  y += 10;

  doc.setFont('helvetica', 'bold');
  doc.setFontSize(17);
  setColor(INK);
  doc.text('Who the engine proposed', MARGIN, y);
  y += 6;

  doc.setFont('helvetica', 'normal');
  doc.setFontSize(10);
  setColor(MUTED);
  const req = set.requirement;
  doc.text(
    ascii(
      `${req.project} / ${req.component} - ${req.engineersRequired} engineer` +
        `${req.engineersRequired === 1 ? '' : 's'}. Ranked on ${req.requiredSkills.join(', ')}.`,
    ),
    MARGIN,
    y,
    { maxWidth: CONTENT_W },
  );
  y += 10;

  // --- the label, above the first name, impossible to crop off with one ----
  const basisText = doc.splitTextToSize(
    ascii(`${set.dataBasis.label}. ${set.dataBasis.note}`),
    CONTENT_W - 12,
  );
  const basisH = 8 + basisText.length * 4.4;
  doc.setDrawColor(AMBER[0], AMBER[1], AMBER[2]);
  doc.setFillColor(252, 246, 233);
  doc.roundedRect(MARGIN, y, CONTENT_W, basisH, 1.5, 1.5, 'FD');
  doc.setFont('helvetica', 'normal');
  doc.setFontSize(8.5);
  setColor(INK);
  doc.text(basisText, MARGIN + 6, y + 6);
  y += basisH + 6;

  // --- the consent gate, stated and counted -------------------------------
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(9);
  setColor(INK);
  doc.text('WHO CAN BE NAMED', MARGIN, y);
  y += 5;
  doc.setFont('helvetica', 'normal');
  doc.setFontSize(8.5);
  setColor(MUTED);
  const gate = doc.splitTextToSize(
    ascii(
      `${set.privacyBasis} ${set.anonymousCapacity.count} further ` +
        `profile${set.anonymousCapacity.count === 1 ? '' : 's'} could not be named here. ` +
        set.anonymousCapacity.note,
    ),
    CONTENT_W,
  );
  doc.text(gate, MARGIN, y);
  y += gate.length * 4.2 + 6;
  rule(y);
  y += 8;

  // --- the people ---------------------------------------------------------
  const person = (rec: EmployeeRecommendation, rank: number, alternate: boolean) => {
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(alternate ? 10 : 12);
    setColor(INK);
    doc.text(`${rank}. ${ascii(rec.name)}`, MARGIN, y);

    doc.setFont('helvetica', 'normal');
    doc.setFontSize(8.5);
    setColor(MUTED);
    doc.text(ascii(rec.employeeId), MARGIN + 58, y);

    doc.setFont('helvetica', 'bold');
    doc.setFontSize(alternate ? 10 : 12);
    setColor(TEAL);
    doc.text(`${rec.matchPercent}%`, PAGE_W - MARGIN, y, { align: 'right' });
    y += 5;

    /**
     * The five terms, rounded exactly as the card rounds them.
     *
     * `fitPoints` is largest-remainder, so these integers sum to the
     * percentage printed beside the name — and the sum is printed too. This
     * page asserts in words that the terms sum to the score; before, it
     * rounded each term to 1dp independently and printed 87.4 next to an 87%
     * headline, so the document disproved its own sentence. On paper nobody
     * can hover for the exact figures, which is why the total is spelled out
     * rather than left to be added up.
     */
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(8);
    setColor(MUTED);
    const points = fitPoints(rec);
    const terms = FIT_DIMENSIONS.map(
      (d) =>
        `${FIT_DIMENSION_SHORT_LABEL[d]} ${Math.round(rec.subScores[d] * 100)}%` +
        ` (+${points[d]} pts)`,
    ).join('   ');
    doc.text(terms, MARGIN, y, { maxWidth: CONTENT_W });
    y += 4.6;

    const total = FIT_DIMENSIONS.reduce((sum, d) => sum + points[d], 0);
    doc.text(`Sums to ${total}% - the score beside the name.`, MARGIN, y);
    y += 4.6;

    if (!alternate) {
      setColor(INK);
      doc.setFontSize(8.5);
      for (const line of [...rec.reasons.map((r) => `+ ${r}`), ...rec.flags.map((f) => `! ${f}`)]) {
        const wrapped = doc.splitTextToSize(ascii(line), CONTENT_W - 4);
        doc.text(wrapped, MARGIN + 2, y);
        y += wrapped.length * 4.1;
      }
    }
    y += 4;
  };

  doc.setFont('helvetica', 'bold');
  doc.setFontSize(9);
  setColor(INK);
  doc.text('RECOMMENDED', MARGIN, y);
  y += 6;
  set.recommendedEmployees.forEach((rec, i) => person(rec, i + 1, false));

  if (set.alternates.length) {
    y += 2;
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(9);
    setColor(INK);
    doc.text('ALTERNATES', MARGIN, y);
    y += 6;
    set.alternates.forEach((rec, i) =>
      person(rec, set.recommendedEmployees.length + i + 1, true),
    );
  }

  // --- excluded, which is not the same as ranked last ---------------------
  if (set.excluded.length) {
    rule(y);
    y += 7;
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(9);
    setColor(INK);
    doc.text('EXCLUDED ON A STATED BOUNDARY, NOT DOWN-RANKED', MARGIN, y);
    y += 5;
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(8.5);
    setColor(MUTED);
    for (const e of set.excluded) {
      const wrapped = doc.splitTextToSize(ascii(`${e.name} - ${e.reason}`), CONTENT_W);
      doc.text(wrapped, MARGIN, y);
      y += wrapped.length * 4.2;
    }
    y += 6;
  }

  // --- how the number was arrived at --------------------------------------
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(9);
  setColor(INK);
  doc.text('HOW THE FIT WAS SCORED', MARGIN, y);
  y += 5;
  doc.setFont('helvetica', 'normal');
  doc.setFontSize(8.5);
  setColor(MUTED);
  const method = doc.splitTextToSize(
    ascii(
      `${set.explanationMethod} No cycle time, throughput, review count or items ` +
        'merged is used, and nobody is ranked against a colleague on anything observed. ' +
        `${set.humanInTheLoop.note}`,
    ),
    CONTENT_W,
  );
  doc.text(method, MARGIN, y);
}

export function exportScenarioPdf(opts: ExportOptions) {
  const doc = buildScenarioDoc(opts);
  // The mode is in the filename: a named export is a different kind of
  // document and should be identifiable before anyone opens it.
  const mode = opts.workforce ? '-named' : '';
  const fileName = `scenario-${opts.input.sourceProject}-${opts.input.destProject}-${opts.input.engineerCount}eng${mode}`
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-');
  doc.save(`${fileName}.pdf`);
}
