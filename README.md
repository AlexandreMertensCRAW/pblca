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
   traçables +       modèles,            Monte-Carlo,
   incertitudes)     interface           stockage JSON)
                     universelle)
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

## Documentation auto-générée

```bash
pdoc pblca -o docs_html          # HTML
pdoc pblca                       # serve le HTML localement
```

## Tests

```bash
pytest -q
```

## Cas d'étude

Ferme de 20 ha : 13 ha de prairie permanente (engraissement de veaux mâles
achetés à 50 kg, engraissés jusqu'à 600 kg avec herbe, concentrés et
co-produits) + 7 ha en rotation pomme de terre → colza → épeautre → avoine →
2 ans de prairie temporaire. Animaux répartis en 3 catégories d'âge
(0–6 mois, 6–12 mois, 12–21 mois).

Voir `pblca/case_study.py` et `run_case_study.py`.
