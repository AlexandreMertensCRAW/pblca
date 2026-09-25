# Plot per-animal-group enteric CH4 emissions, by model variant
#
# Reads the Monte-Carlo entries from results.json (one per enteric_ch4
# variant, sim_id = "mc_enteric_<variant>") and draws, for each animal
# group (facet), a bar of the central value with p5-p95 whiskers for
# every model variant.
#
# Usage: Rscript R/plot_enteric_ch4_by_model.R [path/to/results.json]

args <- commandArgs(trailingOnly = TRUE)
json_path <- if (length(args) >= 1) args[1] else "results.json"
out_png <- "R/fig_ch4_par_modele.png"

suppressPackageStartupMessages({
  library(jsonlite)
  library(ggplot2)
})

stopifnot("results file not found" = file.exists(json_path))

# Single JSON document: {"format_version": ..., "simulations": [entry, ...]}
doc <- fromJSON(json_path, simplifyVector = FALSE)
entries <- doc$simulations

# Keep the Monte-Carlo entries carrying per-group CH4 statistics and
# extract the enteric variant from the model selection of each entry.
mc_entries <- Filter(function(e) {
  !is.null(e$uncertainty) &&
    !is.null(e$uncertainty$enteric_ch4_per_group_kg) &&
    !is.null(e$model_selection$enteric_ch4)
}, entries)
if (length(mc_entries) == 0) {
  stop("No Monte-Carlo entry with 'enteric_ch4_per_group_kg' in ", json_path,
       ". Run run_case_study.py (section 2bis) first.")
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
  groups <- e$uncertainty$enteric_ch4_per_group_kg
  for (lot in names(groups)) {
    g <- groups[[lot]]
    rows[[i]] <- data.frame(
      lot = lot, modele = variant,
      central_kg = g$central_kg, p5 = g$p5, p95 = g$p95
    )
    i <- i + 1
  }
}
df <- do.call(rbind, rows)

# Stable facet order: order of the groups in the JSON (first entry).
lot_levels <- names(mc_entries[[1]]$uncertainty$enteric_ch4_per_group_kg)
df$lot <- factor(df$lot, levels = lot_levels)
# Variant display order: order of appearance in the JSON entries.
df$modele <- factor(df$modele, levels = unique(df$modele))

p <- ggplot(df, aes(x = modele, y = central_kg)) +
  geom_col(fill = "steelblue", alpha = 0.8) +
  geom_errorbar(aes(ymin = p5, ymax = p95), width = 0.25, linewidth = 0.5) +
  facet_wrap(~lot, scales = "free_y", ncol = 1) +
  labs(
    title = "Méthane entérique par lot d'animaux et par modèle",
    subtitle = "Barres : valeur centrale ; moustaches : p5–p95 (Monte-Carlo)",
    x = "Modèle (variante enteric_ch4)",
    y = "CH4 entérique (kg/an)"
  ) +
  theme_minimal(base_size = 11) +
  theme(axis.text.x = element_text(angle = 30, hjust = 1))

ggsave(out_png, p, width = 8, height = 10, dpi = 150)
message("Figure saved: ", out_png)
