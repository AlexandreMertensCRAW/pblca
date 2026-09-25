# AGENTS.md — Interaction rules for AI agents

This file sets the collaboration rules for any AI agent (or
contributor) working on this repository. They apply to all sessions
and conversations, without exception.

## Project context

PBLCA is a process-based LCA (Life Cycle Assessment) engine for
mixed crop-livestock farms, compliant with ISO 14040/14044.
Three-layer architecture: processes (layer 1), CH4/CO2/N2O gas
flows (layer 2), impacts (layer 3: GWP100, GWP20, GWP*). Model
registry with a universal interface, Monte-Carlo uncertainty
propagation, bibliographic traceability of every value.

## Rule 1 — Interpretation validated before any code change

Before modifying the code, the agent must:

1. present its interpretation of the request (what is understood,
   what will be changed, the implementation options envisaged);
2. wait for the user's explicit validation;
3. only then carry out the changes.

## Rule 2 — Validation before any GitHub push

Once the changes are implemented and tested:

1. the agent presents a summary (commit, modified files, tests
   run, results);
2. the agent waits for the user's explicit agreement;
3. only then pushes to GitHub.

## Repository conventions

- Comments, docstrings and commit messages in English; data
  identifiers in French (e.g. veaux_0_6mois, prairie_permanente).
- Every numerical value must keep its bibliographic reference
  (Reference class); no unsourced value.
- Tests: pytest (`python -m pytest tests/`); documentation: pdoc
  (`pdoc pblca -o docs_html`). Both must pass before committing.
- No interpolated values where the source provides tabulated
  values (explicit user preference).
