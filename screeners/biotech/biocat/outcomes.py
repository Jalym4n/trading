"""Primary-outcome classification: what each PRIMARY outcome measures.

Reads protocolSection.outcomesModule.primaryOutcomes[] only (secondary and
other outcomes never enter). Each primary outcome gets exactly one class:

  clinical     a named clinical rating scale or patient outcome; a physiologic
               measure with a clinical target (blood pressure, HbA1c, eGFR, FEV1,
               visual acuity, body weight, lesion count); an event rate
               (exacerbations, hypoglycaemia, relapses, seizures, flares,
               hospitalisations, remission, response, survival, progression,
               recurrence, "responder", "success", "clear / almost clear")
  safety       adverse events, tolerability, labs, vitals, ECG, and bare
               operational phases (dose escalation / expansion, MTD, RP2D, DLT)
  pk_pd        pharmacokinetics, exposure, target engagement
  biomarker    imaging (incl. tracer activity: TSPO, SUVR, amyloid PET, "as
               measured by [F-18]..."), fluid analytes, antibody titres, EEG
  unclassified none of the vocabularies matched (shown, never guessed)

Precedence, applied to the measure text and then, only if nothing matched,
to that primary outcome's own description: a NAMED clinical scale wins
(EASI, CDR-SB, monthly migraine days, PFS, ...), then safety, pk_pd,
biomarker, then generic clinical words (survival, response rate, relapse...).
"""

from __future__ import annotations

import re

NAMED_CLINICAL = re.compile(
    r"\b(?:CDR[- ]?SB|CDR\b|ADAS[- ]?Cog|iADRS|ADCS[- ]?(?:ADL|CGIC)|MMSE|MoCA|NPI|CGI[- ]?[SI]?|PANSS|MADRS|HAM[- ]?[AD]|"
    r"EASI(?:[- ]?\d+)?|IGA|vIGA|SCORAD|PASI(?:[- ]?\d+)?|UAS7|UCT|ISS7|DLQI|ACR\s?(?:20|50|70)|DAS28|CDAI|SDAI|"
    r"HAQ|SLEDAI|BILAG|SRI[- ]?4|Mayo|CDAI|PRO2|monthly migraine days|migraine days|headache days|"
    r"progression[- ]free survival|overall survival|PFS|OS\b|event[- ]free survival|disease[- ]free survival|"
    r"objective response rate|ORR|complete response|duration of response|"
    r"6[- ]?minute walk|6MWT?|NSAA|ALSFRS|EDSS|UPDRS|MDS[- ]UPDRS|FVC|FEV1|SGRQ|KCCQ|NYHA|"
    r"seizure frequency|seizure[- ]free|BCVA|best[- ]corrected visual acuity|ETDRS letters|"
    r"WOMAC|NRS pain|pain intensity|VAS pain|IBS[- ]SSS|stool frequency|Bristol|"
    r"exacerbation rate|annuali[sz]ed (?:exacerbation|relapse|bleed)|ABR\b|hospitali[sz]ation|"
    r"major adverse cardiovascular|MACE|mortality|death|"
    # physiologic measures with a clinical target
    r"(?:systolic|diastolic)? ?blood pressure|SBP|DBP|HbA1c|A1c|eGFR|visual acuity|functional vision|"
    r"body weight|weight loss|lesion counts?|MG[- ]ADL|QMG|sleep latency|Maintenance of Wakefulness|MWT|"
    r"disease control rate|DCR|catheter[- ]free days|"
    # event rates and incidence
    r"exacerbations?|hypoglyc[a]?emi\w*|relapses?|hospitali[sz]ations?|seizures?|flares?|"
    r"rate of infections?|infection rate|remission|response rate|responders?|survival|progression|"
    r"success|almost clear|clear(?![- ]cell)|recurrence)", re.I)
SAFETY = re.compile(
    r"\b(?:TEAEs?|TEAs?|SAEs?|adverse events?|adverse reactions?|AEs?|side effects?|toxicit(?:y|ies)|"
    r"dose[- ]limiting|DLTs?|tolerability|safety|discontinu\w* due to|laboratory (?:abnormalit|value|test|parameter)|"
    r"clinical laboratory|lab abnormalit|vital signs?|ECGs?|electrocardiogram|QTc?|maximum tolerated dose|MTD|"
    r"dose escalation|dose expansion|RP2D|recommended phase 2 dose|clinical chemistry|hematology parameters?|"
    r"infusion[- ](?:associated|related) reactions?|IARs?|injection[- ]site reactions?)\b",
    re.I)
PK_PD = re.compile(
    r"\b(?:pharmacokinetic\w*|PK\b|AUC\w*|Cmax|Cmin|Ctrough|Tmax|half[- ]life|t1/2|clearance|"
    r"volume of distribution|bioavailability|(?:plasma|serum|CSF|blood)[- ]concentrations?|exposure|"
    r"area under the [\w\s-]{0,40}?curve|concentration[- ]time|"
    r"receptor occupancy|target engagement|mass balance|excretion|pharmacodynamic\w*|PD\b)", re.I)
BIOMARKER = re.compile(
    r"\b(?:PET|SUVR|MRI|imaging|volume|volumetric|area\b|lesions?|CSF|cerebrospinal|plasma (?:level|biomarker)|"
    r"biomarkers?|amyloid|tau|p-?tau|neurofilament|NfL|EEG|synaptic density|HbA1c|LDL|cholesterol|"
    r"proteinuria|UPCR|eGFR|viral load|HBsAg|HBV DNA|HIV[- ]1 RNA|ctDNA|expression|"
    r"collagen|histolog\w*|biopsy|filtration slit|fibrosis stage|liver fat|PDFF|ferritin|hemoglobin|"
    r"platelet count|enzyme activity|substrate|Gb3|dystrophin|gene expression|"
    # imaging tracer activity and immune readouts
    r"TSPO|amyloid PET|tracer|radioligand|antibody (?:level|titer|titre)s?|titers?|titres?|"
    r"seroconversion|immunogenicity)\b|as measured by \[?(?:\d{1,3}\s?)?\[?(?:F|C|Ga|Zr|Cu)[- ]?\d{2}", re.I)
GENERIC_CLINICAL = re.compile(
    r"\b(?:survival|response rate|responders?|remission|relapse|recurrence|progression|exacerbations?|"
    r"symptom score|symptoms?|score|scale|questionnaire|quality of life|function(?:al)? (?:score|status)|"
    r"frequency of|days with|time to|healing|clearance of (?:lesions|skin)|cure)\b", re.I)


def classify_outcome(measure: str | None, description: str | None = None) -> str:
    for text in (measure or "", description or ""):
        if not text.strip():
            continue
        if NAMED_CLINICAL.search(text):
            return "clinical"
        if SAFETY.search(text):
            return "safety"
        if PK_PD.search(text):
            return "pk_pd"
        if BIOMARKER.search(text):
            return "biomarker"
        if GENERIC_CLINICAL.search(text):
            return "clinical"
    return "unclassified"


def classify_primary(ps: dict) -> dict:
    """Facts for one registration, from primaryOutcomes[] only."""
    prim = ((ps or {}).get("outcomesModule") or {}).get("primaryOutcomes") or []
    classes = [classify_outcome(p.get("measure"), p.get("description")) for p in prim]
    return {
        "primary_outcomes_n": len(prim),
        "primary_outcome_classes": classes,
        "primary_outcome_is_clinical": "clinical" in classes,
        "primary_is_safety_only": bool(classes) and all(c in ("safety", "pk_pd") for c in classes),
        "primary_outcome_text": [p.get("measure") for p in prim],
        "_unclassified_text": [f"{p.get('measure')} | {p.get('description') or ''}".strip(" |")
                               for p, c in zip(prim, classes) if c == "unclassified"],
    }
