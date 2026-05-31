"""
taxonomy.py — Load user-editable taxonomy.yaml from project root.

Falls back to built-in defaults if the file is missing or malformed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Project root: two levels above this file (lib/zotero-restructuring/)
_PROJECT_ROOT = Path(__file__).parent.parent
_TAXONOMY_PATH = _PROJECT_ROOT / "taxonomy.yaml"

# ── Built-in defaults ─────────────────────────────────────────────────────────

_DEFAULT_METHOD_VOCAB: list[str] = [
    # Experimental
    "patch-clamp", "extracellular recording", "calcium imaging", "2-photon imaging",
    "optogenetics", "chemogenetics", "EEG", "MEG", "fMRI", "psychophysics",
    "pharmacology", "in-vivo", "in-vitro", "lesion", "transcranial magnetic stimulation",
    "single-cell sequencing",
    # Computational
    "spiking network", "rate model", "mean-field theory", "dynamical systems",
    "reinforcement learning", "deep learning", "bayesian inference", "information theory",
    "dimensionality reduction", "attractor network",
    # Analysis
    "spike sorting", "decoding", "regression", "clustering", "spectral analysis",
    "causal inference", "data-analysis",
]

_DEFAULT_FIELD_TAXONOMY: list[str] = [
    "area/auditory-cortex", "area/visual-cortex", "area/prefrontal-cortex",
    "area/somatosensory-cortex", "area/hippocampus", "area/thalamus",
    "area/basal-ganglia", "area/cerebellum", "area/brainstem",
    "model/spiking-network", "model/rate-model", "model/mean-field",
    "model/dendritic-computation", "model/attractor-dynamics", "model/sequence-learning",
    "model/deep-learning", "model/bayesian-inference", "model/reservoir-computing",
    "model/reinforcement-learning",
    "plasticity/stdp", "plasticity/ltp-ltd", "plasticity/hebbian",
    "plasticity/homeostatic", "plasticity/structural", "plasticity/neuromodulation",
    "memory/working-memory", "memory/long-term", "memory/associative",
    "memory/episodic", "memory/sequential",
    "dynamics/oscillation", "dynamics/synchrony", "dynamics/ei-balance",
    "dynamics/up-down-states", "dynamics/population-coding", "dynamics/gain-modulation",
    "dynamics/sparse-coding",
    "language/speech-perception", "language/word-recognition", "language/phonetics",
    "language/syntax", "language/auditory-streaming", "language/lexical-access",
    "cognition/attention", "cognition/decision-making", "cognition/perception",
    "cognition/prediction-error", "cognition/cognitive-control",
    "neuron/dendritic-integration", "neuron/action-potential-generation",
    "neuron/interneuron-types", "neuron/pyramidal-cell", "neuron/calcium-dynamics",
    "theory/information-theory", "theory/dynamical-systems", "theory/statistical-mechanics",
    "theory/dimensionality-reduction", "theory/graph-theory",
]

_DEFAULT_SYSTEM_PROMPT = """\
You are a research librarian tagging academic papers.

You will receive a list of papers, a tag vocabulary, and a field taxonomy.
For each paper return a JSON object with:

1. freeform_tags — 3 to 8 specific tags that best describe THIS paper without
   consulting the vocabulary. Lowercase, 2–4 words. Be specific to the paper's
   actual contribution.

2. vocab_tags — 5 to 12 tags selected from the vocabulary that apply to this
   paper. Each has a score: your confidence it applies (1.0 = certain, 0.5 =
   plausible, 0.2 = speculative). Prefer specific sparse tags over generic ones.

3. methods — pick ALL that apply from: {method_vocab}
   Empty list if none apply.

4. fields — pick 1–3 entries from the field taxonomy exactly as written:
   {field_taxonomy}

Return a JSON object with a "results" key containing one entry per paper:
{{
  "results": [
    {{
      "item_id": <int>,
      "freeform_tags": ["auditory streaming", "gap detection threshold"],
      "vocab_tags": [{{"name": "working memory", "score": 0.9}}, ...],
      "methods": ["electrophysiology", "spiking network"],
      "fields": ["area/auditory-cortex", "model/spiking-network"]
    }},
    ...
  ]
}}
Include ALL papers in "results". Return ONLY the JSON object, no prose.\
"""


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class Taxonomy:
    system_prompt: str
    method_vocab: list[str] = field(default_factory=list)
    field_taxonomy: list[str] = field(default_factory=list)

    def rendered_prompt(self) -> str:
        """Return system_prompt with {method_vocab} and {field_taxonomy} substituted."""
        method_str = ", ".join(self.method_vocab)
        field_str = ", ".join(self.field_taxonomy)
        return self.system_prompt.format(
            method_vocab=method_str,
            field_taxonomy=field_str,
        )


# ── Loader ────────────────────────────────────────────────────────────────────

def load_taxonomy(path: Path | None = None) -> Taxonomy:
    """Load taxonomy.yaml from project root. Falls back to built-in defaults if missing."""
    p = path or _TAXONOMY_PATH
    if not p.exists():
        return _default_taxonomy()
    try:
        import yaml  # PyYAML — already a transitive dependency via pydantic/fastapi extras
        with p.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            return _default_taxonomy()
        system_prompt = str(data.get("system_prompt") or _DEFAULT_SYSTEM_PROMPT)
        method_vocab = list(data.get("method_vocab") or _DEFAULT_METHOD_VOCAB)
        field_taxonomy = list(data.get("field_taxonomy") or _DEFAULT_FIELD_TAXONOMY)
        return Taxonomy(
            system_prompt=system_prompt,
            method_vocab=method_vocab,
            field_taxonomy=field_taxonomy,
        )
    except Exception:
        return _default_taxonomy()


def _default_taxonomy() -> Taxonomy:
    return Taxonomy(
        system_prompt=_DEFAULT_SYSTEM_PROMPT,
        method_vocab=list(_DEFAULT_METHOD_VOCAB),
        field_taxonomy=list(_DEFAULT_FIELD_TAXONOMY),
    )
