# plot_enteric_ch4_per_head_day.R
#
# Enteric CH4 per head and per day (g CH4/head/day): central value
# and Monte-Carlo uncertainty (p5-p95) for each enteric_ch4 variant,
# one facet per animal group.
#
# This is the unit of the AHCS (GreenFeed) measurements, so the
# modelled variants can be compared directly with the measured_ahcs
# variant regardless of head count and days on farm.
#
# Reads the JSON datastore written by run_case_study.py. For each
# Monte-Carlo entry of an enteric_ch4 variant, the engine records
# `uncertainty$enteric_ch4_per_group_g_day` with one statistics
# block per animal group.
#
# Usage:
#   Rscript R/plot_enteric_ch4_per_head_day.R <results.json> [output.png]
#
# Requires: jsonlite, ggplot2.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) {
  stop("Usage: Rscript R/plot_enteric_ch4_per_head_day.R <results.json> [output.png]")
}
json_path <- args[1]
out_png <- if (length(args) >= 2) args[2] else "R/fig_ch4_par_tete_jour.png"

suppressPackageStartupMessages({
  library(jsonlite)
  library(ggplot2)
})

# Single JSON document: {"format_version": ..., "simulations": [entry, ...]}
doc <- fromJSON(json_path, simplifyVector = FALSE)
entries <- doc$simulations

# Keep the Monte-Carlo entries carrying per-head-per-day CH4
# statistics and extract the enteric variant from the model
# selection of each entry.
mc_entries <- Filter(function(e) {
  !is.null(e$uncertainty) &&
    !is.null(e$uncertainty$enteric_ch4_per_group_g_day) &&
    !is.null(e$model_selection$enteric_ch4)
}, entries)
if (length(mc_entries) == 0) {
  stop("No Monte-Carlo entry with 'enteric_ch4_per_group_g_day' in ", json_path,
       ". Run run_case_study.py first.")
}

# Keep only the LAST entry per variant (defensive deduplication:
# geom_col stacks bars sharing one x position, so two entries of the
# same variant would draw a double-height bar with a mislocated
# whisker).
by_variant <- list()
for (e in mc_entries) {
  variant <- e$model_selection$enteric_ch4$variant
  by_variant[[variant]] <- e
}
mc_entries <- unname(by_variant)

rows <- list()
i <- 1
for (e in mc_entries) {
  variant <- e$model_selection$enteric_ch4$variant
  groups <- e$uncertainty$enteric_ch4_per_group_g_day
  for (lot in names(groups)) {
    g <- groups[[lot]]
    rows[[i]] <- data.frame(
      lot = lot, modele = variant,
      central_g_day = g$central_g_day, p5 = g$p5, p95 = g$p95
    )
    i <- i + 1
  }
}
df <- do.call(rbind, rows)

# Stable facet order: order of the groups in the JSON (first entry).
lot_levels <- names(mc_entries[[1]]$uncertainty$enteric_ch4_per_group_g_day)
df$lot <- factor(df$lot, levels = lot_levels)
# Variant display order: order of appearance in the JSON entries.
df$modele <- factor(df$modele, levels = unique(df$modele))

p <- ggplot(df, aes(x = modele, y = central_g_day)) +
  geom_col(fill = "steelblue") +
  geom_errorbar(aes(ymin = p5, ymax = p95), width = 0.25) +
  facet_wrap(~ lot, scales = "free_y") +
  labs(
    title = "Enteric CH4 per head and per day — model comparison",
    subtitle = "Bars: central values; whiskers: Monte-Carlo p5-p95",
    x = "Enteric CH4 model variant",
    y = expression("CH"[4]*" (g per head per day)")
  ) +
  theme_bw() +
  theme(
    panel.background = element_rect(fill = "white", colour = NA),
    panel.grid.minor = element_blank(),
    axis.text.x = element_text(angle = 30, hjust = 1)
  )

ggsave(out_png, p, width = 9, height = 6, dpi = 150)
message("Figure saved: ", out_png)
