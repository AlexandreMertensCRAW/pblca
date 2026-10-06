# analyse_enteric_sensitivity.R
#
# Parameter-to-emissions sensitivity analysis of the enteric-CH4
# model variants, from the paired variant grid written by
# run_case_study.py (run_paired_variant_grid):
#
#   uncertainty$emissions_table       one row per Monte-Carlo
#                                     iteration with the flat
#                                     "<variant>__<indicator>" farm
#                                     columns (gwp100, gwp20,
#                                     gwpstar, ch4_kg, co2_kg,
#                                     n2o_kg); every variant of a
#                                     row shares the SAME parameter
#                                     draw (paired columns);
#   uncertainty$parameter_draws_table one row per iteration: the
#                                     drawn value of every parameter
#                                     (full traceability);
#   uncertainty$main_variant          the reference variant; every
#                                     alternative is also analysed
#                                     as a paired difference against
#                                     it (same draw, same iteration:
#                                     the pure model-choice effect
#                                     on the farm result).
#
# Outputs (in R/ by default):
#   enteric_paired_emissions.csv     the emissions table;
#   enteric_paired_parameter_draws.csv  the parameter-draws table;
#   enteric_paired_correlation.csv   the long-form correlation table
#                                   (parameter x variant, Spearman +
#                                   Pearson on the MAIN variant's
#                                   gwp100), sorted by |rho|;
#   enteric_paired_model_effect.csv  the paired difference of every
#                                   alternative against the main
#                                   variant (mean, sd, p5, p95 per
#                                   indicator) — the pure
#                                   model-choice effect;
#   fig_correlation_parametres_ch4.png  heatmap of the Spearman
#                                   correlations (parameters x
#                                   variants, gwp100);
#   fig_effet_modeles_gwp100.png     the paired model effects on
#                                   gwp100 (mean +/- sd of the
#                                   paired difference per
#                                   alternative).
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
main_variant <- grid$uncertainty$main_variant
if (is.null(main_variant)) main_variant <- variants[1]
if (is.null(emissions_rows) || is.null(params_rows) || length(variants) == 0) {
  stop("Malformed paired-grid section (tables or variants missing).")
}

# Flat rectangular tables: one row per iteration, one column per
# "<variant>__<indicator>" (emissions) or per parameter (draws).
emissions <- do.call(rbind, lapply(emissions_rows, function(r) {
  row <- as.data.frame(r, stringsAsFactors = FALSE)
  row
}))
draws <- do.call(rbind, lapply(params_rows, function(r) {
  row <- as.data.frame(r, stringsAsFactors = FALSE)
  row
}))
stopifnot(nrow(emissions) == nrow(draws))

dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
write.csv(emissions, file.path(out_dir, "enteric_paired_emissions.csv"),
          row.names = FALSE)
write.csv(draws, file.path(out_dir, "enteric_paired_parameter_draws.csv"),
          row.names = FALSE)

# ---------------------------------------------------------------------------
# 1. Parameter sensitivity: correlation of every drawn parameter with
#    the GWP100 of the main variant (and of every alternative).
# ---------------------------------------------------------------------------
methods <- c("pearson", "spearman")
pids <- setdiff(names(draws), "iteration")
main_col <- emissions[[paste0(main_variant, "__gwp100")]]
corr <- do.call(rbind, unlist(lapply(methods, function(m) {
  lapply(variants, function(v) {
    y <- emissions[[paste0(v, "__gwp100")]]
    sapply(pids, function(p) {
      x <- draws[[p]]
      if (length(unique(x)) < 2 || length(unique(y)) < 2) return(NA_real_)
      suppressWarnings(cor(x, y, method = m))
    }) -> r
    data.frame(parameter = pids, variant = v, method = m,
               correlation = as.numeric(r), stringsAsFactors = FALSE)
  })
}), recursive = FALSE))
# Error type: characterisation factors (gwp100_*/gwp20_*, the AR6
# Table 7.15 metrics — uncertainty of the kg CO2e conversion) vs
# inventory parameters (everything driving the kg of gas emitted).
is_cf <- grepl("^gwp(100|20)_", corr$parameter)
corr$error_type <- ifelse(is_cf, "characterisation", "inventory")
corr <- corr[order(corr$variant, corr$method, -abs(corr$correlation)), ]
write.csv(corr, file.path(out_dir, "enteric_paired_correlation.csv"),
          row.names = FALSE)

# Heatmap of the Spearman correlations (robust to the lognormal
# draws): parameters in rows, variants in columns.
heat <- corr[corr$method == "spearman", ]
heat$parameter <- factor(heat$parameter,
                        levels = heat$parameter[order(heat$correlation[heat$variant == main_variant])])
heat$variant <- factor(heat$variant, levels = variants)
heat$error_type <- factor(heat$error_type, levels = c("inventory", "characterisation"))
p <- ggplot(heat, aes(x = variant, y = parameter, fill = correlation)) +
  geom_tile() +
  scale_fill_gradient2(low = "#2166ac", mid = "white", high = "#b2182b",
                       midpoint = 0, limits = c(-1, 1), na.value = "grey90") +
  facet_grid(error_type ~ ., scales = "free_y", space = "free_y") +
  labs(
    title = "Parameter-to-emissions sensitivity of the enteric CH4 models",
    subtitle = paste0("Spearman correlation of the drawn parameters with the ",
                      "farm GWP100; n = ", nrow(emissions), " paired iterations. ",
                      "Top panel: inventory errors (kg of gas emitted); ",
                      "bottom panel: characterisation errors (AR6 Table 7.15 ",
                      "conversion factors)"),
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

# ---------------------------------------------------------------------------
# 2. Model-choice effect: paired difference of every alternative
#    against the main variant, per farm indicator.
# ---------------------------------------------------------------------------
indicators <- c("gwp100", "gwp20", "gwpstar", "ch4_kg", "co2_kg", "n2o_kg")
alts <- setdiff(variants, main_variant)
effect <- do.call(rbind, lapply(alts, function(v) {
  do.call(rbind, lapply(indicators, function(k) {
    y <- emissions[[paste0(v, "__", k)]]
    x <- emissions[[paste0(main_variant, "__", k)]]
    d <- y - x
    data.frame(
      variant = v, indicator = k,
      mean = mean(d), sd = sd(d),
      p5 = quantile(d, 0.05), p95 = quantile(d, 0.95),
      stringsAsFactors = FALSE
    )
  }))
}))
write.csv(effect, file.path(out_dir, "enteric_paired_model_effect.csv"),
          row.names = FALSE)

# Figure: the paired model effects on gwp100 (mean +/- sd).
g100 <- effect[effect$indicator == "gwp100", ]
g100$variant <- factor(g100$variant, levels = g100$variant[order(g100$mean)])
p2 <- ggplot(g100, aes(x = variant, y = mean)) +
  geom_hline(yintercept = 0, linetype = "dashed", colour = "grey40") +
  geom_pointrange(aes(ymin = mean - sd, ymax = mean + sd),
                  colour = "steelblue", size = 0.4) +
  coord_flip() +
  labs(
    title = "Pure model-choice effect on the farm GWP100",
    subtitle = paste0("Paired difference against '", main_variant,
                      "' (same parameter draws); n = ",
                      nrow(emissions), " iterations"),
    x = NULL, y = "GWP100 difference vs main variant (kg CO2e/yr)"
  ) +
  theme_bw()
fig2_path <- file.path(out_dir, "fig_effet_modeles_gwp100.png")
ggsave(fig2_path, p2, width = 9, height = 6, dpi = 150)

message("Emissions table:      ", file.path(out_dir, "enteric_paired_emissions.csv"))
message("Parameter draws:      ", file.path(out_dir, "enteric_paired_parameter_draws.csv"))
message("Correlation table:   ", file.path(out_dir, "enteric_paired_correlation.csv"))
message("Model-effect table:   ", file.path(out_dir, "enteric_paired_model_effect.csv"))
message("Heatmap figure:       ", fig_path)
message("Model-effect figure:  ", fig2_path)

# Console summary: the 5 most influential parameters of the MAIN
# variant and the model effects on gwp100.
cat("\n== Top parameters (main variant:", main_variant, ", GWP100) ==\n")
s <- corr[corr$variant == main_variant & corr$method == "spearman", ]
for (etype in c("inventory", "characterisation")) {
  cat("\n--", etype, "error --\n")
  se <- s[s$error_type == etype, ]
  print(head(se[order(-abs(se$correlation)), c("parameter", "correlation")], 5),
        row.names = FALSE)
}
cat("\n== Pure model-choice effect on GWP100 (vs", main_variant, ") ==\n")
print(g100[, c("variant", "mean", "sd")], row.names = FALSE)
