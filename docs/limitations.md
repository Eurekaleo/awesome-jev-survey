# Limitations

What this release can and cannot support, stated plainly so that nothing downstream overstates it.

## The review process

- **One AI-assisted reviewer.** Screening, extraction and synthesis were done by a single AI-assisted workflow. There is no second independent screener, no inter-rater agreement and no registered protocol; this is not a PRISMA-compliant systematic review. Before formal submission, a second person should re-screen the arXiv records and re-extract the core evidence records.
- **Search coverage.** arXiv `all:` queries search metadata, not full text. Literature was found through the arXiv API, GitHub search and community catalogues; Scopus, Web of Science, ACL Anthology, OpenReview and Google Scholar were not searched exhaustively. Absence from the results is not evidence of absence.
- **Timing.** The field changes daily, and the data cutoff is stated on the website and in the manuscript. arXiv announces new submissions around 00:00 UTC and its API can lag behind the announcement, so a run shortly after midnight may not yet see the newest listing.
- **Background depth.** Background references and related reviews were checked against metadata and abstracts; they support framing and baselines, not detailed claims.
- **Repositories.** README files at pinned commits and a few selected source files were read. Nothing was installed or executed. Repository claims are the maintainers’ own and are labelled as community reports. Entries taken from community catalogues were traced to their own README or paper first; entries whose README does not mention Jev or a typed decision model were left out.

## The evidence

- **Nothing was reproduced.** Every number is author-, vendor- or community-reported. `independently_reproduced` is false everywhere, and the validator refuses `true` without a public run log.
- **Young preprints.** All core studies are arXiv preprints, most in a single version. Several were revised soon after posting; their records follow the version that was read, and later versions may change the results again. Several studies do not state the Jev version they called.
- **Clustering.** Several author groups contribute more than one study; each group is synthesised as a study family, not counted as independent replications. In one case a benchmark’s author also ranks their own model on it. Other studies share benchmarks or base models. These overlaps limit independence.
- **Heterogeneity.** Tasks, metrics, bins, sample sizes and measurement scopes differ. The survey therefore does not pool effects or rank systems; charts that look comparable (scope groups, matrices) are organised to prevent pooling.
- **Hosted-model drift.** Hosted Jev is non-deterministic and can change behind aliases; black-box results describe a model at a point in time.
- **Vendor material.** Documentation, launch posts and the vendor’s evaluation dashboard are used for what the interface is and what the vendor claims; they are never treated as independent evaluation.

## The artefacts

- **Illustrative explainers.** The website’s interactive panels labelled *Illustrative* use hand-made or simulated numbers. They explain mechanisms; they are not measurements.
- **Taxonomy.** The five stages, six readout families and ten findings are an analytical organisation for this survey. They are not the architecture of any commercial model and are not claimed as the first such taxonomy.
- **Status.** This is a working draft: it has no DOI and has not been peer-reviewed, and publication status fields are intentionally unset.
- **Performance figures.** Page-weight numbers are static byte counts from `scripts/measure.py`; no Lighthouse score or field Core Web Vitals were collected.
