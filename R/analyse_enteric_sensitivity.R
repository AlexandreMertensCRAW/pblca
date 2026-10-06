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
#                                   indicator; for gwp100 also the
#                                   inventory-only recomposition with
#                                   the exported central AR6 factors)
#                                   — the pure model-choice effect;
#   fig_correlation_parametres_ch4.png  heatmap of the Spearman
#                                   correlations (parameters x
#                                   variants, gwp100);
#   fig_effet_modeles_gwp100.png     the paired model effects on
#                                   gwp100: two error bars per
#                                   alternative — total error (drawn
#                                   characterisation factors) and
#                                   inventory-only error (central
#                                   factors); their gap is the
#                                   characterisation contribution;
#   enteric_variance_decomposition.csv  exact covariance
#                                   decomposition of the farm GWP100
#                                   of the MAIN variant: one row per
#                                   source x gas term, split into
#                                   inventory and characterisation
#                                   errors (contributions sum to 100%);
#   fig_decomposition_variance_gwp100.png  bar chart of these
#                                   contributions (which source, and
#                                   which GWP factor, drives the
#                                   uncertainty of the farm result).
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
#    against the main variant, per farm indicator. For gwp100 the
#    difference is also recomposed from the gas columns with the
#    CENTRAL characterisation factors (exported by the engine): that
#    strips the GWP-factor uncertainty, leaving the inventory-only
#    error, while the tabulated "gwp100" columns carry the TOTAL
#    error (drawn factors).
# ---------------------------------------------------------------------------
indicators <- c("gwp100", "gwp20", "gwpstar", "ch4_kg", "co2_kg", "n2o_kg")
alts <- setdiff(variants, main_variant)
# Central AR6 Table 7.15 factors exported by run_paired_variant_grid.
cf <- grid$uncertainty$central_gwp_factors
have_cf <- !is.null(cf) && !is.null(cf$gwp100_ch4_biogenic) &&
  !is.null(cf$gwp100_n2o)
if (!have_cf) {
  warning("central_gwp_factors missing: regenerate results.json with ",
          "run_case_study.py (older paired-grid entry). ",
          "The figure will show the total error only.")
}
cf_ch4 <- if (have_cf) as.numeric(cf$gwp100_ch4_biogenic) else NA_real_
cf_n2o <- if (have_cf) as.numeric(cf$gwp100_n2o) else NA_real_
effect <- do.call(rbind, lapply(alts, function(v) {
  do.call(rbind, lapply(indicators, function(k) {
    y <- emissions[[paste0(v, "__", k)]]
    x <- emissions[[paste0(main_variant, "__", k)]]
    d <- y - x
    mean_io <- NA_real_; sd_io <- NA_real_;
    p5_io <- NA_real_; p95_io <- NA_real_
    if (k == "gwp100" && have_cf) {
      # Inventory-only recomposition: central factors on the paired
      # gas differences (CO2 factor is 1 by definition).
      d_gas <- (emissions[[paste0(v, "__ch4_kg")]] -
                  emissions[[paste0(main_variant, "__ch4_kg")]]) * cf_ch4 +
               (emissions[[paste0(v, "__n2o_kg")]] -
                  emissions[[paste0(main_variant, "__n2o_kg")]]) * cf_n2o +
               (emissions[[paste0(v, "__co2_kg")]] -
                  emissions[[paste0(main_variant, "__co2_kg")]])
      mean_io <- mean(d_gas); sd_io <- sd(d_gas)
      p5_io <- quantile(d_gas, 0.05); p95_io <- quantile(d_gas, 0.95)
    }
    data.frame(
      variant = v, indicator = k,
      mean = mean(d), sd = sd(d),
      p5 = quantile(d, 0.05), p95 = quantile(d, 0.95),
      mean_inventory_only = mean_io, sd_inventory_only = sd_io,
      p5_inventory_only = p5_io, p95_inventory_only = p95_io,
      stringsAsFactors = FALSE
    )
  }))
}))
write.csv(effect, file.path(out_dir, "enteric_paired_model_effect.csv"),
          row.names = FALSE)

# Figure: the paired model effects on gwp100 (mean +/- sd). Two error
# bars per alternative when the central factors are available: the
# total error (drawn factors) and the inventory-only error (central
# factors); their gap is the characterisation contribution.
g100 <- effect[effect$indicator == "gwp100", ]
g100$variant <- factor(g100$variant, levels = g100$variant[order(g100$mean)])
g100_long <- do.call(rbind, lapply(seq_len(nrow(g100)), function(i) {
  rows <- data.frame(
    variant = g100$variant[i],
    error_bar = c("total (incl. characterisation)", "inventory only"),
    mean = c(g100$mean[i], g100$mean_inventory_only[i]),
    sd = c(g100$sd[i], g100$sd_inventory_only[i]),
    stringsAsFactors = FALSE
  )
  rows[!is.na(rows$sd), ]
}))
g100_long$error_bar <- factor(g100_long$error_bar,
  levels = c("total (incl. characterisation)", "inventory only"))
p2 <- ggplot(g100_long, aes(x = variant, y = mean, colour = error_bar)) +
  geom_hline(yintercept = 0, linetype = "dashed", colour = "grey40") +
  geom_pointrange(aes(ymin = mean - sd, ymax = mean + sd,
                      linetype = error_bar), size = 0.4,
                  position = position_dodge(width = 0.5)) +
  coord_flip() +
  scale_colour_manual(values = c("total (incl. characterisation)" = "steelblue",
                                 "inventory only" = "darkorange2"),
                      name = NULL) +
  scale_linetype_manual(values = c("total (incl. characterisation)" = "solid",
                                  "inventory only" = "dotted"),
                        name = NULL) +
  labs(
    title = "Pure model-choice effect on the farm GWP100",
    subtitle = paste0("Paired difference against '", main_variant,
                      "' (same parameter draws); n = ",
                      nrow(emissions), " iterations. Total = drawn AR6 ",
                      "factors; inventory only = central Table 7.15 ",
                      "factors (their gap = characterisation error)"),
    x = NULL, y = "GWP100 difference vs main variant (kg CO2e/yr)"
  ) +
  theme_bw() +
  theme(legend.position = "top")
fig2_path <- file.path(out_dir, "fig_effet_modeles_gwp100.png")
ggsave(fig2_path, p2, width = 9, height = 6, dpi = 150)

# ---------------------------------------------------------------------------
# 3. Variance decomposition of the farm GWP100 of the MAIN variant
#    by emission source, splitting inventory vs characterisation
#    errors. Exact covariance decomposition: each term's
#    contribution = Cov(term, total) / Var(total); the contributions
#    sum to 100% (no approximation, no independence assumption).
# ---------------------------------------------------------------------------
sg_cols <- unlist(grid$uncertainty$source_gas_columns)
have_sg <- !is.null(sg_cols) && length(sg_cols) > 0 && have_cf
if (have_sg) {
  gwp100_main <- emissions[[paste0(main_variant, "__gwp100")]]
  var_total <- var(gwp100_main)
  stopifnot(var_total > 0)
  # Terms: for each source x gas, the central-factor part (the
  # inventory term) and, for CH4/N2O, the factor-uncertainty part
  # (kg x drawn factor - central factor). CO2 factor is 1 by
  # definition, so no characterisation term for CO2.
  f_ch4 <- draws[["gwp100_ch4_biogenic"]]
  f_n2o <- draws[["gwp100_n2o"]]
  # Build the per-iteration term matrix (n_iterations x n_terms)
  # and its metadata: for CH4/N2O sources, TWO terms — the central-
  # factor part (inventory) and the factor-deviation part
  # (characterisation); CO2 has no characterisation term.
  term_cols <- list(); meta <- list()
  add_term <- function(src, gas, err, values) {
    term_cols[[length(term_cols) + 1]] <<- values
    meta[[length(meta) + 1]] <<- data.frame(
      source = src, gas = gas, error_type = err,
      stringsAsFactors = FALSE)
  }
  for (col in sg_cols) {
    parts <- strsplit(col, "__")[[1]]
    src <- parts[1]; gas <- sub("_kg$", "", parts[2])
    kg <- emissions[[col]]
    if (identical(gas, "ch4")) {
      add_term(src, gas, "inventory", kg * cf_ch4)
      add_term(src, gas, "characterisation", kg * (f_ch4 - cf_ch4))
    } else if (identical(gas, "n2o")) {
      add_term(src, gas, "inventory", kg * cf_n2o)
      add_term(src, gas, "characterisation", kg * (f_n2o - cf_n2o))
    } else {
      add_term(src, gas, "inventory", kg)
    }
  }
  meta <- do.call(rbind, meta)
  terms_mat <- do.call(cbind, term_cols)
  colnames(terms_mat) <- seq_len(ncol(terms_mat))
  # Exact covariance decomposition: Cov(term, total) per column.
  covs <- cov(terms_mat, gwp100_main)
  decomp <- cbind(meta,
    covariance = as.numeric(covs),
    contribution_pct = 100 * as.numeric(covs) / var_total)
  decomp$label <- paste0(decomp$source, " (", decomp$gas, ", ",
                         decomp$error_type, ")")
  decomp <- decomp[order(-abs(decomp$contribution_pct)), ]
  write.csv(decomp, file.path(out_dir, "enteric_variance_decomposition.csv"),
            row.names = FALSE)
  # Figure: contributions in %, one bar per unique term label.
  decomp$label <- factor(decomp$label,
    levels = decomp$label[order(-abs(decomp$contribution_pct))])
  p3 <- ggplot(decomp, aes(x = label, y = contribution_pct,
                           fill = error_type)) +
    geom_hline(yintercept = 0, colour = "grey40") +
    geom_col() +
    coord_flip() +
    scale_fill_manual(
      values = c("inventory" = "steelblue",
                 "characterisation" = "darkorange2"),
      name = NULL) +
    labs(
      title = "Variance decomposition of the farm GWP100 by error source",
      subtitle = paste0("Main variant '", main_variant, "'; exact covariance ",
                        "decomposition, contributions sum to 100%; n = ",
                        nrow(emissions), " iterations"),
      x = NULL, y = "Contribution to Var(GWP100) (%)",
      fill = NULL
    ) +
    theme_bw() +
    theme(legend.position = "top")
  fig3_path <- file.path(out_dir,
                         "fig_decomposition_variance_gwp100.png")
  ggsave(fig3_path, p3, width = 9, height = 6, dpi = 150)
  message("Variance decomposition: ",
          file.path(out_dir, "enteric_variance_decomposition.csv"))
  message("Decomposition figure:  ", fig3_path)
  # Console summary: the contributions per block.
  cat("\n== GWP100 variance decomposition (main variant:", main_variant,
      ") ==\n")
  for (etype in c("inventory", "characterisation")) {
    cat("\n--", etype, "error --\n")
    de <- decomp[decomp$error_type == etype, ]
    print(head(de[, c("label", "contribution_pct")], 10), row.names = FALSE)
  }
  cat("\nSum of all contributions: ",
      round(sum(decomp$contribution_pct), 2), "%\n")
} else {
  message("source_gas_columns or central_gwp_factors missing: variance ",
          "decomposition skipped (regenerate results.json).")
}

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
