# analyse_paired_grid.R
#
# Multi-slot analysis of the paired grid written by run_case_study
# (step 5, engine.run_paired_grid): one Monte-Carlo parameter draw
# per iteration, shared by every swept column of the grid, so any
# column-wise comparison is free of sampling noise.
#
# Reads the LAST results.json entry carrying BOTH
# uncertainty$emissions_table AND uncertainty$combinations (the
# multi-slot contract; the single-slot enteric grid has
# uncertainty$variants instead and is handled by
# analyse_enteric_sensitivity.R).
#
#   uncertainty$emissions_table       one row per iteration with the
#                                     flat "<label>__<indicator>"
#                                     farm columns (gwp100, gwp20,
#                                     gwpstar, ch4_kg, co2_kg,
#                                     n2o_kg); a label is
#                                     "slot=variant" per swept slot,
#                                     joined by commas (factorial) or
#                                     one slot at a time (one-factor);
#   uncertainty$parameter_draws_table one row per iteration: the
#                                     drawn value of every parameter
#                                     (full traceability);
#   uncertainty$slots                  runnable variants per swept slot;
#   uncertainty$combinations           the column labels of the grid;
#   uncertainty$main_selection         the reference variant of each
#                                     swept slot;
#   uncertainty$mode                   "one_factor" or "full_factorial";
#   uncertainty$central_gwp_factors   central AR6 Table 7.15 factors
#                                     (inventory-only recomposition).
#
# Outputs (in R/ by default):
#   paired_grid_emissions.csv         the emissions table;
#   paired_grid_parameter_draws.csv   the parameter-draws table;
#   paired_grid_correlation.csv       long-form correlations
#                                     (parameter x column, Spearman +
#                                     Pearson on gwp100, error_type);
#   paired_grid_model_effect.csv     the paired difference of every
#                                     alternative against the
#                                     reference selection per swept
#                                     slot (mean, sd, p5, p95 per
#                                     indicator) — the pure effect
#                                     of each model choice;
#   paired_grid_variance_share.csv   share of the gwp100 variance
#                                     explained by each swept slot and
#                                     their interaction (paired
#                                     two-factor decomposition, sum
#                                     = 100 % when the factorial grid
#                                     identifies every term);
#   fig_effet_slots_gwp100.png       forest plot of the per-slot model
#                                     effects on gwp100;
#   fig_interaction_slots_gwp100.png interaction plot (mean gwp100 per
#                                     combination, only when two slots
#                                     are swept in full factorial).
#
# Usage:
#   Rscript R/analyse_paired_grid.R <results.json> [output_dir]
#
# Requires: jsonlite, ggplot2.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) {
  stop("Usage: Rscript R/analyse_paired_grid.R <results.json> [output_dir]")
}
json_path <- args[1]
out_dir <- if (length(args) >= 2) args[2] else "R"

suppressPackageStartupMessages({
  library(jsonlite)
  library(ggplot2)
})

doc <- fromJSON(json_path, simplifyVector = FALSE)
entries <- doc$simulations

# The multi-slot paired-grid entries carry uncertainty$combinations.
# Keep the LAST one (a rerun supersedes the previous one).
grid_entries <- Filter(function(e) {
  !is.null(e$uncertainty) &&
    !is.null(e$uncertainty$emissions_table) &&
    !is.null(e$uncertainty$combinations)
}, entries)
if (length(grid_entries) == 0) {
  stop("No paired-grid entry with 'combinations' in ", json_path,
       ". Declare [paired_grid] in the study card (or PairedGridConfig) ",
       "and run the study first.")
}
grid <- grid_entries[[length(grid_entries)]]
u <- grid$uncertainty

# Column labels: "slot=variant" joined by commas (multi-slot grids);
# bare variant names (single-slot grids run through run_paired_grid,
# whose slot is given by uncertainty$slot).bare_slot <- u$slot
emissions_rows <- u$emissions_table
params_rows <- u$parameter_draws_table
combinations <- unlist(u$combinations)
slots <- u$slots
slot_names <- names(slots)
main_selection <- u$main_selection
mode <- if (is.null(u$mode)) "one_factor" else u$mode

if (is.null(emissions_rows) || is.null(params_rows) ||
    length(combinations) == 0) {
  stop("Malformed paired-grid section (tables or combinations missing).")
}

# Flat rectangular tables: one row per iteration, one column per
# "<label>__<indicator>" (emissions) or per parameter (draws).
emissions <- do.call(rbind, lapply(emissions_rows, function(r) {
  as.data.frame(r, stringsAsFactors = FALSE)
}))
draws <- do.call(rbind, lapply(params_rows, function(r) {
  as.data.frame(r, stringsAsFactors = FALSE)
}))
stopifnot(nrow(emissions) == nrow(draws))

dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
write.csv(emissions, file.path(out_dir, "paired_grid_emissions.csv"),
          row.names = FALSE)
write.csv(draws, file.path(out_dir, "paired_grid_parameter_draws.csv"),
          row.names = FALSE)

# Parse a column label into a named selection {slot: variant}.
# Labels are "slot=variant" joined by commas (see run_paired_grid);
# bare variant names map to the single swept slot (uncertainty$slot).
parse_label <- function(label) {
  if (!grepl("=", label)) {
    if (is.null(bare_slot)) {
      stop("Column label without slot: ", label)
    }
    return(setNames(label, bare_slot))
  }
  parts <- strsplit(label, ",")[[1]]
  kv <- strsplit(parts, "=")
  sel <- vapply(kv, function(p) p[2], character(1))
  names(sel) <- vapply(kv, function(p) p[1], character(1))
  sel
}

# Reference column: the combination whose selection equals
# main_selection (it always belongs to the grid).
reference_label <- NULL
for (cl in combinations) {
  sel <- parse_label(cl)
  if (all(vapply(slot_names, function(s) {
    identical(sel[[s]], main_selection[[s]])
  }, logical(1)))) {
    reference_label <- cl
    break
  }
}
if (is.null(reference_label)) {
  stop("The reference selection is not among the grid combinations.")
}
indicators <- c("gwp100", "gwp20", "gwpstar",
                "ch4_kg", "co2_kg", "n2o_kg")

# ---------------------------------------------------------------------------
# 1. Parameter sensitivity: correlation of every drawn parameter with
#    the gwp100 of every column of the grid (long form, error_type).
# ---------------------------------------------------------------------------
methods <- c("pearson", "spearman")
pids <- setdiff(names(draws), "iteration")

corr <- do.call(rbind, unlist(lapply(methods, function(m) {
  lapply(combinations, function(cl) {
    y <- emissions[[paste0(cl, "__gwp100")]]
    sapply(pids, function(p) {
      x <- draws[[p]]
      if (length(unique(x)) < 2 || length(unique(y)) < 2) return(NA_real_)
      suppressWarnings(cor(x, y, method = m))
    }) -> r
    data.frame(parameter = pids, column = cl, method = m,
               correlation = as.numeric(r), stringsAsFactors = FALSE)
  })
}), recursive = FALSE))

# Error type: characterisation factors (gwp100_*/gwp20_*, the AR6
# Table 7.15 metrics — uncertainty of the kg CO2e conversion) vs
# inventory parameters (everything driving the kg of gas emitted).
corr$error_type <- ifelse(
  grepl("^gwp(100|20)_", corr$parameter),
  "characterisation", "inventory"
)
corr <- corr[order(-abs(corr$correlation)), ]
write.csv(corr, file.path(out_dir, "paired_grid_correlation.csv"),
          row.names = FALSE)

top_inventory <- head(corr[corr$error_type == "inventory" &
                              corr$method == "spearman", ], 5)
cat("\nTop-5 inventory parameters (Spearman, reference column gwp100):\n")
print(top_inventory[, c("parameter", "column", "correlation")],
      row.names = FALSE)

# ---------------------------------------------------------------------------
# 2. Per-slot model effect: paired difference of every alternative
#    against the reference selection (same draw, same iteration), per
#    indicator. In full factorial both columns exist in the grid; in
#    one-factor mode the swept column and the reference column are the
#    two evaluations of the same draw.
# ---------------------------------------------------------------------------
model_effect <- do.call(rbind, unlist(lapply(slot_names, function(s) {
  alternatives <- setdiff(unlist(slots[[s]]), main_selection[[s]])
  lapply(alternatives, function(v) {
    # The column whose selection differs from the reference ONLY on
    # slot s (one column per iteration share the same draw).
    alt_label <- NULL
    for (cl in combinations) {
      sel <- parse_label(cl)
      same_elsewhere <- all(vapply(
        setdiff(slot_names, s),
        function(o) identical(sel[[o]], main_selection[[o]]),
        logical(1)
      ))
      if (identical(sel[[s]], v) && same_elsewhere) {
        alt_label <- cl
        break
      }
    }
    if (is.null(alt_label)) next
    do.call(rbind, lapply(indicators, function(k) {
      alt <- emissions[[paste0(alt_label, "__", k)]]
      main <- emissions[[paste0(reference_label, "__", k)]]
      d <- alt - main
      data.frame(
        slot = s, variant = v, indicator = k,
        mean = mean(d), sd = stats::sd(d),
        p5 = stats::quantile(d, 0.05, names = FALSE),
        p95 = stats::quantile(d, 0.95, names = FALSE),
        stringsAsFactors = FALSE
      )
    }))
  })
}), recursive = FALSE))
write.csv(model_effect,
          file.path(out_dir, "paired_grid_model_effect.csv"),
          row.names = FALSE)

# Forest plot of the per-slot model effects on gwp100.
g100 <- model_effect[model_effect$indicator == "gwp100", ]
p_effect <- ggplot(
  g100,
  aes(x = mean, y = reorder(variant, mean), colour = slot)
) +
  geom_vline(xintercept = 0, linetype = "dashed", colour = "grey50") +
  geom_errorbarh(aes(xmin = p5, xmax = p95), height = 0.25) +
  geom_point(size = 2) +
  facet_wrap(~ slot, scales = "free_y", ncol = 1) +
  labs(
    title = "Effet pur du choix de modèle sur le GWP100 de la ferme",
    subtitle = paste0(
      "Différences appariées contre la sélection de référence (",
      reference_label, ") — un tirage partagé par colonne"
    ),
    x = "Δ GWP100 (kg CO2e/an)", y = NULL, colour = "Slot"
  ) +
  theme_minimal()
ggsave(file.path(out_dir, "fig_effet_slots_gwp100.png"),
       p_effect, width = 8, height = 2 + 2 * length(slot_names),
       dpi = 150)

# ---------------------------------------------------------------------------
# 3. Paired two-factor variance decomposition of the farm gwp100.
#    Every column of one iteration shares the same draw, so an ANOVA
#    on the (iteration x combination) long table attributes the
#    variance to the model CHOICES, not to the parameters. With two
#    swept slots in full factorial, the interaction term is
#    identified (does the effect of one choice depend on the other?).
# ---------------------------------------------------------------------------
long_rows <- do.call(rbind, lapply(seq_len(nrow(emissions)), function(i) {
  do.call(rbind, lapply(combinations, function(cl) {
    sel <- parse_label(cl)
    data.frame(
      iteration = emissions$iteration[i],
      gwp100 = emissions[[paste0(cl, "__gwp100")]][i],
      as.list(setNames(vapply(slot_names, function(s) sel[[s]],
                              character(1)), slot_names)),
      stringsAsFactors = FALSE
    )
  }))
}))

variance_share <- data.frame()
if (length(slot_names) == 2 && mode == "full_factorial" &&
    identical(sort(combinations),
              sort(as.vector(outer(
                unlist(slots[[slot_names[1]]]),
                unlist(slots[[slot_names[2]]]),
                function(a, b) paste0(
                  paste0(slot_names[1], "=", a), ",",
                  paste0(slot_names[2], "=", b)
                )
              ))))) {
  # Full factorial: the interaction is identified.
  fml <- stats::as.formula(paste(
    "gwp100 ~", paste(slot_names, collapse = " + "),
    "+", paste(slot_names, collapse = ":")
  ))
  fit <- stats::lm(fml, data = long_rows)
  a <- stats::anova(fit)
  ss <- a[, "Sum Sq"]
  variance_share <- data.frame(
    term = c(slot_names, paste(slot_names, collapse = ":"),
             "iteration_draws (residual)"),
    share_pct = 100 * ss / sum(ss),
    stringsAsFactors = FALSE
  )
} else {
  if (mode != "full_factorial") {
    warning(
      "one-factor grid: the slot interaction is not identifiable; ",
      "reporting the per-slot additive shares of the model-choice ",
      "variance only. Declare full_factorial = true in [paired_grid] ",
      "to identify the interaction."
    )
  } else {
    warning(
      "The factorial grid is incomplete (some variants were excluded); ",
      "falling back to the additive decomposition."
    )
  }
  fml <- stats::as.formula(paste("gwp100 ~",
                                 paste(slot_names, collapse = " + ")))
  fit <- stats::lm(fml, data = long_rows)
  a <- stats::anova(fit)
  ss <- a[, "Sum Sq"]
  variance_share <- data.frame(
    term = c(slot_names, "iteration_draws (residual)"),
    share_pct = 100 * ss / sum(ss),
    stringsAsFactors = FALSE
  )
}
write.csv(variance_share,
          file.path(out_dir, "paired_grid_variance_share.csv"),
          row.names = FALSE)
cat("\nVariance decomposition of the farm gwp100 ",
    "(model choices vs paired draws):\n")
print(variance_share, row.names = FALSE)

# Interaction plot (two swept slots, full factorial): mean gwp100 per
# combination — crossed lines reveal an interaction at first sight.
if (length(slot_names) == 2 && mode == "full_factorial") {
  agg <- stats::aggregate(
    as.formula(paste("gwp100 ~",
                     paste(slot_names, collapse = " + "))),
    data = long_rows, FUN = mean
  )
  names(agg)[3] <- "gwp100_mean"
  p_inter <- ggplot(agg, aes(
    x = .data[[slot_names[1]]], y = .data[["gwp100_mean"]],
    colour = .data[[slot_names[2]]], group = .data[[slot_names[2]]]
  )) +
    geom_point(size = 2) +
    geom_line(linewidth = 0.6) +
    labs(
      title = "Interaction entre les choix de modèles (gwp100 moyen)",
      x = paste0("Variante — ", slot_names[1]),
      y = "GWP100 moyen (kg CO2e/an)",
      colour = paste0("Variante — ", slot_names[2])
    ) +
    theme_minimal()
  ggsave(file.path(out_dir, "fig_interaction_slots_gwp100.png"),
         p_inter, width = 7, height = 4.5, dpi = 150)
}

cat("\nOutputs written to: ", normalizePath(out_dir), "\n", sep = "")
