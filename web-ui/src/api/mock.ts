// In-memory LibraryApi with realistic sample data (VITE_API_MOCK=1). Titles, authors and DOIs are
// invented; DOIs use the 10.5555 test prefix. Mock modes via the page URL: ?mock=error (shelf fails),
// ?mock=slow (3 s latency).
import { ApiError, newKey, type LibraryApi } from "./api";
import type {
  Accepted,
  Candidate,
  Citation,
  CitationDetail,
  CitationStatus,
  Collection,
  CollectionConsequences,
  Intent,
  JobStatus,
  PaperCard,
  PaperDetail,
  PendingFile,
  Permissions,
  ReviewIn,
  ShelfPage,
  ShelfQuery,
  StageCounts,
} from "./types";

const mode = new URLSearchParams(globalThis.location?.search ?? "").get("mock");
const latency = () => new Promise((r) => setTimeout(r, mode === "slow" ? 3000 : 180 + Math.random() * 220));
const au = (...names: string[]) => names.map((display) => ({ display }));

// ---------- tiny PDF so the viewer has something to show ----------
function makePdf(title: string, lines: string[]): string {
  const esc = (s: string) => s.replace(/[\\()]/g, "\\$&");
  const text = [
    "BT /F1 20 Tf 60 740 Td 24 TL",
    `(${esc(title.slice(0, 60))}) Tj T*`,
    "/F1 11 Tf 16 TL",
    ...lines.map((l) => `T* (${esc(l)}) Tj`),
    "ET",
  ].join("\n");
  const objs = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    `<< /Length ${text.length} >>\nstream\n${text}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let out = "%PDF-1.4\n";
  const offsets: number[] = [];
  objs.forEach((o, i) => {
    offsets.push(out.length);
    out += `${i + 1} 0 obj\n${o}\nendobj\n`;
  });
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n`;
  out += offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("");
  out += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return URL.createObjectURL(new Blob([out], { type: "application/pdf" }));
}

// ---------- store ----------
interface Entry {
  card: PaperCard;
  pages: number | null;
  imported_at: string | null;
  citations: Citation[];
}
interface CollectionState {
  col: Collection;
  papers: Map<string, Entry>;
  candidates: Map<string, Candidate[]>;
  pendingFiles: Map<string, PendingFile>; // citation id -> PDF held at needs_review
}
type JobKind = "extract" | "find" | "download";
interface Job {
  id: string;
  cid: string;
  kind: JobKind;
  paperId: string | null;
  citationId: string | null;
  paperIds: string[];
  started: number;
  duration: number;
  items: number;
  applied: boolean;
  useFallback: boolean;
}

const store = new Map<string, CollectionState>();
const fileBlobs = new Map<string, string>(); // work file id -> blob URL of a sample PDF
const jobs = new Map<string, Job>();
const intents = new Map<string, string>(); // token -> fingerprint
let seq = 100;
const uid = (p: string) => `${p}-${++seq}`;

const grantedAll: Permissions = {
  folder_granted: true,
  network_mode: "retrieval",
  downloads_enabled: true,
  downloads_source: "project",
  can_download: true,
};

function recomputePermissions(p: Permissions): Permissions {
  const can = p.folder_granted && p.network_mode === "retrieval" && p.downloads_enabled;
  return { ...p, downloads_source: p.downloads_enabled ? p.downloads_source : "none", can_download: can };
}

function cite(
  o: number,
  status: CitationStatus,
  text: string,
  m: Partial<Citation> & { canDl?: boolean } = {},
): Citation {
  const { canDl, ...rest } = m;
  return {
    citation_id: uid("o"),
    order: o,
    text,
    status,
    reasons: [],
    title: null,
    authors: [],
    year: null,
    venue: null,
    doi: null,
    can_download: canDl ?? status === "pdf_available",
    downloaded_paper_id: null,
    revision: 1,
    check_kind: status === "check" ? "identity" : null,
    ...rest,
  };
}

function counts(cs: Citation[]): PaperCard["status_counts"] {
  const out: PaperCard["status_counts"] = {};
  for (const c of cs) out[c.status] = (out[c.status] ?? 0) + 1;
  return out;
}

function addPaper(cs: CollectionState, e: Omit<Entry, "card"> & { card: Omit<PaperCard, "file_url" | "status_counts" | "citation_count"> }) {
  const card: PaperCard = {
    ...e.card,
    citation_count: e.citations.length,
    status_counts: counts(e.citations),
    file_url: makePdf(e.card.title, [
      `${e.card.authors.map((a) => a.display).join("; ")} (${e.card.year ?? "n.d."})`,
      e.card.venue ?? "",
      "",
      "Sample file from the RefCollector mock API.",
    ]),
  };
  cs.papers.set(card.paper_id, { ...e, card });
  return card;
}

function seed() {
  const folderA = "C:/Users/me/RefCollector/Coastal erosion thesis";
  const a: CollectionState = {
    col: {
      collection_id: "c-coastal",
      name: "Coastal erosion thesis",
      folder: folderA,
      paper_count: 0,
      check_count: 0,
      permissions: grantedAll,
      settings_revision: "rev-1",
    },
    papers: new Map(),
    candidates: new Map(),
    pendingFiles: new Map(),
  };
  const day = (d: number) => new Date(Date.UTC(2026, 9, d, 9, 30)).toISOString();

  // Downloaded papers first, so uploaded citations can point to them.
  const dl = (id: string, title: string, authors: string[], year: number, venue: string, doi: string, citedBy: string[]) => {
    const file_name = `${authors[0]!.split(",")[0]}_${year}_${title.split(" ").slice(0, 3).join("_")}.pdf`;
    return addPaper(a, {
      pages: null,
      imported_at: null,
      citations: [],
      card: { paper_id: id, kind: "downloaded", title, authors: au(...authors), year, venue, doi, file_name, folder_path: `${folderA}/${file_name}`, cited_by: citedBy, active_job_id: null },
    });
  };
  const w = [
    dl("w-1", "Longshore sediment transport rates on mixed sand and gravel beaches", ["Vandermeer, Anouk", "Okoye, Chidi"], 2016, "Coastal Dynamics Letters", "10.5555/cdl.2016.0412", ["u-thesis", "u-surge"]),
    dl("w-2", "A closure depth revisited for micro-tidal shorelines", ["Larsen, Mikkel"], 2014, "Journal of Shore Management", "10.5555/jsm.2014.0087", ["u-thesis"]),
    dl("w-3", "Wave climate trends in the southern North Sea, 1980 to 2018", ["Brandt, Katrin", "Hartono, Dewi", "Silva, Ana"], 2020, "Ocean Engineering Reports", "10.5555/oer.2020.2210", ["u-thesis", "u-surge"]),
    dl("w-4", "Nourishment longevity and the role of storm sequencing", ["Okoye, Chidi", "Larsen, Mikkel"], 2018, "Journal of Shore Management", "10.5555/jsm.2018.0301", ["u-thesis"]),
    dl("w-5", "Extreme water levels and the choice of return period", ["Brandt, Katrin"], 2019, "Coastal Dynamics Letters", "10.5555/cdl.2019.0193", ["u-surge"]),
  ];
  const wid = (i: number) => w[i]!.paper_id;
  const thesis = [
    cite(1, "downloaded", "Vandermeer, A., Okoye, C. (2016). Longshore sediment transport rates on mixed sand and gravel beaches. Coastal Dynamics Letters 12(4), 301-318.", { title: w[0]!.title, authors: w[0]!.authors, year: 2016, venue: w[0]!.venue, doi: w[0]!.doi, downloaded_paper_id: wid(0) }),
    cite(2, "downloaded", "Larsen, M. (2014). A closure depth revisited for micro-tidal shorelines. J. Shore Manag. 9, 45-61.", { title: w[1]!.title, authors: w[1]!.authors, year: 2014, venue: w[1]!.venue, doi: w[1]!.doi, downloaded_paper_id: wid(1) }),
    cite(3, "downloaded", "Brandt, K., Hartono, D., Silva, A. (2020). Wave climate trends in the southern North Sea, 1980-2018. Ocean Eng. Rep. 31, 1120-1139.", { title: w[2]!.title, authors: w[2]!.authors, year: 2020, venue: w[2]!.venue, doi: w[2]!.doi, downloaded_paper_id: wid(2) }),
    cite(4, "downloaded", "Okoye, C., Larsen, M. (2018). Nourishment longevity and the role of storm sequencing. J. Shore Manag. 13(2), 90-108.", { title: w[3]!.title, authors: w[3]!.authors, year: 2018, venue: w[3]!.venue, doi: w[3]!.doi, downloaded_paper_id: wid(3) }),
    cite(5, "pdf_available", "Nakamura, H. (2017). Cross-shore profile response to repeated storms. Coastal Eng. Proc. 35, 112.", { title: "Cross-shore profile response to repeated storms", authors: au("Nakamura, Haruto"), year: 2017, venue: "Coastal Engineering Proceedings", doi: "10.5555/cep.2017.0112" }),
    cite(6, "pdf_available", "Silva, A., Brandt, K. (2021). Satellite-derived shorelines: accuracy on dissipative beaches. Remote Sens. Coasts 4, 55-74.", { title: "Satellite-derived shorelines: accuracy on dissipative beaches", authors: au("Silva, Ana", "Brandt, Katrin"), year: 2021, venue: "Remote Sensing of Coasts", doi: "10.5555/rsc.2021.0055" }),
    cite(7, "check", "Bakker, W. (2011). The dynamics of dune erosion. Proc. 12th Int. Symp. Coastal Res., 233-240.", { reasons: ["multiple_close_candidates", "year_differs"] }),
    cite(8, "check", "Larsen M., 2015, Beach slope and run-up on gravel. Shore Manag. 10, 12-30.", { reasons: ["author_match_title_weak", "venue_abbreviated"] }),
    cite(9, "no_pdf", "Ahmadi, R. (2013). Tidal inlet stability. Estuar. Coast. Sci. 6, 401-415.", { title: "Tidal inlet stability in meso-tidal settings", authors: au("Ahmadi, Reza"), year: 2013, venue: "Estuarine and Coastal Science", doi: "10.5555/ecs.2013.0401", reasons: ["no_open_copy_found"] }),
    cite(10, "no_match", "Municipality of Westkapelle (2019). Dune maintenance plan 2020-2030 (internal report).", { reasons: ["grey_literature", "no_candidates"] }),
    cite(11, "matched", "Hartono, D. (2021). Gravel beach morphodynamics: field data from two sites. Coastal Dyn. Lett. 17, 77-94.", { title: "Gravel beach morphodynamics: field data from two sites", authors: au("Hartono, Dewi"), year: 2021, venue: "Coastal Dynamics Letters", doi: "10.5555/cdl.2021.0077" }),
    cite(12, "failed", "Silva, A. (2022). Machine-learning shoreline detection: a benchmark. Preprint.", { title: "Machine-learning shoreline detection: a benchmark", authors: au("Silva, Ana"), year: 2022, reasons: ["lookup_timeout"] }),
    cite(13, "extracted", "Okoye, C. (2012). Swash zone hydrodynamics. Ph.D. thesis, Marlow University."),
    cite(14, "extracted", "van den Berg, J. (2010). Dune vegetation and sand capture. Dune Ecol. 3, 1-19."),
    // Accepted work, PDF found and fetched, but held for approval (decision D-46).
    cite(15, "check", "Hartono, D., & Silva, A. (2022). Shoreline response to storm clustering. Coastal Dyn. Lett. 18, 201-220.", { check_kind: "file", title: "Shoreline response to storm clustering", authors: au("Hartono, Dewi", "Silva, Ana"), year: 2022, venue: "Coastal Dynamics Letters", doi: "10.5555/cdl.2022.0201" }),
  ];
  addPaper(a, {
    pages: 212,
    imported_at: day(14),
    citations: thesis,
    card: { paper_id: "u-thesis", kind: "uploaded", title: "Sediment transport and shoreline change on sandy coasts", authors: au("Hartono, Dewi"), year: 2023, venue: "PhD dissertation, Marlow University", doi: null, file_name: "Hartono_2023_Sediment_transport.pdf", folder_path: `${folderA}/Hartono_2023_Sediment_transport.pdf`, cited_by: [], active_job_id: null },
  });
  const surge = [
    cite(1, "downloaded", "Vandermeer, A., Okoye, C. (2016). Longshore sediment transport rates. Coastal Dyn. Lett. 12.", { title: w[0]!.title, authors: w[0]!.authors, year: 2016, venue: w[0]!.venue, doi: w[0]!.doi, downloaded_paper_id: wid(0) }),
    cite(2, "downloaded", "Brandt, K. (2019). Extreme water levels and the choice of return period. Coastal Dyn. Lett. 15.", { title: w[4]!.title, authors: w[4]!.authors, year: 2019, venue: w[4]!.venue, doi: w[4]!.doi, downloaded_paper_id: wid(4) }),
    cite(3, "downloaded", "Brandt, K. et al. (2020). Wave climate trends in the southern North Sea. Ocean Eng. Rep. 31.", { title: w[2]!.title, authors: w[2]!.authors, year: 2020, venue: w[2]!.venue, doi: w[2]!.doi, downloaded_paper_id: wid(2) }),
    cite(4, "pdf_available", "Jansen, P. (2015). Surge decay over shallow shelves. Shelf Hydrodyn. 8, 14-33.", { title: "Surge decay over shallow shelves", authors: au("Jansen, Pieter"), year: 2015, venue: "Shelf Hydrodynamics", doi: "10.5555/sh.2015.0014" }),
    cite(5, "check", "Walker, T. (2016). Joint probability of surge and waves. Flood Risk J. 5, 200-219.", { reasons: ["doi_conflict"] }),
    cite(6, "no_pdf", "Meijer, S. (2018). Historic storm records 1570-1900. Archive Notes 2, 9.", { title: "Historic storm records 1570-1900", authors: au("Meijer, Sanne"), year: 2018, venue: "Archive Notes", reasons: ["no_open_copy_found"] }),
    cite(7, "matched", "Ng, W. (2020). Return level uncertainty with short records. Stat. Hydrol. 14, 71-90.", { title: "Return level uncertainty with short records", authors: au("Ng, Wei"), year: 2020, venue: "Statistical Hydrology", doi: "10.5555/sth.2020.0071" }),
    cite(8, "check", "Brandt, K. (2018). Surge climatology of the German Bight. Ocean Eng. Rep. 29, 77-96.", { check_kind: "file", title: "Surge climatology of the German Bight", authors: au("Brandt, Katrin"), year: 2018, venue: "Ocean Engineering Reports", doi: "10.5555/oer.2018.0077" }),
  ];
  addPaper(a, {
    pages: 14,
    imported_at: day(12),
    citations: surge,
    card: { paper_id: "u-surge", kind: "uploaded", title: "Storm surge return periods along the North Sea coast", authors: au("Brandt, Katrin", "Ng, Wei"), year: 2021, venue: "Coastal Dynamics Letters", doi: "10.5555/cdl.2021.0301", file_name: "Brandt_2021_Storm_surge.pdf", folder_path: `${folderA}/Brandt_2021_Storm_surge.pdf`, cited_by: [], active_job_id: null },
  });
  const dune = [
    cite(1, "extracted", "Koster, L. (2008). Foredune development on nourished beaches. Dune Ecol. 1, 5-22."),
    cite(2, "extracted", "Okoye, C., Larsen, M. (2018). Nourishment longevity and the role of storm sequencing. J. Shore Manag. 13."),
    cite(3, "extracted", "Mendes, F. (2014). Marram grass planting trials. Coastal Veg. 3, 40-52."),
    cite(4, "extracted", "Bakker, W. (2011). The dynamics of dune erosion. Proc. 12th ISCR."),
    cite(5, "extracted", "Visser, M. (2016). Aeolian transport over vegetated dunes. Aeolian Res. Lett. 7, 8-27."),
    cite(6, "extracted", "Johansson, E. (2017). Fencing and sand capture rates. Dune Ecol. 6, 30-44."),
  ];
  addPaper(a, {
    pages: 22,
    imported_at: day(16),
    citations: dune,
    card: { paper_id: "u-dune", kind: "uploaded", title: "Dune restoration after nourishment: a ten-year review", authors: au("Koster, Lotte", "Mendes, Filipa"), year: 2019, venue: "Journal of Shore Management", doi: "10.5555/jsm.2019.0045", file_name: "Koster_2019_Dune_restoration.pdf", folder_path: null, cited_by: [], active_job_id: null },
  });
  const fresh = addPaper(a, {
    pages: null,
    imported_at: day(19),
    citations: [],
    card: { paper_id: "u-draft", kind: "uploaded", title: "Chapter 6 draft, tidal inlets", authors: [], year: null, venue: null, doi: null, file_name: "Chapter 6 draft, tidal inlets.pdf", folder_path: null, cited_by: [], active_job_id: null },
  });
  startJob("c-coastal", "extract", fresh.paper_id, 14000, 1, a);

  // Candidates for the two Check rows of the thesis and the one of the surge paper.
  const cand = (rank: number, title: string, authors: string[], year: number, venue: string, doi: string, reasons: string[]): Candidate => ({
    candidate_id: uid("k"), rank, title, authors: au(...authors), year, venue, doi, reasons, rejected: false,
  });
  a.candidates.set(thesis[6]!.citation_id, [
    cand(1, "The dynamics of dune erosion during storm surges", ["Bakker, Willem"], 2011, "Proceedings of the 12th International Symposium on Coastal Research", "10.5555/iscr.2011.0233", ["title_close_match", "author_match", "year_match"]),
    cand(2, "Dune erosion dynamics: laboratory evidence", ["Bakker, Willem", "de Vries, Iris"], 2012, "Coastal Engineering Proceedings", "10.5555/cep.2012.0098", ["title_close_match", "author_overlap", "year_differs"]),
    cand(3, "Dynamics of dune and beach erosion", ["Bakker, Wim"], 2009, "Geomorphology Notes", "10.5555/gn.2009.0310", ["title_partial_match", "year_differs"]),
  ]);
  a.candidates.set(thesis[7]!.citation_id, [
    cand(1, "Beach slope and run-up on gravel beaches", ["Larsen, Mikkel"], 2015, "Journal of Shore Management", "10.5555/jsm.2015.0012", ["author_match", "year_match", "venue_match"]),
    cand(2, "Beach slope and run-up on mixed beaches", ["Larsen, Mads"], 2015, "Journal of Shore Management", "10.5555/jsm.2015.0140", ["title_close_match", "year_match"]),
  ]);
  a.candidates.set(surge[4]!.citation_id, [
    cand(1, "Joint probability of storm surge and waves for flood risk", ["Walker, Tom"], 2016, "Flood Risk Journal", "10.5555/frj.2016.0200", ["title_close_match", "author_match", "doi_conflict"]),
  ]);
  const heldFile = (c: Citation, wf: string, size: number, host: string, checks: string[]) => {
    a.pendingFiles.set(c.citation_id, { work_file_id: wf, size, source_url: `https://${host}/articles/${wf}.pdf`, failed_checks: checks });
    fileBlobs.set(wf, makePdf(c.title ?? "Held file", [`File held for approval: ${wf}`, `Cited work: ${c.title ?? ""}`, "Sample file from the RefCollector mock API."]));
  };
  heldFile(thesis[14]!, "wf-held-1", 254899, "www.nature.com", ["identity", "version_unknown_or_outside_policy"]);
  heldFile(surge[7]!, "wf-held-2", 1843200, "pure.eur.nl", ["identity"]);
  store.set(a.col.collection_id, a);

  const folderB = "C:/Users/me/RefCollector/Attention reading";
  const b: CollectionState = {
    col: {
      collection_id: "c-attn",
      name: "Attention models reading list",
      folder: folderB,
      paper_count: 0,
      check_count: 0,
      permissions: recomputePermissions({ folder_granted: true, network_mode: "metadata", downloads_enabled: false, downloads_source: "none", can_download: false }),
      settings_revision: "rev-1",
    },
    papers: new Map(),
    candidates: new Map(),
    pendingFiles: new Map(),
  };
  addPaper(b, {
    pages: 11,
    imported_at: day(9),
    citations: [
      cite(1, "matched", "Ivanova, S. (2017). Sparse attention for long sequences. Seq. Learn. Rev. 2, 1-20.", { title: "Sparse attention for long sequences", authors: au("Ivanova, Sofia"), year: 2017, venue: "Sequence Learning Review", doi: "10.5555/slr.2017.0001" }),
      cite(2, "pdf_available", "Moreau, J. (2019). Linear-time attention approximations. Seq. Learn. Rev. 4, 45-70.", { title: "Linear-time attention approximations", authors: au("Moreau, Jules"), year: 2019, venue: "Sequence Learning Review", doi: "10.5555/slr.2019.0045", canDl: false }),
      cite(3, "extracted", "Park, H. (2020). Memory cost of attention. Preprint."),
    ],
    card: { paper_id: "u-sparse", kind: "uploaded", title: "A survey of efficient attention", authors: au("Okafor, Ngozi"), year: 2022, venue: "Sequence Learning Review", doi: "10.5555/slr.2022.0003", file_name: "Okafor_2022_Efficient_attention.pdf", folder_path: `${folderB}/Okafor_2022_Efficient_attention.pdf`, cited_by: [], active_job_id: null },
  });
  store.set(b.col.collection_id, b);
  const doi: CollectionState = {
    col: { collection_id: "c-doi", name: "DOI-first examples", folder: "C:/Users/me/RefCollector/DOI", paper_count: 0, check_count: 0, permissions: { ...grantedAll }, settings_revision: "rev-1" },
    papers: new Map(), candidates: new Map(), pendingFiles: new Map(),
  };
  const doiCitations = [
    cite(1, "extracted", "Unprocessed paper. https://doi.org/10.5555/doi.pass1", { doi: "10.5555/doi.pass1", can_download_by_doi: true, metadata_review_pending: true }),
    cite(2, "check", "Disputed metadata. https://doi.org/10.5555/doi.pass2", { doi: "10.5555/doi.pass2", can_download_by_doi: true, metadata_review_pending: true, reasons: ["AUTHOR_CONFLICT"] }),
    cite(3, "check", "PDF with a failed DOI check. https://doi.org/10.5555/doi.review", { doi: "10.5555/doi.review", can_download_by_doi: true, metadata_review_pending: true, reasons: ["AUTHOR_CONFLICT"] }),
    cite(4, "extracted", "A reference without a DOI.", { can_download_by_doi: false }),
    cite(5, "check", "Ambiguous DOI: 10.5555/first and 10.5555/second.", { can_download_by_doi: false, metadata_review_pending: true }),
  ];
  addPaper(doi, { pages: 1, imported_at: day(1), citations: doiCitations,
    card: { paper_id: "u-doi", kind: "uploaded", title: "DOI download examples", authors: [], year: null, venue: null, doi: null, file_name: "doi-examples.pdf", folder_path: `${doi.col.folder}/doi-examples.pdf`, cited_by: [], active_job_id: null } });
  for (const c of doiCitations.filter((c) => c.status === "check")) {
    doi.candidates.set(c.citation_id, [{ candidate_id: uid("k"), rank: 1, title: "Unconfirmed metadata suggestion", authors: au("Example, Author"), year: 2026, venue: null, doi: c.doi, reasons: ["AUTHOR_CONFLICT"], rejected: false }]);
  }
  store.set(doi.col.collection_id, doi);
}

// ---------- jobs ----------
const STAGES: Record<JobKind, string[]> = {
  extract: ["extract"],
  find: ["resolve", "discover_access"],
  download: ["retrieve"],
};

function startJob(cid: string, kind: JobKind, paperId: string | null, duration: number, items: number, cs: CollectionState, citationId: string | null = null, useFallback = false): string {
  const id = uid("job");
  const scoped = paperId ? [cs.papers.get(paperId)!] : [...cs.papers.values()].filter((p) => p.card.kind === "uploaded");
  const paperIds = scoped.map((p) => p.card.paper_id);
  jobs.set(id, { id, cid, kind, paperId, citationId, paperIds, started: Date.now(), duration, items, applied: false, useFallback });
  for (const p of scoped) p.card.active_job_id = id;
  return id;
}

const WIRE_KIND = { extract: "extract", find: "find_metadata", download: "download" } as const;

function jobStatus(job: Job): JobStatus {
  const t = Math.min(1, (Date.now() - job.started) / job.duration);
  const terminal = t >= 1;
  const stageNames = STAGES[job.kind];
  const stages: StageCounts[] = stageNames.map((name, i) => {
    // stages run one after the other
    const lo = i / stageNames.length;
    const hi = (i + 1) / stageNames.length;
    const f = Math.max(0, Math.min(1, (t - lo) / (hi - lo)));
    const done = Math.floor(job.items * f);
    const running = f > 0 && f < 1 ? 1 : 0;
    return { name, queued: job.items - done - running, running, succeeded: done, failed_retryable: 0, failed_permanent: 0, skipped: 0, retry_wait: 0, cancelled: 0 };
  });
  return {
    job_id: job.id,
    state: terminal ? "completed" : t === 0 ? "queued" : "running",
    kind: WIRE_KIND[job.kind],
    paper_ids: job.paperIds,
    terminal,
    stages,
    warnings: [],
    next_poll_after_ms: 1000,
  };
}

function get(cid: string): CollectionState {
  const cs = store.get(cid);
  if (!cs) throw new ApiError("NOT_FOUND", "Collection not found.", "Reload the page to see the current collections.", 404);
  return cs;
}

function entry(cs: CollectionState, pid: string): Entry {
  const e = cs.papers.get(pid);
  if (!e) throw new ApiError("NOT_FOUND", "Paper not found.", "It may have been removed. Refresh the shelf.", 404);
  return e;
}

function findCitation(cs: CollectionState, oid: string): { e: Entry; c: Citation } {
  for (const e of cs.papers.values()) {
    const c = e.citations.find((x) => x.citation_id === oid);
    if (c) return { e, c };
  }
  throw new ApiError("NOT_FOUND", "Citation not found.", "Refresh the paper and try again.", 404);
}

function refreshCard(e: Entry) {
  e.card.citation_count = e.citations.length;
  e.card.status_counts = counts(e.citations);
}

const CANNED = [
  "Hendriks, B. (2015). Shoreface nourishment performance. Coastal Eng. Proc. 34, 22.",
  "Taylor, R. (2018). Tidal asymmetry in estuary mouths. Estuar. Coast. Sci. 11, 140-160.",
  "Wouters, D. (2012). Sand engine pilots: first results. Shore Manag. 8, 77-95.",
  "Alves, M. (2019). Beach-dune coupling under rising seas. Coastal Dyn. Lett. 15, 330-349.",
  "Cheng, L. (2020). Photogrammetric profiling with drones. Remote Sens. Coasts 3, 18-33.",
  "Holm, E. (2016). Saltmarsh accretion and wave attenuation. Wetland Sci. 9, 101-120.",
  "Raman, V. (2017). Sediment budgets for barrier islands. Barrier Isl. Rev. 2, 5-40.",
  "Dubois, C. (2013). Grain size effects on swash infiltration. J. Shore Manag. 8, 210-225.",
];

// Citations that Find metadata (or Retry) acts on.
const findable = (c: Citation) => c.status === "extracted" || c.status === "failed" || (c.status === "matched" && !c.can_download);

// Papers and citations a run request covers. Precedence: citation_id, paper_id, whole collection.
function runScope(cs: CollectionState, body: { paper_id: string | null; citation_id?: string | null }) {
  if (body.citation_id) {
    const { e, c } = findCitation(cs, body.citation_id);
    return { paperId: e.card.paper_id, citationId: c.citation_id, citations: [c] };
  }
  const entries = body.paper_id ? [entry(cs, body.paper_id)] : [...cs.papers.values()].filter((p) => p.card.kind === "uploaded");
  return { paperId: body.paper_id, citationId: null, citations: entries.flatMap((e) => e.citations) };
}

// Apply a finished job's effects once, so later reads see the new state.
function applyJob(job: Job) {
  if (job.applied) return;
  job.applied = true;
  const cs = get(job.cid);
  const scoped = job.paperIds.map((id) => entry(cs, id));
  for (const e of scoped) {
    if (e.card.active_job_id === job.id) e.card.active_job_id = null;
    if (job.kind === "extract") {
      const n = 6 + Math.floor(Math.random() * 4);
      e.pages ??= 18;
      e.citations = CANNED.slice(0, n).map((t, i) => cite(i + 1, "extracted", t));
      e.card.title = e.card.title.replace(/\.pdf$/i, "");
    } else if (job.kind === "find") {
      e.citations.forEach((c, i) => {
        if (!findable(c) || (job.citationId && c.citation_id !== job.citationId)) return;
        const outcome = (["pdf_available", "pdf_available", "no_pdf", "check", "no_match"] as const)[i % 5]!;
        c.status = outcome;
        c.revision += 1;
        c.can_download = outcome === "pdf_available" && cs.col.permissions.can_download;
        if (outcome === "check") c.reasons = ["multiple_close_candidates"];
        if (outcome === "no_match") c.reasons = ["no_candidates"];
        if (outcome === "pdf_available" || outcome === "no_pdf") {
          const m = /^(.+?), [A-Z]\. \((\d{4})\)\. (.+?)\./.exec(c.text);
          c.title = m?.[3] ?? c.text.slice(0, 60);
          c.authors = au(`${m?.[1] ?? "Unknown"}, ${c.text.includes("Holm") ? "Elin" : "Anna"}`);
          c.year = m ? Number(m[2]) : null;
          c.doi = `10.5555/mock.${c.year ?? 0}.${String(c.order).padStart(4, "0")}`;
        }
        if (outcome === "check") {
          cs.candidates.set(c.citation_id, [
            { candidate_id: uid("k"), rank: 1, title: `${c.text.slice(c.text.indexOf(")") + 3, c.text.indexOf(".", c.text.indexOf(")") + 3))}: an overview`, authors: au("Moreno, Clara"), year: 2015, venue: "Coastal Engineering Proceedings", doi: `10.5555/mock.c${c.order}.1`, reasons: ["title_close_match"], rejected: false },
            { candidate_id: uid("k"), rank: 2, title: "Related work with a similar title", authors: au("Haas, Peter"), year: 2016, venue: "Shore Management Notes", doi: `10.5555/mock.c${c.order}.2`, reasons: ["title_partial_match", "year_differs"], rejected: false },
          ]);
        }
      });
    } else {
      for (const c of e.citations) {
        if (!(job.useFallback ? c.can_download_by_doi : c.can_download) || (job.citationId && c.citation_id !== job.citationId)) continue;
        if (job.useFallback) {
          if (c.status === "downloaded" || c.check_kind === "file") continue;
          const id = uid("cf");
          const url = makePdf(`DOI ${c.doi}`, ["Sample DOI-only PDF.", "Metadata remains unconfirmed."]);
          fileBlobs.set(id, url);
          if (c.doi === "10.5555/doi.review") {
            cs.pendingFiles.set(c.citation_id, { work_file_id: id, file_kind: "citation_file", size: 2048, source_url: "https://example.org/doi.pdf", failed_checks: ["DOI_MISMATCH"] });
            c.status = "check";
            c.check_kind = "file";
          } else {
            const target = addPaper(cs, { pages: 1, imported_at: null, citations: [], card: { paper_id: id, kind: "downloaded", title: `DOI ${c.doi}`, authors: [], year: null, venue: null, doi: c.doi, file_name: `${id}.pdf`, folder_path: `${cs.col.folder}/${id}.pdf`, cited_by: [e.card.paper_id], active_job_id: null } });
            target.file_url = url;
            c.status = "downloaded";
            c.check_kind = null;
            c.downloaded_paper_id = id;
          }
          c.revision += 1;
          continue;
        }
        const existing = [...cs.papers.values()].find((p) => p.card.kind === "downloaded" && p.card.doi === c.doi && c.doi);
        const target =
          existing ??
          { card: addPaper(cs, {
            pages: null,
            imported_at: null,
            citations: [],
            card: { paper_id: uid("w"), kind: "downloaded", title: c.title ?? c.text.slice(0, 60), authors: c.authors, year: c.year, venue: c.venue, doi: c.doi, file_name: `${c.authors[0]?.display.split(",")[0] ?? "Unknown"}_${c.year ?? "nd"}_${(c.title ?? "paper").split(" ").slice(0, 3).join("_")}.pdf`, folder_path: null, cited_by: [], active_job_id: null },
          }) };
        const folder = cs.col.folder;
        if (folder) target.card.folder_path = `${folder}/${target.card.file_name}`;
        if (!target.card.cited_by.includes(e.card.paper_id)) target.card.cited_by.push(e.card.paper_id);
        c.status = "downloaded";
        c.can_download = false;
        c.downloaded_paper_id = target.card.paper_id;
        c.revision += 1;
      }
    }
    refreshCard(e);
  }
}

function settle(cs: CollectionState) {
  for (const job of jobs.values()) {
    if (job.cid === cs.col.collection_id && !job.applied && Date.now() - job.started >= job.duration) applyJob(job);
  }
}

function view(cs: CollectionState): Collection {
  settle(cs);
  const uploaded = [...cs.papers.values()].filter((p) => p.card.kind === "uploaded");
  const downloaded = cs.papers.size - uploaded.length;
  return {
    ...cs.col,
    paper_count: uploaded.length + downloaded,
    check_count: uploaded.reduce((n, p) => n + p.citations.filter((c) => c.status === "check").length, 0),
  };
}

function matches(e: Entry, q: ShelfQuery): boolean {
  const c = e.card;
  if (q.kind && c.kind !== q.kind) return false;
  if (q.status && !(c.kind === "uploaded" ? e.citations.some((x) => x.status === q.status) : q.status === "downloaded")) return false;
  if (q.q) {
    const hay = [c.title, ...c.authors.map((a) => a.display), c.year, c.venue, c.doi, c.file_name].join(" ").toLowerCase();
    return q.q.toLowerCase().split(/\s+/).every((t) => hay.includes(t));
  }
  return true;
}

const dlReason = (p: Permissions): string | null =>
  !p.folder_granted
    ? "Grant folder access first."
    : p.network_mode !== "retrieval"
      ? "Allow network retrieval for this collection first."
      : !p.downloads_enabled
        ? "Allow downloads for this collection first."
        : null;

export const mockApi: LibraryApi = {
  async defaults() {
    await latency();
    return { default_root: "C:/Users/me/RefCollector" };
  },

  async listCollections() {
    await latency();
    return [...store.values()].map(view);
  },

  async renameCollection(cid, name) {
    await latency();
    const clean = name.trim();
    if (!clean) throw new ApiError("INVALID_INPUT", "Collection name cannot be empty.", "Enter a non-empty name.", 400);
    const cs = get(cid);
    cs.col.name = clean;
    return view(cs);
  },

  async collectionConsequences(cid): Promise<CollectionConsequences> {
    await latency();
    const cs = get(cid);
    let citeCount = 0;
    for (const e of cs.papers.values()) citeCount += e.citations.length;
    return {
      collection_id: cid,
      name: cs.col.name,
      paper_count: cs.papers.size,
      citation_count: citeCount,
      file_count: 0,
      folder_path: cs.col.folder,
      message: `Deleting this collection will permanently remove its ${cs.papers.size} papers, ${citeCount} citations, and review decisions from the database. The files in your folder on disk will NOT be deleted.`,
      has_active_jobs: false,
    };
  },

  async deleteCollection(cid) {
    await latency();
    get(cid);
    store.delete(cid);
  },

  async createCollection(body) {
    await latency();
    if (!body.name.trim() || body.name.length > 200) throw new ApiError("INVALID_INPUT", "The name must have 1 to 200 characters.", "Shorten the name.", 400);
    const id = uid("c");
    store.set(id, {
      col: {
        collection_id: id,
        name: body.name.trim(),
        folder: body.folder,
        paper_count: 0,
        check_count: 0,
        permissions: { folder_granted: false, network_mode: "offline", downloads_enabled: false, downloads_source: "none", can_download: false },
        settings_revision: "rev-1",
      },
      papers: new Map(),
      candidates: new Map(),
      pendingFiles: new Map(),
    });
    return view(get(id));
  },

  async permissionIntent(cid, body) {
    await latency();
    const cs = get(cid);
    if (body.expected_settings_revision !== cs.col.settings_revision) {
      throw new ApiError("REVISION_CONFLICT", "The settings changed since this page loaded.", "Reload the collection and try again.", 409);
    }
    const token = `tok-${newKey()}`;
    intents.set(token, `${cid}:${body.change}`);
    const summary: Record<string, unknown> = {
      add_allowed_folder: { change: "Allow writing PDFs to", folder: cs.col.folder },
      set_project_network: { change: "Set network mode for this collection", from: cs.col.permissions.network_mode, to: "retrieval" },
      set_project_downloads: { change: "Allow PDF downloads for this collection", from: "off", to: "on" },
    }[body.change];
    const description = {
      add_allowed_folder: [`Allow RefCollector to write to ${cs.col.folder}`],
      set_project_network: [`Set the network mode of "${cs.col.name}" to retrieval`, "RefCollector may then look up papers and fetch PDFs online"],
      set_project_downloads: [`Allow PDF downloads for "${cs.col.name}"`],
    }[body.change];
    return { confirmation_token: token, expires_at: new Date(Date.now() + 5 * 60_000).toISOString(), description, summary } satisfies Intent;
  },

  async permissionConfirm(cid, body, token) {
    await latency();
    const cs = get(cid);
    if (intents.get(token) !== `${cid}:${body.change}`) throw new ApiError("NOT_AUTHORIZED", "The confirmation token is not valid.", "Start the change again.", 403);
    intents.delete(token);
    if (body.expected_settings_revision !== cs.col.settings_revision) throw new ApiError("REVISION_CONFLICT", "The settings changed since this page loaded.", "Reload the collection and try again.", 409);
    const p = { ...cs.col.permissions };
    if (body.change === "add_allowed_folder") p.folder_granted = true;
    if (body.change === "set_project_network") p.network_mode = "retrieval";
    if (body.change === "set_project_downloads") {
      p.downloads_enabled = true;
      p.downloads_source = "project";
    }
    cs.col.permissions = recomputePermissions(p);
    cs.col.settings_revision = `rev-${Number(cs.col.settings_revision.slice(4)) + 1}`;
    return view(cs);
  },

  async shelf(cid, q): Promise<ShelfPage> {
    await latency();
    if (mode === "error") throw new ApiError("INTERNAL_ERROR", "The shelf could not be read.", "Check the server log, then retry.", 500, true);
    const cs = get(cid);
    settle(cs);
    const all = [...cs.papers.values()].filter((e) => matches(e, q));
    const rank = (e: Entry) => (e.card.kind === "uploaded" ? 0 : 1);
    all.sort((x, y) => rank(x) - rank(y) || (y.imported_at ?? "").localeCompare(x.imported_at ?? "") || x.card.title.localeCompare(y.card.title));
    const limit = q.limit ?? 50;
    const start = Number(q.cursor ?? 0);
    const page = all.slice(start, start + limit);
    return {
      papers: page.map((e) => structuredClone(e.card)),
      next_cursor: start + limit < all.length ? String(start + limit) : null,
      total: all.length,
    };
  },

  async uploadPaper(cid, file) {
    await latency();
    const cs = get(cid);
    if (!/\.pdf$/i.test(file.name)) throw new ApiError("UNSUPPORTED_FILE", "Only PDF files can be added.", "Choose a .pdf file.", 400);
    const existing = [...cs.papers.values()].find((e) => e.card.file_name === file.name && e.card.kind === "uploaded");
    if (existing) return structuredClone(existing.card);
    const card = addPaper(cs, {
      pages: null,
      imported_at: new Date().toISOString(),
      citations: [],
      card: { paper_id: uid("u"), kind: "uploaded", title: file.name.replace(/\.pdf$/i, ""), authors: [], year: null, venue: null, doi: null, file_name: file.name, folder_path: cs.col.folder ? `${cs.col.folder}/${file.name}` : null, cited_by: [], active_job_id: null },
    });
    startJob(cid, "extract", card.paper_id, 4500, 1, cs);
    return structuredClone(card);
  },

  async paperDetail(cid, pid): Promise<PaperDetail> {
    await latency();
    const cs = get(cid);
    settle(cs);
    const e = entry(cs, pid);
    return { paper: structuredClone(e.card), pages: e.pages, imported_at: e.imported_at, citations: structuredClone(e.citations) };
  },

  async findMetadata(cid, body): Promise<Accepted> {
    await latency();
    const cs = get(cid);
    const scope = runScope(cs, body);
    const items = scope.citations.filter(findable).length;
    if (items === 0) return { job_id: null, disabled_reason: "No citations are waiting for metadata." };
    return { job_id: startJob(cid, "find", scope.paperId, 4000 + items * 150, items, cs, scope.citationId), disabled_reason: null };
  },

  async download(cid, body): Promise<Accepted> {
    await latency();
    const cs = get(cid);
    const reason = dlReason(cs.col.permissions);
    if (reason) return { job_id: null, disabled_reason: reason };
    const scope = runScope(cs, body);
    const items = scope.citations.filter((c) => body.use_fallback ? c.can_download_by_doi : c.can_download).length;
    if (items === 0) return { job_id: null, disabled_reason: "No citation has a PDF that can be downloaded." };
    return { job_id: startJob(cid, "download", scope.paperId, 5000 + items * 200, items, cs, scope.citationId, body.use_fallback), disabled_reason: null };
  },

  async citationDetail(cid, oid): Promise<CitationDetail> {
    await latency();
    const cs = get(cid);
    const { c } = findCitation(cs, oid);
    return {
      citation: structuredClone(c),
      candidates: structuredClone(cs.candidates.get(oid) ?? []),
      pending_file: structuredClone(cs.pendingFiles.get(oid) ?? null),
    };
  },

  async fileReviewIntent(cid, oid, body) {
    await latency();
    const cs = get(cid);
    const { c } = findCitation(cs, oid);
    const file = cs.pendingFiles.get(oid);
    if (c.check_kind !== "file" || !file || file.work_file_id !== body.work_file_id || (file.file_kind ?? "work_file") !== (body.file_kind ?? "work_file")) {
      throw new ApiError("INVALID_INPUT", "This file is not waiting for approval.", "Reload the citation.", 400);
    }
    if (body.expected_revision !== c.revision) throw new ApiError("REVISION_CONFLICT", "This citation changed since you opened it.", "Reload the citation and review the file again.", 409);
    const token = `tok-${newKey()}`;
    intents.set(token, `file:${oid}:${body.file_kind ?? "work_file"}:${body.action}:${body.work_file_id}`);
    const host = file.source_url ? new URL(file.source_url).hostname : "an unknown host";
    const verb = body.action === "approve" ? "Approve" : "Reject";
    return {
      confirmation_token: token,
      expires_at: new Date(Date.now() + 5 * 60_000).toISOString(),
      description: [
        `${verb} this PDF (${file.size.toLocaleString("en-US")} bytes from ${host}) for "${c.title ?? c.doi ?? "this citation"}"`,
        `It failed these checks: ${file.failed_checks.join(", ")}`,
        body.action === "approve" ? "Approving marks the file as verified and records your decision" : "Rejecting removes the file from this citation and records your decision",
      ],
      summary: { action: body.action, work_file_id: body.work_file_id },
    };
  },

  async fileReviewConfirm(cid, oid, body, token) {
    await latency();
    const cs = get(cid);
    const { e, c } = findCitation(cs, oid);
    if (intents.get(token) !== `file:${oid}:${body.file_kind ?? "work_file"}:${body.action}:${body.work_file_id}`) throw new ApiError("NOT_AUTHORIZED", "The confirmation token is not valid.", "Start the review again.", 403);
    intents.delete(token);
    if (body.expected_revision !== c.revision) throw new ApiError("REVISION_CONFLICT", "This citation changed since you opened it.", "Reload the citation and review the file again.", 409);
    const file = cs.pendingFiles.get(oid)!;
    cs.pendingFiles.delete(oid);
    c.check_kind = null;
    if (body.action === "approve") {
      const same = [...cs.papers.values()].find((p) => p.card.kind === "downloaded" && p.card.doi === c.doi && c.doi);
      const target =
        same?.card ??
        addPaper(cs, {
          pages: null,
          imported_at: null,
          citations: [],
          card: { paper_id: file.file_kind === "citation_file" ? file.work_file_id : uid("w"), kind: "downloaded", title: c.title ?? `DOI ${c.doi ?? "unknown"}`, authors: c.authors, year: c.year, venue: c.venue, doi: c.doi, file_name: `${c.authors[0]?.display.split(",")[0] ?? "Unknown"}_${c.year ?? "nd"}.pdf`, folder_path: null, cited_by: [], active_job_id: null },
        });
      if (cs.col.folder) target.folder_path = `${cs.col.folder}/${target.file_name}`;
      target.file_url = fileBlobs.get(file.work_file_id) ?? target.file_url;
      if (!target.cited_by.includes(e.card.paper_id)) target.cited_by.push(e.card.paper_id);
      c.status = "downloaded";
      c.can_download = false;
      c.downloaded_paper_id = target.paper_id;
      if (!c.metadata_review_pending) c.reasons = [];
    } else {
      c.status = "failed";
      c.reasons = ["FILE_REJECTED_BY_USER"];
      c.can_download = cs.col.permissions.can_download;
      fileBlobs.delete(file.work_file_id);
    }
    c.revision += 1;
    refreshCard(e);
    return structuredClone(c);
  },

  workFileUrl: (_cid, id) => fileBlobs.get(id) ?? "",

  async reviewIntent(cid, oid, body: ReviewIn) {
    await latency();
    const cs = get(cid);
    const { c } = findCitation(cs, oid);
    if (body.expected_revision !== c.revision) throw new ApiError("REVISION_CONFLICT", "This citation changed since you opened it.", "Reload the citation and review it again.", 409);
    const cand = cs.candidates.get(oid)?.find((k) => k.candidate_id === body.candidate_id);
    if (!cand) throw new ApiError("NOT_FOUND", "Candidate not found.", "Reload the citation.", 404);
    const token = `tok-${newKey()}`;
    intents.set(token, `${oid}:${body.action}:${body.candidate_id}`);
    return {
      confirmation_token: token,
      expires_at: new Date(Date.now() + 5 * 60_000).toISOString(),
      description: [
        body.action === "accept" ? `Accept "${cand.title}" as the match for this citation` : `Reject "${cand.title}" as a match for this citation`,
        `DOI ${cand.doi ?? "unknown"}`,
      ],
      summary: { action: body.action, candidate: cand.title, doi: cand.doi },
    };
  },

  async reviewConfirm(cid, oid, body, token) {
    await latency();
    const cs = get(cid);
    const { e, c } = findCitation(cs, oid);
    if (intents.get(token) !== `${oid}:${body.action}:${body.candidate_id}`) throw new ApiError("NOT_AUTHORIZED", "The confirmation token is not valid.", "Start the review again.", 403);
    intents.delete(token);
    if (body.expected_revision !== c.revision) throw new ApiError("REVISION_CONFLICT", "This citation changed since you opened it.", "Reload the citation and review it again.", 409);
    const list = cs.candidates.get(oid) ?? [];
    const cand = list.find((k) => k.candidate_id === body.candidate_id)!;
    if (body.action === "accept") {
      const fileStatus = c.status;
      const fileCheck = c.check_kind;
      Object.assign(c, { title: cand.title, authors: cand.authors, year: cand.year, venue: cand.venue, doi: cand.doi, reasons: [], status: "pdf_available" as const, can_download: cs.col.permissions.can_download });
      if (c.metadata_review_pending !== undefined) {
        c.metadata_review_pending = false;
        if (c.downloaded_paper_id || cs.pendingFiles.get(oid)?.file_kind === "citation_file") {
          c.status = fileStatus;
          c.check_kind = fileCheck;
          c.can_download = false;
        }
      }
      cs.candidates.delete(oid);
    } else {
      cand.rejected = true;
      if (list.every((k) => k.rejected)) {
        c.status = "no_match";
        c.reasons = ["all_candidates_rejected"];
      }
    }
    c.revision += 1;
    refreshCard(e);
    return structuredClone(c);
  },

  async sync(cid) {
    await latency();
    const cs = get(cid);
    for (const e of cs.papers.values()) if (!e.card.folder_path && cs.col.folder) e.card.folder_path = `${cs.col.folder}/${e.card.file_name}`;
    return view(cs);
  },

  async job(cid, jid) {
    await latency();
    const cs = get(cid);
    const job = jobs.get(jid);
    if (!job || job.cid !== cid) throw new ApiError("NOT_FOUND", "Job not found.", "Refresh the shelf.", 404);
    const status = jobStatus(job);
    if (status.terminal) settle(cs);
    return status;
  },
};

seed();
