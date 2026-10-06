# analyse_enteric_sensitivity.R
#
# Parameter-to-emissions sensitivity analysis of the enteric-CH4
# model variants, from the paired variant grid written by
# run_case_study.py (run_paired_variant_grid):
#
#   uncertainty$emissions_table       one row per Monte-Carlo
#                                     iteration: the farm-total
#                                     enteric CH4 (kg/yr) of each
#                                     evaluated variant (paired
#                                     columns: every variant of a
#                                     row shares the SAME parameter
#                                     draw);
#   uncertainty$parameter_draws_table one row per iteration: the
#                                     drawn value of every parameter
#                                     (full traceability).
#
# Outputs (in R/ by default):
#   enteric_paired_emissions.csv    the emissions table;
#   enteric_paired_parameter_draws.csv  the parameter-draws table;
#   enteric_paired_correlation.csv  the long-form correlation table
#                                   (parameter x variant x method),
#                                   sorted by |Spearman| within each
#                                   (variant, method);
#   fig_correlation_parametres_ch4.png  the heatmap of the Spearman
#                                   correlations (parameters x
#                                   variants).
#
# Usage:
#   Rscript R/analyse_enteric_sensitivity.R <results.json> [output_dir]
#
# Requires: jsonlite, ggplot2.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) {
  stop("Usage: Rscript R/analyse_enteric_sensitivity.R <results.json> [output_dir]")
}
json_path <- args[1]
out_dir <- if (length(args) >= 2) args[2] else "R"

suppressPackageStartupMessages({
  library(jsonlite)
  library(ggplot2)
})

# Single JSON document: {"format_version": ..., "simulations": [...]}.
doc <- fromJSON(json_path, simplifyVector = FALSE)
entries <- doc$simulations

# The paired-grid entries carry uncertainty$emissions_table. Keep the
# LAST one (a rerun supersedes the previous one).
grid_entries <- Filter(function(e) {
  !is.null(e$uncertainty) && !is.null(e$uncertainty$emissions_table)
}, entries)
if (length(grid_entries) == 0) {
  stop("No paired-enteric-grid entry with 'emissions_table' in ", json_path,
       ". Run run_case_study.py first.")
}
grid <- grid_entries[[length(grid_entries)]]

emissions_rows <- grid$uncertainty$emissions_table
params_rows <- grid$uncertainty$parameter_draws_table
variants <- unlist(grid$uncertainty$variants)
if (is.null(emissions_rows) || is.null(params_rows) || length(variants) == 0) {
  stop("Malformed paired-grid section (tables or variants missing).")
}

# Long data.frame: one row per iteration with one column per variant
# (the lists of the JSON entries are already rectangular: every
# iteration shares the same runnable variants).
emissions <- do.call(rbind, lapply(emissions_rows, function(r) {
  row <- as.data.frame(lapply(variants, function(v) r[[v]]),
                       stringsAsFactors = FALSE)
  names(row) <- variants
  row$iteration <- r$iteration
  row
}))
pids <- setdiff(names(params_rows[[1]]), "iteration")
draws <- do.call(rbind, lapply(params_rows, function(r) {
  row <- as.data.frame(lapply(pids, function(p) r[[p]]),
                       stringsAsFactors = FALSE)
  names(row) <- pids
  row
}))
stopifnot(nrow(emissions) == nrow(draws))

dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
write.csv(emissions, file.path(out_dir, "enteric_paired_emissions.csv"),
          row.names = FALSE)
write.csv(draws, file.path(out_dir, "enteric_paired_parameter_draws.csv"),
          row.names = FALSE)

# Correlation matrix (parameters x variants), Pearson and Spearman.
# A constant draw (sd = 0) has no defined correlation: reported NA.
methods <- c("pearson", "spearman")
corr <- do.call(rbind, unlist(lapply(methods, function(m) {
  lapply(variants, function(v) {
    sapply(pids, function(p) {
      x <- draws[[p]]
      if (length(unique(x)) < 2) return(NA_real_)
      suppressWarnings(cor(x, emissions[[v]], method = m))
    }) -> r
    data.frame(parameter = pids, variant = v, method = m,
               correlation = as.numeric(r), stringsAsFactors = FALSE)
  })
}), recursive = FALSE))
corr <- corr[order(corr$variant, corr$method,
                   -abs(corr$correlation)), ]
write.csv(corr, file.path(out_dir, "enteric_paired_correlation.csv"),
          row.names = FALSE)

# Heatmap of the Spearman correlations (robust to the lognormal
# draws): parameters in rows, variants in columns.
heat <- corr[corr$method == "spearman", ]
heat$parameter <- factor(heat$parameter, levels = rev(sort(unique(heat$parameter))))
heat$variant <- factor(heat$variant, levels = variants)
p <- ggplot(heat, aes(x = variant, y = parameter, fill = correlation)) +
  geom_tile() +
  scale_fill_gradient2(low = "#2166ac", mid = "white", high = "#b2182b",
                       midpoint = 0, limits = c(-1, 1), na.value = "grey90") +
  labs(
    title = "Parameter-to-emissions sensitivity of the enteric CH4 models",
    subtitle = paste0("Spearman correlation, paired Monte-Carlo draws; n = ",
                      nrow(emissions), " iterations"),
    x = "Model variant", y = "Parameter", fill = "rho"
  ) +
  theme_bw() +
  theme(
    axis.text.x = element_text(angle = 35, hjust = 1, size = 8),
    axis.text.y = element_text(size = 6),
    panel.grid = element_blank()
  )
fig_path <- file.path(out_dir, "fig_correlation_parametres_ch4.png")
ggsave(fig_path, p, width = 9, height = 10, dpi = 150)

message("Emissions table:      ", file.path(out_dir, "enteric_paired_emissions.csv"))
message("Parameter draws:     ", file.path(out_dir, "enteric_paired_parameter_draws.csv"))
message("Correlation table:   ", file.path(out_dir, "enteric_paired_correlation.csv"))
message("Heatmap figure:      ", fig_path)

# Console summary: the 5 most influential parameters per variant.
for (v in variants) {
  s <- corr[corr$variant == v & corr$method == "spearman", ]
  s <- s[order(-abs(s$correlation)), ]
  cat("\n==", v, "==\n")
  print(head(s[, c("parameter", "correlation")], 5), row.names = FALSE)
}
