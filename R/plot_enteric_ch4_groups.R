# Plot per-animal-group enteric CH4 emissions (central value + p5-p95)
#
# Reads the Monte-Carlo entry from results.json and draws, for each
# animal group, a bar of the central value with p5-p95 whiskers.
#
# Usage: Rscript R/plot_enteric_ch4_groups.R [path/to/results.json]

args <- commandArgs(trailingOnly = TRUE)
json_path <- if (length(args) >= 1) args[1] else "results.json"
out_png <- "R/fig_ch4_par_lot.png"

suppressPackageStartupMessages({
  library(jsonlite)
  library(ggplot2)
})

stopifnot("results file not found" = file.exists(json_path))

# Single JSON document: {"format_version": ..., "simulations": [entry, ...]}
doc <- fromJSON(json_path, simplifyVector = FALSE)
entries <- doc$simulations

# Keep the most recent Monte-Carlo entry carrying per-group CH4 stats.
entries <- Filter(function(e) {
  !is.null(e$uncertainty) &&
    !is.null(e$uncertainty$enteric_ch4_per_group_kg)
}, entries)
if (length(entries) == 0) {
  stop("No Monte-Carlo entry with 'enteric_ch4_per_group_kg' in ", json_path,
       ". Run run_case_study.py (Monte-Carlo) first.")
}
mc <- entries[[length(entries)]]
groups <- mc$uncertainty$enteric_ch4_per_group_kg

df <- data.frame(
  lot = rep(names(groups), each = 1),
  central_kg = vapply(groups, function(g) g$central_kg, numeric(1)),
  p5  = vapply(groups, function(g) g$p5,  numeric(1)),
  p95 = vapply(groups, function(g) g$p95, numeric(1)),
  row.names = NULL
)
# Preserve the JSON group order on the x-axis.
df$lot <- factor(df$lot, levels = df$lot)

p <- ggplot(df, aes(x = lot, y = central_kg)) +
  geom_col(fill = "steelblue", alpha = 0.8) +
  geom_errorbar(aes(ymin = p5, ymax = p95), width = 0.25, linewidth = 0.6) +
  labs(
    title = "Méthane entérique par lot d'animaux",
    subtitle = sprintf(
      "Barres : valeur centrale ; moustaches : p5–p95 (Monte-Carlo, %d itérations)",
      mc$uncertainty$n_iterations
    ),
    x = "Lot",
    y = "CH4 entérique (kg/an)"
  ) +
  theme_minimal()

ggsave(out_png, p, width = 7, height = 5, dpi = 150)
message("Figure saved: ", out_png)
