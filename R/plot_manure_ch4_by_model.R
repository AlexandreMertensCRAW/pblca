# plot_manure_ch4_by_model.R
#
# Manure CH4 per manure-handling system: central value and
# Monte-Carlo uncertainty (p5-p95) for each manure_ch4 variant of
# the case study.
#
# Reads the JSON datastore written by run_case_study.py. For each
# Monte-Carlo entry of a manure_ch4 variant (sim_id =
# "mc_<case>_manure_ch4_<variant>"), the engine records
# `uncertainty$manure_ch4_by_system_kg` with one statistics block
# per manure system (slurry, solid, pasture...).
#
# Usage:
#   Rscript R/plot_manure_ch4_by_model.R <results.json> [output.png]
#
# Requires: jsonlite, ggplot2.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) {
  stop("Usage: Rscript R/plot_manure_ch4_by_model.R <results.json> [output.png]")
}
json_path <- args[1]
out_path <- if (length(args) >= 2) args[2] else "R/fig_ch4_fumier_par_modele.png"

suppressPackageStartupMessages({
  library(jsonlite)
  library(ggplot2)
})

# Single JSON document: {"format_version": ..., "simulations": [entry, ...]}
doc <- fromJSON(json_path, simplifyVector = FALSE)
entries <- doc$simulations

# Keep the Monte-Carlo entries carrying per-system CH4 statistics
# and extract the manure variant from the model selection.
mc_entries <- Filter(function(e) {
  !is.null(e$uncertainty) &&
    !is.null(e$uncertainty$manure_ch4_by_system_kg) &&
    !is.null(e$model_selection$manure_ch4)
}, entries)
if (length(mc_entries) == 0) {
  stop("No Monte-Carlo entry with 'manure_ch4_by_system_kg' in ", json_path,
       ". Run run_case_study.py first.")
}

# Keep only the LAST entry per variant (defensive deduplication:
# geom_col stacks bars sharing one x position, so two entries of the
# same variant would draw a double-height bar with a mislocated
# whisker).
by_variant <- list()
for (e in mc_entries) {
  variant <- e$model_selection$manure_ch4$variant
  by_variant[[variant]] <- e
}
mc_entries <- unname(by_variant)

rows <- list()
i <- 1
for (e in mc_entries) {
  variant <- e$model_selection$manure_ch4$variant
  systems <- e$uncertainty$manure_ch4_by_system_kg
  for (sys in names(systems)) {
    s <- systems[[sys]]
    rows[[i]] <- data.frame(
      systeme = sys, modele = variant,
      central_kg = s$central_kg, p5 = s$p5, p95 = s$p95
    )
    i <- i + 1
  }
}
df <- do.call(rbind, rows)

# Stable facet order: order of the systems in the JSON (first entry).
sys_levels <- names(mc_entries[[1]]$uncertainty$manure_ch4_by_system_kg)
df$systeme <- factor(df$systeme, levels = sys_levels)
# Variant display order: order of appearance in the JSON entries.
df$modele <- factor(df$modele, levels = unique(df$modele))

p <- ggplot(df, aes(x = modele, y = central_kg)) +
  geom_col(fill = "steelblue") +
  geom_errorbar(aes(ymin = p5, ymax = p95), width = 0.25) +
  facet_wrap(~ systeme) +
  labs(
    title = "Manure CH4 per handling system — model comparison",
    subtitle = "Bars: central values; whiskers: Monte-Carlo p5-p95",
    x = "Manure CH4 model variant",
    y = expression("CH"[4]*" (kg per year)")
  ) +
  theme_bw() +
  theme(
    panel.background = element_rect(fill = "white", colour = NA),
    panel.grid.minor = element_blank(),
    axis.text.x = element_text(angle = 30, hjust = 1)
  )

ggsave(out_path, p, width = 9, height = 5.5, dpi = 150)
message("Figure written to ", out_path)
