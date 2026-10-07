# Changelog

Notable changes to the data, website and manuscript.

## 0.3.1 — 2026-10-07

- Studies from the 7 October arXiv listing, each read in full: content moderation with hosted Jev and Laya, readout stability of prefill-only decision models, a looped open decision model (SanSi) with hosted Jev as a reference, query optimisation with calibrated confidences, behaviour-tree agents with typed decisions, a preregistered evaluation of the open contrastive model CLM as a judge, a registered test of a 4B model for questions about randomised trials, a typed decision layer for materials discovery, multi-agent coordination by a System One model, and agents that build cheaper reusable solutions compared with Jev.
- A revised candidate-coverage study now tests whether rejection thresholds transfer across tasks; its records were updated. Two other revised studies changed wording only.
- Two open models added to the ecosystem: CLM and Jev-LCT.
- Findings, failure modes, application cards and the manuscript updated with the new evidence.

## 0.3.0 — 2026-10-07

- Literature through early October 2026: new core studies on option semantics, missing-answer rejection, probability coherence, numeric and structural reasoning, security, agents, networks, medicine, search, databases and robotics, and new open models in several languages. Every core study was read in full; studies posted in revised versions were re-read and their records updated.
- Ten findings instead of seven. New: a calibrated probability is not a coherent belief (F8); typed decisions read what the input states but do not reliably compute what it implies (F9); a typed output does not make a decision safe from its inputs (F10). Each finding now lists curated key evidence, and existing findings were revised with the new studies.
- Open ecosystem: open models and readouts, benchmarks, applications and tools, each read at a pinned commit, with weights, training code, evaluation material and data recorded separately. Entries from community catalogues, including the OmniJev gallery, were traced to their own READMEs or papers; entries whose README does not mention Jev were left out.
- Related reviews and catalogues compared side by side; lineage now covers study families, shared authors and name collisions (for example the several unrelated projects called OpenJev).
- Redesigned website: chapter-based layout, findings with headline figures, a sortable study-by-finding heatmap, ecosystem tabs, a news list and a citation card, with a lighter first load.
- Manuscript rewritten around the ten findings, with new figures and an ecosystem table; README regenerated as a curated list.

## 0.2.0 — 2026-09-24

- Four new core studies from the 24 September arXiv listing: Jev on legal contract inference (ContractNLI), Jev as a radiology report factuality judge, KITE (population experiments with a typed Jev kernel) and JEV-Star (StarCraft II control with Jev action selection and GPT-6 planning), with their evidence records and code repositories.
- New application areas (legal document review, population simulation) and a new failure mode: a consistent answer can be consistently wrong.
- Findings, tables and figures in the manuscript updated to include the new studies.
- Leaner repository: compact screening and discovery lists replace the raw search files.

## 0.1.0 — 2026-09-23

First release.

- Literature on TypeSafe’s Jev and Jev-like typed decision models: core studies, a peripheral study, and background references on calibration, selective prediction, structured output, routing and judging.
- Evidence records with source locators, measurement scope, sample size and model version, linked to cross-cutting findings.
- Field-by-field openness audit of implementation and evaluation resources.
- Website with search, filters, evidence panels and exports; manuscript draft (PDF, Markdown, LaTeX).
- Code under the MIT License; text, figures and data under CC BY 4.0.
