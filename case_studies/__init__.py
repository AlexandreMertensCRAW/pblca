"""Case-study configurations (declarative files, one per study).

Each module exposes a ``CONFIG`` (pblca.scenarios.CaseStudyConfig):
the farm builder, the on-farm measurements, the model variants to
sweep per slot and the coherent named combinations to run. The
orchestration is case-study independent
(``pblca.scenarios.run_case_study``).

Adding a new case study = adding one module here; nothing else
changes. Variants requiring measurements the farm does not have are
excluded from the grid automatically (with a warning), so a config
can be exchanged between farms with different data availability.
"""
