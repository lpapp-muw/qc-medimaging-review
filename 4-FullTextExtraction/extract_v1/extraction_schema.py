"""
extraction_schema.py — Locked schema for Step-4 AI extraction.

Mirrors OSF Protocol v0.6 §9 (data items and extraction plan) and §10 (RoB
flags). Each field is declared with its type, allowed values (for enums),
quote-grounding requirement, multi-value flag, pass assignment, derived flag,
and conditional-requirement rule.

This module is THE single source of truth for the schema. The validator
(validate_extraction.py), the subagent prompts (.claude/agents/*.md), the
merge logic (merge_passes.py), the derived-field computation
(compute_derived.py), and the xlsx exporter (extractions_to_xlsx.py) all
import from here.

Python 3.8 compatible (no PEP-604 unions, no PEP-585 builtins,
no @dataclass with generic annotations, no `from __future__ import annotations`).

Schema design rules (locked Phase 1.2):
  - Every extracted value field has a paired `_quote` (B1 strict grounding).
  - Multi-value fields are lists of {value, quote, chunk_id, pass_id}.
  - Derived fields are computed post-extraction, never asked of a subagent.
  - Human-only fields are excluded from the AI schema entirely.
  - Conditional-required fields are validated at record level
    (e.g. funding_source required iff funding_declared == True).
"""

# ----------------------------------------------------------------------------
# Type constants (string-based, no enum module to keep Python 3.8 light)
# ----------------------------------------------------------------------------
TYPE_VERBATIM = "verbatim"        # free-text copy from source; quote required
TYPE_ENUM = "enum"                # single value from allowed set
TYPE_MULTI_ENUM = "multi_enum"    # list of values from allowed set
TYPE_BOOL = "bool"                # True / False
TYPE_INT = "int"                  # integer
TYPE_INT_RANGE = "int_range"      # integer with min/max bounds
TYPE_STRING = "string"            # raw string (no verbatim contract; e.g. doi)
TYPE_LIST_OBJ = "list_obj"        # list of dict, schema per-item

# Pass identifiers
PASS_A = "A"   # Bibliographic + Imaging + Translational
PASS_B = "B"   # QC characterisation
PASS_C = "C"   # Validation
PASS_D = "D"   # RoB synthesis (gets PDF + B + C)
PASS_E = "E"   # Reconciliation (gets structured outputs only)
PASS_META = "meta"          # metadata, not extracted
PASS_DERIVED = "derived"    # computed post-extraction
PASS_HUMAN = "human"        # human-only, excluded from AI


class FieldSpec(object):
    """Declarative spec for a single schema field.

    Plain class (not @dataclass) for Python 3.8 compatibility with the
    project's existing style convention.
    """

    def __init__(
        self,
        name,
        type_,
        pass_,
        allowed=None,
        requires_quote=True,
        multivalue=False,
        derived=False,
        human_only=False,
        conditional_required_when=None,
        int_min=None,
        int_max=None,
        list_item_schema=None,
        description="",
    ):
        # name: str, e.g. "qubit_count"
        # type_: one of the TYPE_* constants
        # pass_: one of PASS_*
        # allowed: list of strings for TYPE_ENUM / TYPE_MULTI_ENUM
        # requires_quote: bool; if True, value must be quote-grounded
        # multivalue: bool; if True, stored as list-of-{value,quote,chunk_id,pass_id}
        # derived: bool; if True, computed post-extraction (never asked of subagent)
        # human_only: bool; if True, excluded from AI schema entirely
        # conditional_required_when: tuple (cond_field, cond_value) or None;
        #     this field must be present and non-null when record[cond_field] == cond_value
        # int_min, int_max: bounds for TYPE_INT_RANGE
        # list_item_schema: dict of sub-FieldSpecs for TYPE_LIST_OBJ
        # description: short human-readable note
        self.name = name
        self.type_ = type_
        self.pass_ = pass_
        self.allowed = list(allowed) if allowed else None
        self.requires_quote = requires_quote
        self.multivalue = multivalue
        self.derived = derived
        self.human_only = human_only
        self.conditional_required_when = conditional_required_when
        self.int_min = int_min
        self.int_max = int_max
        self.list_item_schema = list_item_schema
        self.description = description


# ----------------------------------------------------------------------------
# Enum vocabularies (locked Phase 1.2)
# ----------------------------------------------------------------------------
MODALITY_ENUM = [
    "CT", "PET", "SPECT", "MRI", "ultrasound", "X-ray", "fundus", "OCT",
    "histopathology", "dermoscopy", "microscopy", "other",
]

ANATOMY_ENUM = [
    "oncology", "neurology", "cardiology", "ophthalmology", "pulmonology",
    "other",
]

IMAGING_TASK_ENUM = [
    "reconstruction", "analysis", "segmentation", "classification",
    "denoising", "super_resolution", "radiomics", "fusion",
    "instrumentation", "dosimetry", "other",
]

DATASET_TYPE_ENUM = [
    "public_benchmark", "private_clinical", "synthetic",
]

TRANSLATIONAL_LEVEL_ENUM = [
    "proof_of_concept", "in_silico", "retrospective_clinical",
    "prospective_clinical",
]

PARADIGM_ENUM = [
    "QML", "VQE", "QAOA", "quantum_kernel", "FRQI", "NEQR",
    "hybrid_quantum_classical", "other",
]

HARDWARE_MODALITY_ENUM = [
    "superconducting", "trapped_ion", "photonic", "neutral_atom",
    "annealing", "simulator_only", "not_reported",
]

REAL_OR_SIM_ENUM = ["real", "simulator", "both"]

SIMULATOR_FRAMEWORK_ENUM = [
    "Qiskit_Aer", "PennyLane", "Cirq", "MS_QDK", "Strawberry_Fields", "other",
]

TRANSPILATION_LEVEL_ENUM = [
    "none_reported", "default_transpilation", "hardware_aware_mapping",
    "pulse_level_optimisation",
]

IMAGE_ENCODING_ENUM = [
    "basis", "angle", "amplitude", "FRQI", "NEQR", "NASS", "MCRQI", "NCQI",
    "BRQI", "hybrid", "not_applicable", "other",
]

ERROR_MIT_TYPE_ENUM = [
    "none_reported", "readout_only", "ZNE", "PEC", "dynamical_decoupling",
    "T_REx", "CDR", "composite",
]

CV_STRATEGY_ENUM = [
    "k_fold", "LOOCV", "holdout", "none", "not_reported",
]

BASELINE_RIGOUR_ENUM = [
    "none", "weak", "matched_data", "matched_data_compute",
    "matched_data_compute_stats",
]

DATASET_REALISM_ENUM = [
    "toy", "public_benchmark", "private_single_centre",
    "private_multi_centre", "prospective_clinical",
]

REPRODUCIBILITY_TIER_ENUM = [
    "none", "code_only", "data_only", "weights_or_circuit_params_only",
    "code_plus_data_plus_weights",
]

REPORTING_STANDARD_ENUM = [
    "CLAIM_2024", "TRIPOD_AI", "METRICS_2024", "none",
]

EXPLAINABILITY_ENUM = [
    "none", "classical_XAI", "quantum_XAI", "both",
]

# Derived enums
DATASET_REALISM_ROB_ENUM = ["benchmark", "real_clinical"]
ERROR_MIT_CLARITY_ENUM = ["none", "low", "high"]
SUMMARY_QUALITY_ENUM = ["low_concern", "moderate_concern", "high_concern"]


# ----------------------------------------------------------------------------
# Pass A — Bibliographic + Imaging + Translational
# ----------------------------------------------------------------------------
PASS_A_FIELDS = [
    # § 9.1 Bibliographic
    FieldSpec("doi", TYPE_STRING, PASS_META, requires_quote=False,
              description="Lowercase DOI. From metadata, not extracted."),
    FieldSpec("title", TYPE_VERBATIM, PASS_A,
              description="Paper title, verbatim."),
    FieldSpec("authors", TYPE_LIST_OBJ, PASS_A, multivalue=True,
              list_item_schema={"family": "str", "given": "str"},
              description="List of {family, given, quote}."),
    FieldSpec("year", TYPE_INT, PASS_META, requires_quote=False,
              description="Publication year. From metadata."),
    FieldSpec("journal", TYPE_VERBATIM, PASS_A,
              description="Journal name."),
    FieldSpec("publisher", TYPE_VERBATIM, PASS_A,
              description="Publisher name."),
    FieldSpec("country_corresponding", TYPE_VERBATIM, PASS_A,
              description="Country of corresponding author or 'not_reported'."),
    FieldSpec("funding_declared", TYPE_BOOL, PASS_A,
              description="True iff a funding declaration is present."),
    FieldSpec("funding_source", TYPE_VERBATIM, PASS_A,
              conditional_required_when=("funding_declared", True),
              description="Verbatim funding source. Required iff funding_declared."),

    # § 9.3 Imaging
    FieldSpec("modality_primary", TYPE_ENUM, PASS_A, allowed=MODALITY_ENUM,
              description="Primary imaging modality."),
    FieldSpec("modality_secondary", TYPE_ENUM, PASS_A, allowed=MODALITY_ENUM,
              description="Secondary modality if multi-modal; else null."),
    FieldSpec("multimodal", TYPE_BOOL, PASS_DERIVED, requires_quote=False,
              derived=True,
              description="Derived: true iff modality_secondary not null."),
    FieldSpec("anatomy", TYPE_MULTI_ENUM, PASS_A, allowed=ANATOMY_ENUM,
              multivalue=True,
              description="Anatomic region(s) or disease context(s)."),
    FieldSpec("imaging_task", TYPE_ENUM, PASS_A, allowed=IMAGING_TASK_ENUM,
              description="Primary imaging task addressed."),
    FieldSpec("dataset_type", TYPE_MULTI_ENUM, PASS_A,
              allowed=DATASET_TYPE_ENUM, multivalue=True,
              description="Dataset category/categories used."),
    FieldSpec("dataset_name", TYPE_VERBATIM, PASS_A, multivalue=True,
              description="Name(s) of dataset(s) cited."),
    FieldSpec("dataset_size_train", TYPE_VERBATIM, PASS_A,
              description="Training set size, verbatim or 'not_reported'."),
    FieldSpec("dataset_size_val", TYPE_VERBATIM, PASS_A,
              description="Validation set size, verbatim or 'not_reported'."),
    FieldSpec("dataset_size_test", TYPE_VERBATIM, PASS_A,
              description="Test set size, verbatim or 'not_reported'."),
    FieldSpec("ionising_flag", TYPE_BOOL, PASS_DERIVED, requires_quote=False,
              derived=True,
              description="Derived from modality_primary + modality_secondary."),

    # § 9.5 Translational maturity
    FieldSpec("translational_level", TYPE_ENUM, PASS_A,
              allowed=TRANSLATIONAL_LEVEL_ENUM,
              description="Translational maturity level."),
    FieldSpec("clinical_impact_claim", TYPE_VERBATIM, PASS_A,
              description="Verbatim clinical-impact claim made by authors."),
    FieldSpec("sample_size_justification_reported", TYPE_BOOL, PASS_A,
              description="True iff authors report sample-size justification."),
    FieldSpec("external_validation_used", TYPE_BOOL, PASS_A,
              description="True iff an external/independent validation set used."),
    FieldSpec("regulatory_pathway_addressed", TYPE_BOOL, PASS_A,
              description="True iff regulatory pathway addressed."),
    FieldSpec("prospective_clinical_present", TYPE_BOOL, PASS_A,
              description="True iff prospective clinical evaluation present."),
    FieldSpec("clinical_translation_readiness", TYPE_INT_RANGE, PASS_DERIVED,
              requires_quote=False, derived=True, int_min=0, int_max=4,
              description="Derived 0-4: sum of the four bools above."),
    FieldSpec("clinical_impact_assessment", TYPE_ENUM, PASS_HUMAN,
              allowed=["low", "moderate", "high"], requires_quote=False,
              human_only=True,
              description="Human-only review-team judgment. Excluded from AI."),
]


# ----------------------------------------------------------------------------
# Pass B — QC characterisation
# ----------------------------------------------------------------------------
PASS_B_FIELDS = [
    FieldSpec("paradigm", TYPE_ENUM, PASS_B, allowed=PARADIGM_ENUM,
              description="Quantum-computing paradigm."),
    FieldSpec("paradigm_novelty_note", TYPE_VERBATIM, PASS_B,
              conditional_required_when=("paradigm", "other"),
              description="Required iff paradigm=='other'."),
    FieldSpec("hardware_vendor", TYPE_VERBATIM, PASS_B,
              description="Vendor name or 'not_reported'."),
    FieldSpec("hardware_modality", TYPE_ENUM, PASS_B,
              allowed=HARDWARE_MODALITY_ENUM,
              description="Qubit modality."),
    FieldSpec("real_or_simulator", TYPE_ENUM, PASS_B, allowed=REAL_OR_SIM_ENUM,
              description="Execution context."),
    FieldSpec("simulator_framework", TYPE_MULTI_ENUM, PASS_B,
              allowed=SIMULATOR_FRAMEWORK_ENUM, multivalue=True,
              description="Simulator framework(s) used."),
    FieldSpec("ansatz_family", TYPE_VERBATIM, PASS_B,
              description="Ansatz family if applicable, or 'not_reported'."),
    FieldSpec("parameter_count", TYPE_VERBATIM, PASS_B,
              description="Parameter count, verbatim or 'not_reported'."),
    FieldSpec("transpilation_level", TYPE_ENUM, PASS_B,
              allowed=TRANSPILATION_LEVEL_ENUM,
              description="Hardware-aware transpilation level."),
    FieldSpec("image_encoding", TYPE_MULTI_ENUM, PASS_B,
              allowed=IMAGE_ENCODING_ENUM, multivalue=True,
              description="Image-encoding scheme(s)."),
    FieldSpec("image_encoding_novelty_note", TYPE_VERBATIM, PASS_B,
              conditional_required_when=("image_encoding_contains_other", True),
              description="Required iff image_encoding contains 'other'."),
    FieldSpec("error_mitigation_strategy", TYPE_VERBATIM, PASS_B,
              description="Verbatim description or 'none_reported'."),
    FieldSpec("error_mitigation_type", TYPE_ENUM, PASS_B,
              allowed=ERROR_MIT_TYPE_ENUM,
              description="Classification of the strategy."),
    FieldSpec("qubit_count", TYPE_VERBATIM, PASS_B,
              description="Verbatim per § 9.2 readout, or 'not_reported'."),
    FieldSpec("circuit_depth", TYPE_VERBATIM, PASS_B,
              description="Verbatim circuit depth, or 'not_reported'."),
    FieldSpec("gate_count", TYPE_VERBATIM, PASS_B,
              description="Verbatim gate count, or 'not_reported'."),
    FieldSpec("shot_count", TYPE_VERBATIM, PASS_B,
              description="Verbatim shot count per circuit eval, or 'not_reported'."),
]


# ----------------------------------------------------------------------------
# Pass C — Validation
# ----------------------------------------------------------------------------
PASS_C_FIELDS = [
    FieldSpec("classical_baseline_present", TYPE_BOOL, PASS_C,
              description="True iff a classical baseline is reported."),
    FieldSpec("classical_baseline_identification", TYPE_VERBATIM, PASS_C,
              conditional_required_when=("classical_baseline_present", True),
              description="Baseline architecture/method. Required iff present."),
    FieldSpec("performance_metrics_quantum", TYPE_LIST_OBJ, PASS_C,
              multivalue=True,
              list_item_schema={"metric_name": "str", "value": "str"},
              description="Quantum-method metrics: list of {metric_name, value, quote}."),
    FieldSpec("performance_metrics_classical", TYPE_LIST_OBJ, PASS_C,
              multivalue=True,
              list_item_schema={"metric_name": "str", "value": "str"},
              description="Classical-baseline metrics: list of {metric_name, value, quote}."),
    FieldSpec("cross_validation_strategy", TYPE_ENUM, PASS_C,
              allowed=CV_STRATEGY_ENUM,
              description="Cross-validation strategy used."),
    FieldSpec("external_test_set", TYPE_BOOL, PASS_C,
              description="True iff an external test set used."),
    FieldSpec("statistical_testing_present", TYPE_BOOL, PASS_C,
              description="True iff statistical testing is reported."),
    FieldSpec("statistical_testing_method", TYPE_VERBATIM, PASS_C,
              conditional_required_when=("statistical_testing_present", True),
              description="Statistical test name(s). Required iff present."),
    FieldSpec("code_release", TYPE_BOOL, PASS_C,
              description="True iff code is released."),
    FieldSpec("code_url", TYPE_VERBATIM, PASS_C,
              conditional_required_when=("code_release", True),
              description="Code URL. Required iff code_release."),
    FieldSpec("data_release", TYPE_BOOL, PASS_C,
              description="True iff dataset released."),
    FieldSpec("data_identifier", TYPE_VERBATIM, PASS_C,
              conditional_required_when=("data_release", True),
              description="Data identifier or URL. Required iff data_release."),
    FieldSpec("weights_release", TYPE_BOOL, PASS_C,
              description="True iff pre-trained weights or circuit params released."),
    FieldSpec("computational_cost_training", TYPE_VERBATIM, PASS_C,
              description="Training time/cost, verbatim or 'not_reported'."),
    FieldSpec("computational_cost_inference", TYPE_VERBATIM, PASS_C,
              description="Inference time/cost, verbatim or 'not_reported'."),
    FieldSpec("baseline_rigour_grade", TYPE_ENUM, PASS_C,
              allowed=BASELINE_RIGOUR_ENUM,
              description="Baseline-rigour grade per § 9.4."),
    FieldSpec("dataset_realism_grade", TYPE_ENUM, PASS_C,
              allowed=DATASET_REALISM_ENUM,
              description="Dataset-realism grade per § 9.4."),
    FieldSpec("reproducibility_tier", TYPE_ENUM, PASS_DERIVED,
              allowed=REPRODUCIBILITY_TIER_ENUM, requires_quote=False,
              derived=True,
              description="Derived from {code_release, data_release, weights_release}."),
    FieldSpec("reporting_standard_adherence", TYPE_MULTI_ENUM, PASS_C,
              allowed=REPORTING_STANDARD_ENUM, multivalue=True,
              description="Reporting standard(s) followed, or 'none'."),
]


# ----------------------------------------------------------------------------
# Pass D — RoB synthesis (receives PDF + Pass B + Pass C outputs)
# ----------------------------------------------------------------------------
PASS_D_FIELDS = [
    FieldSpec("explainability_mechanism", TYPE_ENUM, PASS_D,
              allowed=EXPLAINABILITY_ENUM,
              description="Explainability mechanism type."),
    FieldSpec("explainability_method_name", TYPE_VERBATIM, PASS_D,
              conditional_required_when=("explainability_mechanism", "not_none"),
              description="Method name (Grad-CAM, SHAP, QSHAP, ...). Required iff mechanism != none."),
    FieldSpec("dataset_realism_rob", TYPE_ENUM, PASS_DERIVED,
              allowed=DATASET_REALISM_ROB_ENUM, requires_quote=False,
              derived=True,
              description="Derived from dataset_realism_grade."),
    FieldSpec("quantum_resource_accounting_completeness", TYPE_INT_RANGE,
              PASS_DERIVED, requires_quote=False, derived=True,
              int_min=0, int_max=4,
              description="Derived 0-4: count of (qubit, depth, gate, shot) reported."),
    FieldSpec("error_mitigation_clarity", TYPE_ENUM, PASS_DERIVED,
              allowed=ERROR_MIT_CLARITY_ENUM, requires_quote=False,
              derived=True,
              description="Derived from Pass B error_mitigation fields."),
    FieldSpec("honest_resource_reporting", TYPE_BOOL, PASS_DERIVED,
              requires_quote=False, derived=True,
              description="Derived: real_or_simulator != not_reported AND QRA >= 3."),
    FieldSpec("summary_methodological_quality", TYPE_ENUM, PASS_DERIVED,
              allowed=SUMMARY_QUALITY_ENUM, requires_quote=False,
              derived=True,
              description="Derived: deterministic rubric over RoB flags."),
]


# ----------------------------------------------------------------------------
# Pass E — Reconciliation (no extracted fields; outputs contradictions list)
# ----------------------------------------------------------------------------
# Pass E's output is a contradictions list, not field values. Schema for
# downstream consumption only:
PASS_E_OUTPUT_SCHEMA = {
    "contradictions": "list_of_dicts",
    # each dict: {field_a, value_a, field_b, value_b, severity, note}
    # severity in {low, medium, high}
}


# ----------------------------------------------------------------------------
# Aggregated schema (the canonical registry)
# ----------------------------------------------------------------------------
ALL_FIELDS = PASS_A_FIELDS + PASS_B_FIELDS + PASS_C_FIELDS + PASS_D_FIELDS

# Index by name for O(1) lookup
SCHEMA = {f.name: f for f in ALL_FIELDS}


# ----------------------------------------------------------------------------
# Convenience accessors
# ----------------------------------------------------------------------------
def fields_for_pass(pass_letter):
    """Return the list of FieldSpecs whose pass_ matches `pass_letter`."""
    return [f for f in ALL_FIELDS if f.pass_ == pass_letter]


def extracted_fields():
    """All fields the AI is responsible for extracting (excludes derived,
    human_only, and meta)."""
    return [
        f for f in ALL_FIELDS
        if f.pass_ in (PASS_A, PASS_B, PASS_C, PASS_D) and not f.derived
    ]


def derived_fields():
    """All fields computed post-extraction by a deterministic rubric."""
    return [f for f in ALL_FIELDS if f.derived]


def human_only_fields():
    """All fields excluded from the AI schema."""
    return [f for f in ALL_FIELDS if f.human_only]


def meta_fields():
    """Fields populated from input metadata, not extracted."""
    return [f for f in ALL_FIELDS if f.pass_ == PASS_META]


# ----------------------------------------------------------------------------
# Self-check
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    print("Total fields in schema      :", len(ALL_FIELDS))
    print("Pass A (Biblio+Img+Trans)   :", len(fields_for_pass(PASS_A)))
    print("Pass B (QC characterisation):", len(fields_for_pass(PASS_B)))
    print("Pass C (Validation)         :", len(fields_for_pass(PASS_C)))
    print("Pass D (RoB synthesis)      :", len(fields_for_pass(PASS_D)))
    print("Derived (post-extraction)   :", len(derived_fields()))
    print("Human-only (excluded)       :", len(human_only_fields()))
    print("Meta (from metadata)        :", len(meta_fields()))
    print("AI-extracted total          :", len(extracted_fields()))
    print()
    print("Sanity checks:")
    # 1. Every field name unique
    names = [f.name for f in ALL_FIELDS]
    assert len(names) == len(set(names)), "Duplicate field names!"
    print("  - all field names unique : OK")
    # 2. Every enum field has allowed list
    for f in ALL_FIELDS:
        if f.type_ in (TYPE_ENUM, TYPE_MULTI_ENUM):
            assert f.allowed, "Field {} is enum but no allowed list".format(f.name)
    print("  - all enums have allowed : OK")
    # 3. Every conditional_required_when refers to an existing field
    for f in ALL_FIELDS:
        if f.conditional_required_when:
            cond_field, _ = f.conditional_required_when
            # special pseudo-conditions like "image_encoding_contains_other"
            # and "not_none" are handled in the validator, not here
            if cond_field == "image_encoding_contains_other":
                assert "image_encoding" in SCHEMA, "image_encoding missing"
            elif cond_field in SCHEMA:
                pass
            else:
                raise AssertionError(
                    "Field {} references unknown cond_field {}"
                    .format(f.name, cond_field))
    print("  - cond-required wired up : OK")
    # 4. Every int-range field has int_min and int_max
    for f in ALL_FIELDS:
        if f.type_ == TYPE_INT_RANGE:
            assert f.int_min is not None and f.int_max is not None, \
                "int_range field {} missing bounds".format(f.name)
    print("  - int_range bounds set   : OK")
    print()
    print("Schema lock self-check: PASSED")
