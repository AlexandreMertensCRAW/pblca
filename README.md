# PBLCA — Process-Based LCA Engine (ACV par processus)

Moteur d'ACV (Analyse de Cycle de Vie) processuelle pour fermes en polyculture-élevage,
conforme ISO 14040/14044, avec propagation d'incertitudes par Monte-Carlo, registre de
modèles (Tier-2 / Tier-3 interchangeables) et traçabilité des références bibliographiques.

## Architecture en trois couches (exigence ISO 14044)

```
┌─────────────────────────────────────────────────────────────────┐
│  COUCHE 3 — IMPACTS                                             │
│  pblca/impacts.py : GWP100, GWP20, GWP* (Cain et al. 2019)       │
├─────────────────────────────────────────────────────────────────┤
│  COUCHE 2 — FLUX DE GAZ                                         │
│  pblca/gases.py : registre CH4 / CO2 / N2O (inventaire),         │
│  chaque émission tracée (source, équation, référence)            │
├─────────────────────────────────────────────────────────────────┤
│  COUCHE 1 — PROCESSUS AGRONOMIQUES                              │
│  pblca/processes/*.py : entérique (Tier-2/Tier-3), fumier,       │
│  sol N2O, carbone du sol, achats (animaux/aliments/engrais),     │
│  travaux mécaniques                                              │
└─────────────────────────────────────────────────────────────────┘
        ▲                ▲                    ▲
        │                │                    │
  pblca/params.py   pblca/registry.py   pblca/engine.py
  (paramètres       (registre de        (orchestration,
   traçables +       modèles,            stockage JSON)
   incertitudes)     interface
                     universelle)

Sous-couche d'échantillonnage de la couche 1 : pblca/mc.py
(génération des valeurs Monte-Carlo — tirages, perturbation des
rations mesurées, idempotence) ; les processus de pblca/processes/
restent des évaluateurs purs et l'engine en reste la façade.
```

## Installation

```bash
cd pblca
pip install -e ".[dev]"
```

## Démarrage rapide

```python
from pblca.case_study import build_case_study_farm
from pblca.engine import LCAEngine

engine = LCAEngine(datastore_path="results.json")
farm = build_case_study_farm(engine.params)

# Valeur centrale (variantes par défaut), avec sélection de modèles :
result = engine.run(farm, model_selection={"enteric_ch4": "tier2_2006"})

# Variante Tier-3 (Mills et al. 2003) pour tester l'équation alternative :
result_t3 = engine.run(farm, model_selection={"enteric_ch4": "tier3_mills"})

# Propagation des incertitudes par Monte-Carlo (500 itérations) :
mc = engine.run_monte_carlo(farm, n_iterations=500, seed=2024)

# Sauvegarde JSON (une entrée par simulation) :
engine.datastore.save("results.json")
```

## Card d'étude déclarative (TOML) et CLI

Une étude complète (ferme, variantes de modèles, plan Monte-Carlo,
sortie) se décrit dans **un seul fichier TOML** — la « card », sur le
modèle des frameworks de simulation rapide : zéro code utilisateur.

```bash
pblca run cards/studies/ferme_20ha.toml  # exécute l'étude décrite par la card
pblca slots                              # slots + variantes disponibles
pblca slots --slot enteric_ch4           # variantes d'un slot
pblca farms                              # fermes intégrées utilisables
```

Structure d'une card étude (voir `cards/studies/ferme_20ha.toml`) :

```toml
[study]
name = "ferme_20ha"
farm = "ferme_20ha"        # carte ferme (cards/farms/), chemin .toml, ou [farm] en ligne

[model_selection]           # variantes du run central
enteric_ch4 = "tier2_2006_modelled_ingestion"

[variant_grid]             # sweeps mono-slot (run central + Monte-Carlo)
enteric_ch4 = ["tier2_2006_modelled_ingestion", "tier3_mills_modelled_ingestion"]

[monte_carlo]
n_iterations = 500
seed = 2024

[datastore]
path = "results.json"
```

**Carte ferme** (`cards/farms/ferme_20ha.toml`) : la ferme complète —
troupeau, parcelles, achats, mesures on-farm (AHCS, rations, dMO/dMOd),
**événements de pâturage datés** (`[[farm.parcels.grazing]]` : lot +
dates d'entrée/sortie — la part pâturage du fumier en est dérivée et le
N déposé est routé vers la parcelle pâturée) et **fertilisations datées
par parcelle** (`[[farm.parcels.synthetic_fertilisation]]` /
`[[farm.parcels.organic_fertilisation]]` : vecteurs d'événements
`date` + `n_kg`, plusieurs apports par an). Le fumier solide épandu
est déclaré explicitement ; le moteur vérifie sa cohérence avec le
fumier disponible (diagnostic si écart > 25 %). Une étude = une paire
de cartes.

La card est validée au chargement (slot/variante inconnus → erreur
explicite immédiate). Le fichier de résultats embarque la card
(hachage SHA-256 + texte brut) : chaque étude reste traçable et
reproductible (ISO 14044 §4.5). Les variantes exigeant des mesures que
la ferme ne possède pas sont automatiquement exclues (avec avertissement).

## Documentation auto-générée

```bash
pdoc pblca -o docs_html          # HTML
pdoc pblca                       # serve le HTML localement
```

## Cycle de vie de `results.json`

Chaque exécution démarre sur un fichier vierge : un éventuel
`results.json` précédent est automatiquement déplacé vers
`results_archive/results_<horodatage>.json` (aucune perte de
résultats, ISO 14044 §4.5 — les figures R ne peuvent donc jamais
mixer deux exécutions). Le répertoire `results_archive/` est ignoré
par git, comme `results.json`. Pour retrouver le comportement
d'accumulation d'autrefois : `DataStore(path, archive_previous=False)`.

## Tests

```bash
pytest -q
```

`tests/test_r_contract.py` verrouille le contrat Python → R : les
clés JSON réellement consommées par les scripts `R/plot_*.R`
(`uncertainty.enteric_ch4_per_group_kg`, `..._per_group_g_day`,
`manure_ch4_by_system_kg`, `enteric_ch4_samples`,
`model_selection.<slot>.variant`, convention `sim_id`, appariement
des échantillons) sont garanties présentes avec la structure attendue.

## Cas d'étude

Ferme de 20 ha : 13 ha de prairie permanente (engraissement de veaux mâles
achetés à 50 kg, engraissés jusqu'à 600 kg avec herbe, concentrés et
co-produits) + 7 ha en rotation pomme de terre → colza → épeautre → avoine →
2 ans de prairie temporaire. Animaux répartis en 3 catégories d'âge
(0–6 mois, 6–12 mois, 12–21 mois).

Voir `pblca/case_study.py` et `run_case_study.py`.
