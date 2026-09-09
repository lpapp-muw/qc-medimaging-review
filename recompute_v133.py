# -*- coding: utf-8 -*-
"""Substep 0.1. Workbook-only corpus recompute at N=134 and N=133.

Emits, in order:
  PART 1  row partition and reconciliation
  PART 2  every metric at N=134 and N=133 with delta, and its operation
  PART 3  v5 binding: each N=134 value checked against the line of v5 that states it
  PART 4  set figures (all-three / at-least-two / union) at both N
  PART 5  year series from human.xlsx
  PART 6  cross-tabulations for Appendix B at N=133
  PART 7  v5 staleness inventory by section

No prose is written by this script. It produces the numbers that prose is written around.
Python 3.8 compatible: .format() only, no f-strings.
"""
from __future__ import print_function
import io, os, re, sys, collections
import openpyxl

WB_PATH = os.environ.get('QC_WB', '/mnt/user-data/uploads/extractions_ai_enriched_v134__2_.xlsx')
HUMAN_PATH = os.environ.get('QC_HUMAN', '/mnt/project/human.xlsx')
V5_PATH = os.environ.get('QC_V5', '/mnt/project/QC_MedImaging_manuscript_draft_v5.md')
EXCLUDED_DOI = '10.1109/access.2025.3627877'

ABSENT = set(['', 'not_reported', 'not reported', 'nr', 'n/a', 'na'])

# ---------------------------------------------------------------- load

def load_rows():
    wb = openpyxl.load_workbook(WB_PATH, read_only=True, data_only=True)
    ws = wb['extractions']
    it = ws.iter_rows(min_row=1, values_only=True)
    next(it)
    hdr = list(next(it))
    rows = [r for r in it if r[0]]
    idx = dict((h, i) for i, h in enumerate(hdr))
    return rows, idx

ROWS, IDX = load_rows()

def g(r, name):
    v = r[IDX[name]]
    return '' if v is None else str(v).strip()

# ---------------------------------------------------------------- helpers

def t(r, f):
    return g(r, f).upper() == 'TRUE'

def ct_true(f):
    return lambda rs: sum(1 for r in rs if t(r, f))

def ct_false(f):
    return lambda rs: sum(1 for r in rs if g(r, f).upper() == 'FALSE')

def ct_eq(f, val):
    return lambda rs: sum(1 for r in rs if g(r, f) == val)

def ct_reported(f):
    """Non-empty and not an absence marker."""
    return lambda rs: sum(1 for r in rs if g(r, f).lower() not in ABSENT)

def ct_nonempty(f):
    return lambda rs: sum(1 for r in rs if g(r, f) != '')

def labels(r, f):
    return [x.strip() for x in g(r, f).split(';') if x.strip()]

def ct_label(f, lab):
    """Presence count for a multi-label field."""
    return lambda rs: sum(1 for r in rs if lab in labels(r, f))

def ct_distinct(f, alias=None):
    def fn(rs):
        vals = set()
        for r in rs:
            v = g(r, f)
            if alias:
                v = alias(v)
            vals.add(v)
        return len(vals)
    return fn

PUB_ALIAS = [
    ('Springer', 'Springer or Springer Nature'),
    ('Elsevier', 'Elsevier'),
    ('IEEE', 'IEEE'),
    ('MDPI', 'MDPI'),
    ('IOP Publishing', 'IOP Publishing'),
    ('Frontiers', 'Frontiers'),
]

def pub_family(v):
    for pref, fam in PUB_ALIAS:
        if v.startswith(pref):
            return fam
    return v

COUNTRY_ALIAS = {
    'Republic of Korea': 'South Korea',
    'South Korea': 'South Korea',
    'United States': 'United States',
    'USA': 'United States',
    'United States of America': 'United States',
}

def country_norm(v):
    return COUNTRY_ALIAS.get(v, v)

def ct_pubfam(fam):
    return lambda rs: sum(1 for r in rs if pub_family(g(r, 'publisher')) == fam)

def ct_country(name):
    return lambda rs: sum(1 for r in rs if country_norm(g(r, 'country_corresponding')) == name)

# ---------------------------------------------------------------- metrics
# (id, manuscript section, operation description, function)

M = []
def add(mid, sec, op, fn):
    M.append((mid, sec, op, fn))

add('N', '4.1', 'row count', lambda rs: len(rs))

# --- 4.2 study characteristics
add('countries_distinct', '4.2', "distinct country_corresponding after alias merge", ct_distinct('country_corresponding', country_norm))
add('countries_raw', '4.2', "distinct country_corresponding raw, unnormalised", ct_distinct('country_corresponding'))
for c in ['India', 'China', 'South Korea', 'United States', 'Saudi Arabia', 'Pakistan', 'Malaysia', 'Egypt', 'Australia']:
    add('country_' + c.replace(' ', '_'), '4.2', "country_corresponding == '{}' after alias merge".format(c), ct_country(c))
add('journals_distinct', '4.2', 'distinct journal', ct_distinct('journal'))
add('journal_ieee_access', '4.2', "journal == 'IEEE Access'", ct_eq('journal', 'IEEE Access'))
add('journal_sci_rep', '4.2', "journal == 'Scientific Reports'", ct_eq('journal', 'Scientific Reports'))
for fam in ['Springer or Springer Nature', 'Elsevier', 'IEEE', 'MDPI', 'IOP Publishing', 'Frontiers']:
    add('pub_' + fam.split()[0].lower(), '4.2', "publisher family '{}' by prefix merge".format(fam), ct_pubfam(fam))
add('funding_declared', '4.2', 'funding_declared TRUE', ct_true('funding_declared'))
add('funding_absent', '4.2', 'funding_declared FALSE', ct_false('funding_declared'))
add('funding_source_named', '4.2', 'funding_source reported', ct_reported('funding_source'))
for a in ['oncology', 'other', 'pulmonology', 'neurology', 'ophthalmology', 'cardiology']:
    add('anatomy_' + a, '4.2', "anatomy contains '{}' (multi-label presence)".format(a), ct_label('anatomy', a))

# --- 4.3 imaging applications
for tk in ['classification', 'segmentation', 'analysis', 'reconstruction', 'super_resolution', 'denoising', 'radiomics', 'other']:
    add('task_' + tk, '4.3', "task_axis_a (derived) == '{}'".format(tk), ct_eq('task_axis_a (derived)', tk))
for tf in ['predictive', 'image_to_image', 'segmentation', 'representation', 'other']:
    add('taskfam_' + tf, '4.3', "task_family (derived) == '{}'".format(tf), ct_eq('task_family (derived)', tf))
for md in ['mri', 'ct', 'x_ray', 'dermoscopy', 'fundus', 'histopathology', 'multimodal', 'pet', 'ultrasound', 'endoscopy', 'unspecified_medical_imaging']:
    add('mod_' + md, '4.3', "modality_subcategory (derived) == '{}'".format(md), ct_eq('modality_subcategory (derived)', md))
for mf in ['radiological_ionising', 'mr', 'optical', 'microscopy_pathology', 'multimodal', 'nuclear', 'ultrasound', 'unspecified']:
    add('modfam_' + mf, '4.3', "modality_family (derived) == '{}'".format(mf), ct_eq('modality_family (derived)', mf))
add('ionising_true', '4.3', 'ionising_flag (derived) TRUE', ct_true('ionising_flag (derived)'))
add('ionising_false', '4.3', 'ionising_flag (derived) FALSE', ct_false('ionising_flag (derived)'))

# --- 4.4 quantum-computing methods
for pd in ['hybrid_quantum_classical', 'quantum_machine_learning', 'quantum_kernel_methods', 'frqi_neqr_encoding', 'quantum_annealing_qubo', 'variational_algorithms', 'qaoa_quantum_optimisation', 'other']:
    add('para_' + pd, '4.4', "paradigm_axis_b (derived) == '{}'".format(pd), ct_eq('paradigm_axis_b (derived)', pd))
for ec in ['ideal_or_unspecified_simulator', 'real_qpu', 'noisy_simulator']:
    add('exec_' + ec, '4.4', "execution_context (derived) == '{}'".format(ec), ct_eq('execution_context (derived)', ec))
for rs_ in ['simulator', 'both', 'real']:
    add('ros_' + rs_, '4.4', "real_or_simulator == '{}'".format(rs_), ct_eq('real_or_simulator', rs_))
for hm in ['simulator_only', 'superconducting', 'annealing', 'not_reported', 'trapped_ion', 'NMR']:
    add('hwmod_' + hm, '4.4', "hardware_modality == '{}'".format(hm), ct_eq('hardware_modality', hm))
add('simfw_named', '4.4', 'simulator_framework reported', ct_reported('simulator_framework'))
add('simfw_none', '4.4', 'simulator_framework blank or absence marker', lambda rs: sum(1 for r in rs if g(r, 'simulator_framework').lower() in ABSENT))
add('simfw_pennylane_primary', '4.4', "simulator_framework == 'PennyLane' exactly", ct_eq('simulator_framework', 'PennyLane'))
add('ansatz_named', '4.4', 'ansatz_family reported', ct_reported('ansatz_family'))
add('ansatz_none', '4.4', 'ansatz_family absence marker', lambda rs: sum(1 for r in rs if g(r, 'ansatz_family').lower() in ABSENT))
for tl in ['none_reported', 'hardware_aware_mapping', 'pulse_level_optimisation', 'default_transpilation']:
    add('transp_' + tl, '4.4', "transpilation_level == '{}'".format(tl), ct_eq('transpilation_level', tl))
for k in ['0', '1', '2', '3', '4']:
    add('qra_' + k, '4.4', "quantum_resource_accounting_completeness (derived) == '{}'".format(k), ct_eq('quantum_resource_accounting_completeness (derived)', k))
for f in ['qubit_count', 'circuit_depth', 'parameter_count', 'shot_count', 'gate_count']:
    add('rep_' + f, '4.4', '{} reported (not an absence marker)'.format(f), ct_reported(f))
for enc in ['angle', 'amplitude', 'basis', 'NEQR', 'FRQI', 'other', 'not_applicable']:
    add('enc_' + enc, '4.4', "image_encoding contains '{}' (multi-label presence)".format(enc), ct_label('image_encoding', enc))
add('enc_blank', '4.4', 'image_encoding blank', lambda rs: sum(1 for r in rs if g(r, 'image_encoding') == ''))
add('em_strategy_none', '4.4', "error_mitigation_strategy == 'none_reported'", ct_eq('error_mitigation_strategy', 'none_reported'))
add('em_type_none', '4.4', "error_mitigation_type == 'none_reported'", ct_eq('error_mitigation_type', 'none_reported'))
add('em_clarity_low', '4.4', "error_mitigation_clarity (derived) == 'low'", ct_eq('error_mitigation_clarity (derived)', 'low'))
add('em_clarity_high', '4.4', "error_mitigation_clarity (derived) == 'high'", ct_eq('error_mitigation_clarity (derived)', 'high'))

# --- 4.5 datasets, baselines, validation
for dr in ['public_benchmark', 'private_single_centre', 'toy', 'private_multi_centre', 'not_reported']:
    add('realism_' + dr, '4.5', "dataset_realism_grade == '{}'".format(dr), ct_eq('dataset_realism_grade', dr))
for rb in ['benchmark', 'real_clinical', 'toy']:
    add('rob_' + rb, '4.5', "dataset_realism_rob (derived) == '{}'".format(rb), ct_eq('dataset_realism_rob (derived)', rb))
add('rob_blank', '4.5', 'dataset_realism_rob (derived) blank', lambda rs: sum(1 for r in rs if g(r, 'dataset_realism_rob (derived)') == ''))
add('split_any', '4.5', 'any of train/val/test size reported', lambda rs: sum(1 for r in rs if any(g(r, f).lower() not in ABSENT for f in ['dataset_size_train', 'dataset_size_val', 'dataset_size_test'])))
add('split_train', '4.5', 'dataset_size_train reported', ct_reported('dataset_size_train'))
add('split_val', '4.5', 'dataset_size_val reported', ct_reported('dataset_size_val'))
add('split_test', '4.5', 'dataset_size_test reported', ct_reported('dataset_size_test'))
add('baseline_present', '4.5', 'classical_baseline_present TRUE', ct_true('classical_baseline_present'))
add('baseline_identified', '4.5', "classical_baseline_identification != 'none'", lambda rs: sum(1 for r in rs if g(r, 'classical_baseline_identification') != 'none'))
for br in ['matched_data_compute', 'matched_data', 'matched_data_compute_stats', 'none', 'weak']:
    add('rigour_' + br, '4.5', "baseline_rigour_grade == '{}'".format(br), ct_eq('baseline_rigour_grade', br))
add('stat_testing', '4.5', 'statistical_testing_present TRUE', ct_true('statistical_testing_present'))
for cv in ['holdout', 'k_fold', 'none', 'not_reported']:
    add('cv_' + cv, '4.5', "cross_validation_strategy == '{}'".format(cv), ct_eq('cross_validation_strategy', cv))
add('cv_freetext', '4.5', 'cross_validation_strategy outside the four controlled values', lambda rs: sum(1 for r in rs if g(r, 'cross_validation_strategy') not in ['holdout', 'k_fold', 'none', 'not_reported']))
add('ext_test_set', '4.5', 'external_test_set TRUE', ct_true('external_test_set'))
add('ext_validation', '4.5', 'external_validation_used TRUE', ct_true('external_validation_used'))
add('samplesize_just', '4.5', 'sample_size_justification_reported TRUE', ct_true('sample_size_justification_reported'))
add('prospective', '4.5', 'prospective_clinical_present TRUE', ct_true('prospective_clinical_present'))
add('regulatory', '4.5', 'regulatory_pathway_addressed TRUE', ct_true('regulatory_pathway_addressed'))
add('perf_quantum', '4.5', 'performance_metrics_quantum non-empty', ct_nonempty('performance_metrics_quantum'))
add('perf_classical', '4.5', 'performance_metrics_classical non-empty', ct_nonempty('performance_metrics_classical'))
add('perf_both', '4.5', 'both performance metric fields non-empty', lambda rs: sum(1 for r in rs if g(r, 'performance_metrics_quantum') != '' and g(r, 'performance_metrics_classical') != ''))
add('cost_training', '4.5', 'computational_cost_training reported', ct_reported('computational_cost_training'))
add('cost_inference', '4.5', 'computational_cost_inference reported', ct_reported('computational_cost_inference'))
add('code_release', '4.5', 'code_release TRUE', ct_true('code_release'))
add('data_release', '4.5', 'data_release TRUE', ct_true('data_release'))
add('weights_release', '4.5', 'weights_release TRUE', ct_true('weights_release'))
for rt in ['none', 'data_only', 'code_only', 'code_plus_data_plus_weights']:
    add('repro_' + rt, '4.5', "reproducibility_tier (derived) == '{}'".format(rt), ct_eq('reproducibility_tier (derived)', rt))

# --- 4.6 quality and maturity
for tv in ['proof_of_concept', 'retrospective_clinical', 'in_silico']:
    add('transl_' + tv, '4.6', "translational_level == '{}'".format(tv), ct_eq('translational_level', tv))
for xm in ['none', 'classical_XAI', 'quantum_XAI', 'both']:
    add('xai_' + xm, '4.6', "explainability_mechanism == '{}'".format(xm), ct_eq('explainability_mechanism', xm))
add('xai_named', '4.6', 'explainability_method_name non-empty', ct_nonempty('explainability_method_name'))
add('multimodal', '4.6', 'multimodal (derived) TRUE', ct_true('multimodal (derived)'))
for sq in ['low_concern', 'moderate_concern', 'high_concern']:
    add('qual_' + sq, '4.6', "summary_methodological_quality (derived) == '{}'".format(sq), ct_eq('summary_methodological_quality (derived)', sq))
add('honest_reporting', '4.6', 'honest_resource_reporting (derived) TRUE', ct_true('honest_resource_reporting (derived)'))
add('standard_none', '4.6', "reporting_standard_adherence == 'none'", ct_eq('reporting_standard_adherence', 'none'))
for k in ['0', '1', '2']:
    add('readiness_' + k, '4.6', "clinical_translation_readiness (derived) == '{}' corpus-wide".format(k), ct_eq('clinical_translation_readiness (derived)', k))

def claim_rows(rs):
    out = []
    for r in rs:
        v = g(r, 'clinical_impact_claim')
        if v != '' and v.lower() not in ABSENT:
            out.append(r)
    return out

add('claim_asserted', '4.6', 'clinical_impact_claim non-empty and not an absence marker', lambda rs: len(claim_rows(rs)))
add('claim_readiness0', '4.6', 'of claimants, clinical_translation_readiness == 0', lambda rs: sum(1 for r in claim_rows(rs) if g(r, 'clinical_translation_readiness (derived)') == '0'))
add('claim_nostat', '4.6', 'of claimants, statistical_testing_present FALSE', lambda rs: sum(1 for r in claim_rows(rs) if not t(r, 'statistical_testing_present')))
add('claim_noextval', '4.6', 'of claimants, external_validation_used FALSE', lambda rs: sum(1 for r in claim_rows(rs) if not t(r, 'external_validation_used')))

# --- composites that v5 states as single figures
add('benchmark_plus_toy', '4.5', "dataset_realism_rob in {benchmark, toy}", lambda rs: sum(1 for r in rs if g(r, 'dataset_realism_rob (derived)') in ('benchmark', 'toy')))
add('india_plus_china', '4.2', "country_corresponding in {India, China}", lambda rs: sum(1 for r in rs if country_norm(g(r, 'country_corresponding')) in ('India', 'China')))
add('xai_any', '4.6', "explainability_mechanism != 'none'", lambda rs: sum(1 for r in rs if g(r, 'explainability_mechanism') != 'none'))

# --- 4.8 kernel
add('qualityfit_yes', '4.8', "'Quality fit' column == YES", ct_eq('Quality fit', 'YES'))

# ---------------------------------------------------------------- run

def sep(title):
    print('')
    print('=' * 100)
    print(title)
    print('=' * 100)

B134 = ROWS
B133 = [r for r in ROWS if g(r, 'doi').lower() != EXCLUDED_DOI]
DROPPED = [r for r in ROWS if g(r, 'doi').lower() == EXCLUDED_DOI]

sep('PART 1. ROW PARTITION AND RECONCILIATION')
print('workbook                 : {}'.format(WB_PATH))
print('rows read                : {}'.format(len(B134)))
print('matched excluded DOI     : {}'.format(len(DROPPED)))
print('retained                 : {}'.format(len(B133)))
print('reconciles               : {} + {} = {}  -> {}'.format(len(B133), len(DROPPED), len(B133) + len(DROPPED),
      'OK' if len(B133) + len(DROPPED) == len(B134) else 'FAIL'))
print('doi uniqueness           : {} distinct of {} rows -> {}'.format(
      len(set(g(r, 'doi').lower() for r in B134)), len(B134),
      'OK' if len(set(g(r, 'doi').lower() for r in B134)) == len(B134) else 'FAIL'))

sep('PART 2. METRICS AT N=134 AND N=133')
print('{:<28} {:<5} {:>7} {:>7} {:>7}  {}'.format('metric', 'sect', 'N=134', 'N=133', 'delta', 'operation'))
print('-' * 100)
VAL134 = {}
VAL133 = {}
for mid, sec, op, fn in M:
    a = fn(B134)
    b = fn(B133)
    VAL134[mid] = a
    VAL133[mid] = b
    d = b - a
    ds = '0' if d == 0 else ('{:+d}'.format(d))
    print('{:<28} {:<5} {:>7} {:>7} {:>7}  {}'.format(mid, sec, a, b, ds, op))

sep('PART 2b. PERCENTAGES THAT MOVE WHILE THE COUNT DOES NOT')
print('{:<28} {:>7} {:>10} {:>10}'.format('metric', 'count', 'pct@134', 'pct@133'))
print('-' * 60)
for mid, sec, op, fn in M:
    if mid == 'N':
        continue
    if VAL134[mid] == VAL133[mid] and VAL134[mid] > 0:
        p1 = 100.0 * VAL134[mid] / len(B134)
        p2 = 100.0 * VAL133[mid] / len(B133)
        if round(p1, 1) != round(p2, 1):
            print('{:<28} {:>7} {:>9.1f}% {:>9.1f}%'.format(mid, VAL134[mid], p1, p2))

# ---------------------------------------------------------------- v5 binding

sep('PART 3. V5 BINDING. Each N=134 value checked against the v5 line that states it.')
V5LINES = io.open(V5_PATH, encoding='utf-8').read().split('\n')

BIND = [
    ('N', 155, 'ionising sentence carries the denominator'),
    ('ionising_true', 155, None),
    ('ionising_false', 155, None),
    ('task_classification', 151, None),
    ('task_segmentation', 151, None),
    ('taskfam_predictive', 151, None),
    ('mod_mri', 153, None),
    ('mod_ct', 153, None),
    ('para_hybrid_quantum_classical', 161, None),
    ('para_quantum_machine_learning', 161, None),
    ('exec_ideal_or_unspecified_simulator', 163, None),
    ('exec_real_qpu', 163, None),
    ('ros_simulator', 163, None),
    ('simfw_named', 165, None),
    ('simfw_pennylane_primary', 165, None),
    ('ansatz_named', 167, None),
    ('transp_none_reported', 169, None),
    ('qra_0', 171, None),
    ('qra_1', 171, None),
    ('qra_2', 171, None),
    ('qra_3', 171, None),
    ('qra_4', 171, None),
    ('rep_qubit_count', 171, None),
    ('rep_circuit_depth', 171, None),
    ('rep_parameter_count', 171, None),
    ('rep_shot_count', 171, None),
    ('rep_gate_count', 171, None),
    ('enc_angle', 173, None),
    ('enc_amplitude', 173, None),
    ('em_strategy_none', 175, None),
    ('em_type_none', 175, None),
    ('em_clarity_low', 175, None),
    ('realism_public_benchmark', 179, None),
    ('realism_private_single_centre', 179, None),
    ('realism_private_multi_centre', 179, None),
    ('rob_real_clinical', 179, None),
    ('split_any', 181, None),
    ('split_train', 181, None),
    ('split_test', 181, None),
    ('split_val', 181, None),
    ('baseline_present', 183, None),
    ('rigour_matched_data_compute', 183, None),
    ('rigour_matched_data', 183, None),
    ('rigour_matched_data_compute_stats', 183, None),
    ('stat_testing', 185, None),
    ('cv_holdout', 185, None),
    ('cv_k_fold', 185, None),
    ('ext_test_set', 185, None),
    ('ext_validation', 185, None),
    ('regulatory', 185, None),
    ('perf_quantum', 189, None),
    ('perf_classical', 189, None),
    ('cost_training', 189, None),
    ('cost_inference', 189, None),
    ('code_release', 191, None),
    ('data_release', 191, None),
    ('repro_none', 191, None),
    ('repro_data_only', 191, None),
    ('repro_code_only', 191, None),
    ('transl_proof_of_concept', 195, None),
    ('transl_retrospective_clinical', 195, None),
    ('transl_in_silico', 195, None),
    ('xai_none', 197, None),
    ('xai_classical_XAI', 197, None),
    ('multimodal', 197, None),
    ('qual_low_concern', 199, None),
    ('qual_moderate_concern', 199, None),
    ('honest_reporting', 199, None),
    ('standard_none', 199, None),
    ('claim_asserted', 203, None),
    ('claim_readiness0', 203, None),
    ('claim_nostat', 203, None),
    ('claim_noextval', 203, None),
    ('countries_distinct', 139, None),
    ('country_India', 139, None),
    ('country_China', 139, None),
    ('journals_distinct', 143, None),
    ('journal_ieee_access', 143, None),
    ('pub_springer', 143, None),
    ('pub_elsevier', 143, None),
    ('funding_declared', 145, None),
    ('funding_absent', 145, None),
    ('funding_source_named', 145, None),
    ('qualityfit_yes', 215, None),
]

nb_pass = 0
nb_fail = 0
for mid, ln, note in BIND:
    line = V5LINES[ln - 1] if 0 < ln <= len(V5LINES) else ''
    val = VAL134[mid]
    hit = re.search(r'(?<![\d,])' + str(val) + r'(?![\d])', line) is not None
    status = 'BOUND' if hit else 'NOT-IN-LINE'
    if hit:
        nb_pass += 1
    else:
        nb_fail += 1
    print('  [{:<11}] {:<34} value={:<5} v5 line {:<4} {}'.format(status, mid, val, ln, ('' if hit else line.strip()[:70])))
print('')
print('bindings: {} bound, {} not found on the stated line'.format(nb_pass, nb_fail))
print('NOTE: NOT-IN-LINE means the v5 sentence does not state the recomputed N=134 value.')
print('      That is a v5 defect that predates the exclusion, or a wrong line reference. Both need adjudication.')

# ---------------------------------------------------------------- printed percentages

sep('PART 3b. PRINTED PERCENTAGES. v5 prints integers; the change lists are stated at one decimal.')

def rhu(x):
    """Round half up, which is what v5 uses everywhere except one case."""
    import math
    return int(math.floor(x + 0.5))

# (v5 line, metric id, count as printed, percent as printed, denominator kind)
PCT = [
    (29, 'benchmark_plus_toy', 106, 79, 'N'),
    (139, 'india_plus_china', 58, 43, 'N'),
    (145, 'funding_declared', 69, 52, 'N'),
    (155, 'ionising_true', 56, 42, 'N'),
    (155, 'ionising_false', 78, 58, 'N'),
    (171, 'rep_qubit_count', 116, 87, 'N'),
    (171, 'rep_circuit_depth', 52, 39, 'N'),
    (171, 'rep_parameter_count', 47, 35, 'N'),
    (171, 'rep_shot_count', 24, 18, 'N'),
    (171, 'rep_gate_count', 18, 13, 'N'),
    (179, 'benchmark_plus_toy', 106, 79, 'N'),
    (181, 'split_any', 109, 81, 'N'),
    (183, 'baseline_present', 125, 93, 'N'),
    (185, 'stat_testing', 35, 26, 'N'),
    (189, 'perf_quantum', 131, 98, 'N'),
    (189, 'perf_classical', 121, 90, 'N'),
    (189, 'perf_both', 120, 90, 'N'),
    (191, 'code_release', 30, 22, 'N'),
    (191, 'data_release', 78, 58, 'N'),
    (199, 'honest_reporting', 18, 13, 'N'),
    (203, 'claim_asserted', 128, 96, 'N'),
    (203, 'claim_readiness0', 112, 88, 'claimants'),
]

print('{:<6} {:<24} {:>6} {:>6} {:>7} {:>7} {:>7}  {}'.format(
      'line', 'metric', 'cnt134', 'cnt133', 'v5 pct', 'pct134', 'pct133', 'verdict'))
print('-' * 108)
bad_at_134 = []
pct_moves = []
for ln, mid, pc, pp, dk in PCT:
    c134 = VAL134[mid]
    c133 = VAL133[mid]
    d134 = len(B134) if dk == 'N' else VAL134['claim_asserted']
    d133 = len(B133) if dk == 'N' else VAL133['claim_asserted']
    p134 = rhu(100.0 * c134 / d134)
    p133 = rhu(100.0 * c133 / d133)
    v = []
    if c134 != pc:
        v.append('COUNT-MISMATCH@134')
        bad_at_134.append((ln, mid, 'count printed {} computed {}'.format(pc, c134)))
    if p134 != pp:
        v.append('PCT-WRONG@134(computed {}%)'.format(p134))
        bad_at_134.append((ln, mid, 'pct printed {}% computed {}%'.format(pp, p134)))
    if c133 != c134:
        v.append('count moves')
    if p133 != p134:
        v.append('PCT MOVES {}->{}'.format(p134, p133))
        pct_moves.append((ln, mid, p134, p133, c134 == c133))
    if not v:
        v.append('unchanged')
    print('{:<6} {:<24} {:>6} {:>6} {:>6}% {:>6}% {:>6}%  {}'.format(
          ln, mid, c134, c133, pp, p134, p133, '; '.join(v)))

print('')
print('Printed percentages that move: {}'.format(len(pct_moves)))
for ln, mid, a, b, same_count in pct_moves:
    print('   line {:<5} {:<24} {}% -> {}%   {}'.format(
          ln, mid, a, b, 'COUNT UNCHANGED' if same_count else 'count also moves'))
print('')
print('Defects already present at N=134: {}'.format(len(bad_at_134)))
for ln, mid, msg in bad_at_134:
    print('   line {:<5} {:<24} {}'.format(ln, mid, msg))

sep('PART 3c. THE 4.8 SET FIGURES UNDER TWO OPERATIONALISATIONS')
c1 = lambda r: g(r, 'dataset_realism_rob (derived)') == 'real_clinical'
c2 = lambda r: t(r, 'classical_baseline_present')
c2b = lambda r: g(r, 'baseline_rigour_grade') == 'matched_data_compute_stats'
c3 = lambda r: t(r, 'statistical_testing_present')
for nm, cs in [('A: realism + baseline_present + stat_testing (the text says this)', (c1, c2, c3)),
               ('B: realism + top_baseline_rigour + stat_testing', (c1, c2b, c3))]:
    print(nm)
    for label, rs in [('N=134', B134), ('N=133', B133)]:
        n3 = sum(1 for r in rs if all(f(r) for f in cs))
        n2 = sum(1 for r in rs if sum(bool(f(r)) for f in cs) >= 2)
        nu = sum(1 for r in rs if any(f(r) for f in cs))
        print('   {}   all three = {:<4} at least two = {:<4} union = {}'.format(label, n3, n2, nu))
print('')
print('v5 line 215 states: all three = 8, at least two = 32, union = 54.')
print('8 comes from set A. 32 and 54 come from set B. The paragraph mixes two operationalisations.')

# ---------------------------------------------------------------- set figures

sep('PART 4. KERNEL SET FIGURES')

def crits(r):
    c1 = g(r, 'dataset_realism_rob (derived)') == 'real_clinical'
    c2 = t(r, 'classical_baseline_present')
    c3 = t(r, 'statistical_testing_present')
    return c1, c2, c3

for label, rs in [('N=134', B134), ('N=133', B133)]:
    n3 = sum(1 for r in rs if all(crits(r)))
    n2 = sum(1 for r in rs if sum(crits(r)) >= 2)
    nu = sum(1 for r in rs if any(crits(r)))
    c1 = sum(1 for r in rs if crits(r)[0])
    c2 = sum(1 for r in rs if crits(r)[1])
    c3 = sum(1 for r in rs if crits(r)[2])
    top = sum(1 for r in rs if g(r, 'baseline_rigour_grade') == 'matched_data_compute_stats')
    qf = sum(1 for r in rs if g(r, 'Quality fit') == 'YES')
    print('{}  real_clinical={}  baseline={}  stat_testing={}  top_rigour={}'.format(label, c1, c2, c3, top))
    print('        all three = {}   at least two of three = {}   union = {}   Quality fit YES = {}'.format(n3, n2, nu, qf))
    print('        kernel-vs-column agreement: {}'.format('OK' if n3 == qf else 'MISMATCH'))

print('')
print('Kernel DOIs at N=133:')
for r in B133:
    if all(crits(r)):
        print('   {}  {}'.format(g(r, 'doi'), g(r, 'stable_name')))

# ---------------------------------------------------------------- year

sep('PART 5. YEAR SERIES FROM human.xlsx')
hw = openpyxl.load_workbook(HUMAN_PATH, data_only=True)['extractions']
hrows = [(str(r[0]).strip().lower(), r[4]) for r in hw.iter_rows(min_row=3, values_only=True) if r[0]]
ymap = dict(hrows)
print('human.xlsx rows: {}   years populated: {}'.format(len(hrows), sum(1 for _, y in hrows if y not in (None, ''))))
for label, rs in [('N=134', B134), ('N=133', B133)]:
    dois = [g(r, 'doi').lower() for r in rs]
    missing = [d for d in dois if d not in ymap]
    yc = collections.Counter(int(ymap[d]) for d in dois if d in ymap)
    tot = sum(yc.values())
    print('{}  {}  total={}  unknown={}  reconciles={}'.format(
        label, dict(sorted(yc.items())), tot, len(missing),
        'OK' if tot == len(rs) and not missing else 'FAIL'))

# ---------------------------------------------------------------- cross-tabs

sep('PART 6. CROSS-TABULATIONS AT N=133 (Appendix B)')

def xtab(rs, rowf, colf, rowvals, colvals):
    tab = collections.defaultdict(collections.Counter)
    for r in rs:
        tab[g(r, rowf)][g(r, colf)] += 1
    print('{:<32} {}'.format(rowf + ' \\ ' + colf, ' '.join('{:>6}'.format(c[:6]) for c in colvals)))
    for rv in rowvals:
        tot = sum(tab[rv].values())
        print('{:<32} {} {:>7}'.format(rv[:32], ' '.join('{:>6}'.format(tab[rv][c]) for c in colvals), tot))

TASKS = ['analysis', 'classification', 'denoising', 'other', 'radiomics', 'reconstruction', 'segmentation', 'super_resolution']
PARAS = ['hybrid_quantum_classical', 'quantum_machine_learning', 'quantum_kernel_methods', 'frqi_neqr_encoding', 'quantum_annealing_qubo', 'variational_algorithms', 'qaoa_quantum_optimisation', 'other']
MODS = ['mri', 'ct', 'x_ray', 'dermoscopy', 'fundus', 'histopathology', 'multimodal', 'pet', 'ultrasound', 'endoscopy', 'unspecified_medical_imaging']
EXECS = ['ideal_or_unspecified_simulator', 'real_qpu', 'noisy_simulator']
TRANS = ['proof_of_concept', 'retrospective_clinical', 'in_silico']

xtab(B133, 'paradigm_axis_b (derived)', 'task_axis_a (derived)', PARAS, TASKS)
print('')
xtab(B133, 'modality_subcategory (derived)', 'task_axis_a (derived)', MODS, TASKS)
print('')
xtab(B133, 'execution_context (derived)', 'translational_level', EXECS, TRANS)
print('')
xtab(B133, 'execution_context (derived)', 'task_axis_a (derived)', EXECS, TASKS)
print('')
rq = [r for r in B133 if g(r, 'execution_context (derived)') == 'real_qpu']
print('real_qpu dataset realism at N=133: {}'.format(dict(collections.Counter(g(r, 'dataset_realism_rob (derived)') for r in rq))))

# ---------------------------------------------------------------- staleness

sep('PART 7. V5 STALENESS INVENTORY')
sec_re = re.compile(r'^#{1,4}\s+(.*)$')
cur = '(preamble)'
per_sec = collections.OrderedDict()
lines_134 = []
for i, ln in enumerate(V5LINES, 1):
    m = sec_re.match(ln)
    if m:
        cur = m.group(1).strip()
        per_sec.setdefault(cur, {'lines': 0, 'tok134': 0, 'stale_vals': 0})
    per_sec.setdefault(cur, {'lines': 0, 'tok134': 0, 'stale_vals': 0})
    per_sec[cur]['lines'] += 1
    n134 = len(re.findall(r'(?<![\d,])134(?![\d])', ln))
    if n134:
        per_sec[cur]['tok134'] += n134
        lines_134.append(i)

CHANGED = dict((mid, VAL134[mid]) for mid, sec, op, fn in M if VAL134[mid] != VAL133[mid] and mid != 'N')
cur = '(preamble)'
for i, ln in enumerate(V5LINES, 1):
    m = sec_re.match(ln)
    if m:
        cur = m.group(1).strip()
    for mid, v in CHANGED.items():
        if re.search(r'(?<![\d,])' + str(v) + r'(?![\d])', ln):
            per_sec[cur]['stale_vals'] += 1
            break

print('total lines            : {}'.format(len(V5LINES)))
print("occurrences of '134'   : {}".format(sum(v['tok134'] for v in per_sec.values())))
print("lines containing '134' : {}".format(len(lines_134)))
print('metrics that move      : {}'.format(len(CHANGED)))
print('')
print('{:<62} {:>6} {:>7} {:>10}'.format('section', 'lines', "'134'", 'stale-vals'))
print('-' * 90)
touched = 0
for k, v in per_sec.items():
    if v['tok134'] or v['stale_vals']:
        touched += 1
        print('{:<62} {:>6} {:>7} {:>10}'.format(k[:62], v['lines'], v['tok134'], v['stale_vals']))
print('')
print('sections requiring edit: {} of {}'.format(touched, len(per_sec)))
print("line numbers containing '134': {}".format(lines_134))
