# plot_ration_comparison_correlation.R
#
# Correlation between the paired Monte-Carlo draws of the two
# ration-definition modes of the enteric CH4:
#   x = tier2_2006_modelled_ingestion (IPCC energy chain)
#   y = tier2_2006_ingestion_measured (on-farm measured DMI)
#
# Both evaluations of an iteration share the same parameter draw
# (same Ym, same emission factors, same farm data), so the scatter
# shows how much of the uncertainty is COMMON to the two chains:
# a tight cloud along the 1:1 line means the two modes deviate
# together (highly correlated errors); a diffuse cloud means the
# ration information changes the deviations independently.
#
# Reads `uncertainty$enteric_ch4_samples` of the ration-comparison
# entry written by run_case_study.py (run_ration_comparison): for
# each mode, the per-iteration enteric CH4 per animal group and the
# farm total.
#
# Usage:
#   Rscript R/plot_ration_comparison_correlation.R <results.json> [output.png]
#
# Requires: jsonlite, ggplot2.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 1) {
  stop("Usage: Rscript R/plot_ration_comparison_correlation.R <results.json> [output.png]")
}
json_path <- args[1]
out_png <- if (length(args) >= 2) args[2] else "R/fig_correlation_rations.png"

suppressPackageStartupMessages({
  library(jsonlite)
  library(ggplot2)
})

# Single JSON document: {"format_version": ..., "simulations": [entry, ...]}
doc <- fromJSON(json_path, simplifyVector = FALSE)
entries <- doc$simulations

# The ration-comparison entry carries the paired per-iteration
# enteric CH4 samples (uncertainty$enteric_ch4_samples).
cmp_entries <- Filter(function(e) {
  !is.null(e$uncertainty) && !is.null(e$uncertainty$enteric_ch4_samples)
}, entries)
if (length(cmp_entries) == 0) {
  stop("No ration-comparison entry with 'enteric_ch4_samples' in ", json_path,
       ". Run run_case_study.py first.")
}
# Keep the LAST entry (a rerun supersedes the previous one).
cmp <- cmp_entries[[length(cmp_entries)]]
samples <- cmp$uncertainty$enteric_ch4_samples

ipcc <- samples$ipcc_equations
meas <- samples$measured
if (is.null(ipcc) || is.null(meas)) {
  stop("Malformed enteric_ch4_samples section (modes missing).")
}

# Build one data.frame row per (iteration, group): paired x/y draws.
keys <- intersect(names(ipcc), names(meas))
rows <- list()
i <- 1
for (key in keys) {
  x <- unlist(ipcc[[key]])
  y <- unlist(meas[[key]])
  n <- min(length(x), length(y))
  if (n == 0) next
  r <- suppressWarnings(cor(x[seq_len(n)], y[seq_len(n)],
                            method = "pearson", use = "complete.obs"))
  rows[[length(rows) + 1]] <- data.frame(
    lot = key,
    iteration = seq_len(n),
    modelled = x[seq_len(n)],
    measured = y[seq_len(n)],
    pearson = r
  )
}
df <- do.call(rbind, rows)

# Facet titles with the Pearson correlation coefficient per panel.
lot_levels <- keys[keys %in% df$lot]
lab <- vapply(lot_levels, function(k) {
  r <- unique(df$pearson[df$lot == k])
  if (length(r) != 1 || is.na(r)) return(k)
  paste0(k, "  (r = ", sprintf("%.2f", r), ")")
}, character(1))
df$lot <- factor(df$lot, levels = lot_levels)

# Farm total first, then the age classes (stable, meaningful order).
total_first <- c("farm_total", setdiff(lot_levels, "farm_total"))
df$lot <- factor(df$lot, levels = total_first)
lab <- lab[total_first]

p <- ggplot(df, aes(x = modelled, y = measured)) +
  geom_abline(slope = 1, intercept = 0, linetype = "dashed",
              colour = "grey40") +
  geom_point(alpha = 0.3, size = 1.2, colour = "steelblue") +
  facet_wrap(~ lot, scales = "free",
             labeller = as_labeller(lab)) +
  labs(
    title = "Paired Monte-Carlo draws: IPCC-chain vs measured-ration enteric CH4",
    subtitle = paste0("Same parameter draw on both axes; dashed line = 1:1. ",
                      "n = ", length(unique(df$iteration)),
                      " paired iterations"),
    x = "CH4, IPCC energy chain (kg per year)",
    y = "CH4, measured ration (kg per year)"
  ) +
  theme_bw() +
  theme(
    panel.background = element_rect(fill = "white", colour = NA),
    panel.grid.minor = element_blank()
  )

ggsave(out_png, p, width = 9, height = 7, dpi = 150)
message("Figure saved: ", out_png)
